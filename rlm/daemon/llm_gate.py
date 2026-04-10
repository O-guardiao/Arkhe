from __future__ import annotations

from rlm.daemon.contracts import ChannelEvent, DispatchClass


class LLMGate:
    """Classifica eventos do daemon sem acoplar a heurística ao dispatcher.

    Rotas determinísticas expandidas conforme rascunho-daemon-sempre-ativo:
    - status_query (status/help/ping/channels/sessions/history)
    - channel_control (reconnect, ack, sync)
    - cross_channel_forward (forward_to / cross_channel_target em metadata)
    - iot_signal_normal (leitura nominal sem anomalia)
    - control message types (control/heartbeat/system/command)
    """

    _DETERMINISTIC_MESSAGE_TYPES = frozenset({
        "control",
        "heartbeat",
        "system",
        "command",
        "ack",
        "sync",
        "reconnect",
        "channel_control",
        "cross_channel_forward",
    })

    _DETERMINISTIC_COMMANDS = frozenset({
        "help",
        "maintenance",
        "status",
        "ping",
        "/help",
        "/maintenance",
        "/status",
        "/ping",
        "/channels",
        "/sessions",
        "/history",
        "/reconnect",
        "/sync",
    })

    def classify_event(self, event: ChannelEvent) -> DispatchClass:
        text = event.text.strip().lower()
        command = text.split(maxsplit=1)[0] if text else ""

        # Message-type check BEFORE empty-text rejection:
        # ack/sync/reconnect/channel_control may carry no text.
        if event.message_type in self._DETERMINISTIC_MESSAGE_TYPES:
            return "deterministic"

        if not text and not event.attachments:
            return "reject"

        if event.channel == "iot":
            if bool(event.metadata.get("anomaly")):
                return "task_agent_required"
            return "deterministic"

        if command in self._DETERMINISTIC_COMMANDS:
            return "deterministic"

        if event.metadata.get("forward_to") or event.metadata.get("cross_channel_target"):
            return "deterministic"

        return "llm_required"


__all__ = ["LLMGate"]