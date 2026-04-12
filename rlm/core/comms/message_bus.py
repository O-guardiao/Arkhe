"""
MessageBus — Ponto central de ingest, routing e enqueue do multichannel.

Responsabilidades (e SOMENTE estas):
  1. Aceitar envelopes inbound (via InboundMessage ou Envelope direto)
  2. Aplicar RoutingPolicy para decidir destinos outbound
  3. Enfileirar outbound no OutboxStore
  4. Emitir eventos de observabilidade no EventBus

O que NÃO faz (evitar bifurcação com componentes existentes):
  - NÃO resolve sessão → SessionManager continua fazendo isso
  - NÃO executa RLM → pipeline existente continua responsável
  - NÃO entrega mensagens → DeliveryWorker faz isso
  - NÃO substitui ChannelRegistry → DeliveryWorker usa ChannelRegistry

Singleton acessível via ``get_message_bus()`` / ``init_message_bus()``.
"""
from __future__ import annotations

import threading
from typing import Any

from rlm.core.comms.envelope import Direction, Envelope
from rlm.core.comms.outbox import OutboxStore
from rlm.core.comms.routing_policy import RoutingPolicy
from rlm.core.structured_log import get_logger

_log = get_logger("message_bus")

# ── Singleton ─────────────────────────────────────────────────────────────

_bus_instance: MessageBus | None = None
_bus_lock = threading.Lock()


def init_message_bus(
    outbox: OutboxStore,
    routing_policy: RoutingPolicy | None = None,
    event_bus: Any | None = None,
) -> MessageBus:
    """
    Inicializa o singleton do MessageBus. Chamado no lifespan do api.py.
    Idempotente — se já inicializado, retorna a instância existente.
    """
    global _bus_instance
    with _bus_lock:
        if _bus_instance is None:
            _bus_instance = MessageBus(
                outbox=outbox,
                routing_policy=routing_policy or RoutingPolicy(),
                event_bus=event_bus,
            )
            _log.info("MessageBus initialized")
        return _bus_instance


def get_message_bus() -> MessageBus:
    """
    Retorna o singleton. Levanta RuntimeError se não inicializado.
    Usado por closures (cross_channel_send) e skills.
    """
    if _bus_instance is None:
        raise RuntimeError(
            "MessageBus não inicializado. Chame init_message_bus() no lifespan."
        )
    return _bus_instance


class MessageBus:
    """
    Ponto central de roteamento multichannel.

    Fluxo de uso pelo pipeline existente:
        1. Gateway normaliza → InboundMessage (sem mudança)
        2. Pipeline executa RLM → response_text (sem mudança)
        3. Pipeline chama ``bus.route_response(inbound_envelope, response_text, session)``
        4. MessageBus aplica RoutingPolicy → envelopes outbound
        5. Cada envelope outbound é salvo no OutboxStore
        6. DeliveryWorker drena e entrega

    Para cross-channel direto (sem passar pelo pipeline):
        ``bus.enqueue_outbound(envelope)``
    """

    def __init__(
        self,
        outbox: OutboxStore,
        routing_policy: RoutingPolicy,
        event_bus: Any | None = None,
    ) -> None:
        self.outbox = outbox
        self.routing_policy = routing_policy
        self.event_bus = event_bus

    # ── Ingest (InboundMessage → Envelope) ────────────────────────────────

    def ingest(self, inbound_msg: Any) -> Envelope:
        """
        Converte InboundMessage → Envelope para tracking no bus.

        Nota: isso NÃO dispara o pipeline RLM. É apenas normalização
        para que o MessageBus conheça o envelope e possa correlacionar
        a resposta depois.
        """
        if isinstance(inbound_msg, Envelope):
            envelope = inbound_msg
        elif hasattr(inbound_msg, "msg_id") and hasattr(inbound_msg, "channel") and hasattr(inbound_msg, "content_type"):
            # Duck-type check para InboundMessage (gateway layer) — evita import cross-layer.
            envelope = Envelope.from_inbound_message(inbound_msg)
        else:
            raise TypeError(
                f"MessageBus.ingest espera InboundMessage ou Envelope, "
                f"recebeu {type(inbound_msg).__name__}"
            )

        self._emit("bus.ingest", {"envelope_id": envelope.id, "source": envelope.source_client_id})
        return envelope

    # ── Route response (pipeline → outbox) ────────────────────────────────

    def route_response(
        self,
        inbound: Envelope,
        response_text: str,
        session: Any,
        session_id: str | None = None,
    ) -> list[str]:
        """
        Aplica RoutingPolicy ao response e enfileira outbounds no Outbox.

        Args:
            inbound: Envelope da mensagem original recebida.
            response_text: Texto da resposta gerada pelo RLM.
            session: SessionRecord (para metadata de routing).
            session_id: ID da sessão (para referência no outbox).

        Returns:
            Lista de IDs dos envelopes enfileirados.
        """
        outbound_envelopes = self.routing_policy.route(
            inbound, response_text, session,
        )
        ids: list[str] = []
        for env in outbound_envelopes:
            eid = self.outbox.enqueue(env, session_id=session_id)
            ids.append(eid)
            self._emit(
                "bus.routed",
                {
                    "envelope_id": eid,
                    "target": env.delivery_target,
                    "rule": "policy",
                },
            )
        return ids

    # ── Enqueue outbound direto (cross-channel, IoT, skill) ──────────────

    def enqueue_outbound(
        self,
        envelope: Envelope,
        session_id: str | None = None,
    ) -> str:
        """
        Enfileira envelope outbound diretamente no Outbox.
        Usado por ``cross_channel_send()`` e skills de IoT.
        """
        if envelope.direction != Direction.OUTBOUND:
            envelope.direction = Direction.OUTBOUND
        eid = self.outbox.enqueue(envelope, session_id=session_id)
        self._emit(
            "bus.enqueued",
            {"envelope_id": eid, "target": envelope.delivery_target},
        )
        return eid

    # ── Observabilidade ───────────────────────────────────────────────────

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        if self.event_bus is not None:
            try:
                self.event_bus.emit(event_type, data)
            except Exception:
                pass
