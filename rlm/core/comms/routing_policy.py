"""
RoutingPolicy — Chain of responsibility para decisão de destino outbound.

Dado um Envelope inbound e a resposta do agente, produz 1+ Envelope(s)
outbound que o Outbox deve entregar.

Regras em ordem de prioridade (primeira que produz envelopes vence):
  1. AgentDirectiveRule — agente mandou @@route:canal:id@@ explicitamente
  2. BroadcastRule      — sessão tem broadcast_channels configurado
  3. UserPreferenceRule — usuário tem preferred_channel definido
  4. EchoBackRule       — fallback: responde no canal de origem

Fase 1: somente EchoBackRule ativo. As demais regras ficam prontas mas
não serão exercitadas até que os campos de metadata estejam populados
(Phase 3+ quando gateways migrarem).
"""
from __future__ import annotations

import re
from typing import Any, Protocol

from rlm.core.comms.envelope import Direction, Envelope, MessageType


class RoutingRule(Protocol):
    """Contrato para regras de roteamento."""

    def evaluate(
        self,
        inbound: Envelope,
        response_text: str,
        session: Any,
    ) -> list[Envelope]:
        """Retorna envelopes outbound (0 = pass, N = decisão tomada)."""
        ...


# ── Regra 1: Diretiva explícita do agente ────────────────────────────────


class AgentDirectiveRule:
    """
    Se a resposta contém ``@@route:telegram:12345@@``, redireciona.
    Remove a diretiva do texto enviado ao usuário.
    """

    _PATTERN = re.compile(r"@@route:(\w+:\S+)@@\s*")

    def evaluate(
        self,
        inbound: Envelope,
        response_text: str,
        session: Any,
    ) -> list[Envelope]:
        match = self._PATTERN.search(response_text)
        if not match:
            return []
        target = match.group(1)
        clean = self._PATTERN.sub("", response_text).strip()
        ch, tid = target.split(":", 1)
        return [
            Envelope(
                correlation_id=inbound.id,
                source_channel="rlm",
                source_id="system",
                source_client_id="rlm:system",
                target_channel=ch,
                target_id=tid,
                target_client_id=target,
                direction=Direction.OUTBOUND,
                message_type=MessageType.TEXT,
                text=clean,
            )
        ]


# ── Regra 2: Broadcast (alerta multi-canal) ─────────────────────────────


class BroadcastRule:
    """
    Se ``session.metadata["broadcast_channels"]`` existe, envia para todos.
    Útil para alertas IoT: temperatura alta → avisa Telegram + Slack.
    """

    def evaluate(
        self,
        inbound: Envelope,
        response_text: str,
        session: Any,
    ) -> list[Envelope]:
        meta = getattr(session, "metadata", None)
        if not meta or not isinstance(meta, dict):
            return []
        channels: list[str] = meta.get("broadcast_channels", [])
        if not channels:
            return []
        envelopes: list[Envelope] = []
        for client_id in channels:
            if ":" not in client_id:
                continue
            ch, tid = client_id.split(":", 1)
            envelopes.append(
                Envelope(
                    correlation_id=inbound.id,
                    source_channel="rlm",
                    source_id="system",
                    source_client_id="rlm:system",
                    target_channel=ch,
                    target_id=tid,
                    target_client_id=client_id,
                    direction=Direction.OUTBOUND,
                    message_type=MessageType.TEXT,
                    text=response_text,
                )
            )
        return envelopes


# ── Regra 3: Preferência do usuário ──────────────────────────────────────


class UserPreferenceRule:
    """
    Se o usuário configurou ``session.metadata["preferred_channel"]``,
    redireciona para lá. Formato: ``"telegram:12345"``.
    """

    def evaluate(
        self,
        inbound: Envelope,
        response_text: str,
        session: Any,
    ) -> list[Envelope]:
        meta = getattr(session, "metadata", None)
        if not meta or not isinstance(meta, dict):
            return []
        pref: str | None = meta.get("preferred_channel")
        if not pref or ":" not in pref:
            return []
        ch, tid = pref.split(":", 1)
        return [
            Envelope(
                correlation_id=inbound.id,
                source_channel="rlm",
                source_id="system",
                source_client_id="rlm:system",
                target_channel=ch,
                target_id=tid,
                target_client_id=pref,
                direction=Direction.OUTBOUND,
                message_type=MessageType.TEXT,
                text=response_text,
            )
        ]


# ── Regra 4: Echo-back (fallback) ────────────────────────────────────────


class EchoBackRule:
    """Default: responde no mesmo canal que perguntou."""

    def evaluate(
        self,
        inbound: Envelope,
        response_text: str,
        session: Any,
    ) -> list[Envelope]:
        return [inbound.reply(response_text)]


# ── Policy (orquestrador) ────────────────────────────────────────────────


class RoutingPolicy:
    """
    Executa regras em ordem de prioridade.
    Primeira que produz envelopes vence.
    Se nenhuma produz, EchoBack é o fallback incondicional.
    """

    def __init__(self) -> None:
        self.rules: list[RoutingRule] = [
            AgentDirectiveRule(),
            BroadcastRule(),
            UserPreferenceRule(),
            EchoBackRule(),
        ]

    def route(
        self,
        inbound: Envelope,
        response_text: str,
        session: Any,
    ) -> list[Envelope]:
        for rule in self.rules:
            envelopes = rule.evaluate(inbound, response_text, session)
            if envelopes:
                return envelopes
        # Defensivo — EchoBack sempre retorna, mas por segurança:
        return [inbound.reply(response_text)]
