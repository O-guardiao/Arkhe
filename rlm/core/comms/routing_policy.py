"""
RoutingPolicy — Chain of responsibility para decisão de destino outbound.

Dado um Envelope inbound e a resposta do agente, produz 1+ Envelope(s)
outbound que o Outbox deve entregar.

Regras em ordem de prioridade (primeira que produz envelopes vence):
  1. AgentDirectiveRule — agente mandou @@route:canal:id@@ explicitamente
  2. BroadcastRule      — sessão tem broadcast_channels configurado
  3. UserPreferenceRule — canal preferido do indivíduo (cross-channel identity)
  4. EchoBackRule       — fallback: responde no canal de origem

Regra 3 consulta em cascata:
  a. CrossChannelIdentityStore.get_preferred() — preferência persistida
     via unificação de identidade cross-channel (mesmo indivíduo em vários canais).
  b. session.metadata["preferred_channel"] — fallback legacy (string
     no formato "canal:user_id").
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
    Redireciona a resposta para o canal preferido do indivíduo.

    Consulta em cascata:
      1. ``CrossChannelIdentityStore.get_preferred()`` — preferência persistida
         via unificação de identidade cross-channel.  Retorna None se o store
         não estiver inicializado (ex: testes de unidade) ou se não houver
         preferência configurada para este usuário.
      2. ``session.metadata["preferred_channel"]`` — fallback legacy.
         Formato: ``"canal:user_id"`` (ex: ``"slack:T01:C02"``).

    Se nenhuma fonte retornar preferência, retorna lista vazia →
    RoutingPolicy avança para EchoBackRule.
    """

    def evaluate(
        self,
        inbound: Envelope,
        response_text: str,
        session: Any,
    ) -> list[Envelope]:
        # ── 1. CrossChannelIdentityStore (fonte canônica) ─────────────────
        from rlm.core.comms.crosschannel_identity import get_crosschannel_identity

        store = get_crosschannel_identity()
        if store is not None:
            pref = store.get_preferred(inbound.source_channel, inbound.source_id)
            if pref and ":" in pref:
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

        # ── 2. Fallback: session.metadata["preferred_channel"] ────────────
        meta = getattr(session, "metadata", None)
        if not meta or not isinstance(meta, dict):
            return []
        pref_meta: str | None = meta.get("preferred_channel")
        if not pref_meta or ":" not in pref_meta:
            return []
        ch, tid = pref_meta.split(":", 1)
        return [
            Envelope(
                correlation_id=inbound.id,
                source_channel="rlm",
                source_id="system",
                source_client_id="rlm:system",
                target_channel=ch,
                target_id=tid,
                target_client_id=pref_meta,
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
