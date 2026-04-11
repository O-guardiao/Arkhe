from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rlm.daemon.contracts import ChannelEvent

if TYPE_CHECKING:
    from rlm.daemon.recursion_daemon import RecursionDaemon


_CHANNEL_ALIASES = {
    "api": "api",
    "http": "api",
    "https": "api",
    "webhook": "api",
    "web": "webchat",
    "webchat": "webchat",
}


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _client_channel_prefix(client_id: str) -> str:
    if ":" not in client_id:
        return ""
    return str(client_id.partition(":")[0] or "").strip().lower()


def _client_channel_suffix(client_id: str) -> str:
    if ":" not in client_id:
        return ""
    return str(client_id.partition(":")[2] or "").strip()


def _canonical_channel_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    return _CHANNEL_ALIASES.get(normalized, normalized)


def _normalize_channel_name(client_id: str, source_name: str) -> str:
    channel = _canonical_channel_name(_client_channel_prefix(client_id))
    if channel:
        return channel
    normalized_source = _canonical_channel_name(source_name)
    return normalized_source or "runtime"


@dataclass(slots=True)
class ChannelSubAgent:
    daemon: "RecursionDaemon"
    channel: str
    source_name: str = "runtime"
    agent_name: str = "channel"

    def attach_session(self, session: Any | None) -> None:
        if session is None:
            return
        metadata = dict(getattr(session, "metadata", {}) or {})
        active_channels = {
            str(item).strip().lower()
            for item in metadata.get("_active_channels", [])
            if str(item).strip()
        }
        active_channels.add(self.channel)
        metadata["_active_channels"] = sorted(active_channels)
        session.metadata = metadata
        self.daemon.attach_channel(
            channel=self.channel,
            client_id=str(getattr(session, "client_id", "") or ""),
            session_id=str(getattr(session, "session_id", "") or ""),
        )

    def build_event(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> ChannelEvent:
        normalized_payload = self.normalize_payload(
            client_id=client_id,
            payload=payload,
            session=session,
        )
        channel_meta = self.build_channel_meta(
            client_id=client_id,
            payload=normalized_payload,
            session=session,
        )
        normalized_payload["channel_meta"] = channel_meta
        return self.daemon.build_event(
            client_id=client_id,
            payload=normalized_payload,
            session=session,
        )

    def normalize_payload(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        del client_id, session
        normalized_payload = dict(payload)
        normalized_payload.setdefault("channel", self.channel)
        if not normalized_payload.get("content_type") and normalized_payload.get("type"):
            normalized_payload["content_type"] = normalized_payload.get("type")
        return normalized_payload

    def build_channel_meta(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        del client_id, session
        channel_meta = dict(payload.get("channel_meta") or {})
        raw_metadata = payload.get("metadata")
        if isinstance(raw_metadata, dict):
            for key, value in raw_metadata.items():
                channel_meta.setdefault(str(key), value)
        channel_meta.setdefault("channel", self.channel)
        channel_meta.setdefault("source_name", self.source_name)
        channel_meta.setdefault("channel_agent", self.agent_name)
        if self.source_name != self.channel:
            channel_meta.setdefault("ingress_source", self.source_name)
        return channel_meta


class TuiSubAgent(ChannelSubAgent):
    def build_channel_meta(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        channel_meta = super().build_channel_meta(client_id=client_id, payload=payload, session=session)
        channel_meta.setdefault("interactive", True)
        channel_meta.setdefault("local_client", True)
        return channel_meta


class TelegramSubAgent(ChannelSubAgent):
    def normalize_payload(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        normalized_payload = super().normalize_payload(client_id=client_id, payload=payload, session=session)
        normalized_payload.setdefault(
            "thread_id",
            _first_non_empty(
                normalized_payload.get("thread_id"),
                normalized_payload.get("message_thread_id"),
            ),
        )
        normalized_payload.setdefault(
            "from_user",
            _first_non_empty(
                normalized_payload.get("from_user"),
                normalized_payload.get("username"),
                normalized_payload.get("user_id"),
            ),
        )
        return normalized_payload

    def build_channel_meta(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        channel_meta = super().build_channel_meta(client_id=client_id, payload=payload, session=session)
        chat_id = _first_non_empty(
            payload.get("chat_id"),
            channel_meta.get("chat_id"),
            _client_channel_suffix(client_id),
        )
        if chat_id:
            channel_meta.setdefault("chat_id", chat_id)
            channel_meta.setdefault("replyable", True)

        message_id = _first_non_empty(payload.get("message_id"), channel_meta.get("message_id"))
        if message_id:
            channel_meta.setdefault("message_id", message_id)

        update_id = _first_non_empty(payload.get("update_id"), channel_meta.get("update_id"))
        if update_id:
            channel_meta.setdefault("update_id", update_id)

        thread_id = _first_non_empty(
            payload.get("thread_id"),
            payload.get("message_thread_id"),
            channel_meta.get("thread_id"),
        )
        if thread_id:
            channel_meta.setdefault("thread_id", thread_id)

        username = _first_non_empty(payload.get("from_user"), payload.get("username"), channel_meta.get("username"))
        if username:
            channel_meta.setdefault("username", username)

        content_type = _first_non_empty(payload.get("content_type"), payload.get("type"), channel_meta.get("content_type"))
        if content_type:
            channel_meta.setdefault("content_type", content_type)
        return channel_meta


class WebChatSubAgent(ChannelSubAgent):
    def normalize_payload(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        normalized_payload = super().normalize_payload(client_id=client_id, payload=payload, session=session)
        normalized_payload.setdefault(
            "thread_id",
            _first_non_empty(
                normalized_payload.get("thread_id"),
                normalized_payload.get("session_id"),
                normalized_payload.get("request_id"),
            ),
        )
        if not normalized_payload.get("content_type"):
            normalized_payload["content_type"] = "text"
        return normalized_payload

    def build_channel_meta(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        channel_meta = super().build_channel_meta(client_id=client_id, payload=payload, session=session)
        session_id = _first_non_empty(
            payload.get("session_id"),
            channel_meta.get("session_id"),
            getattr(session, "session_id", "") if session is not None else "",
        )
        if session_id:
            channel_meta.setdefault("session_id", session_id)

        request_id = _first_non_empty(payload.get("request_id"), channel_meta.get("request_id"))
        if request_id:
            channel_meta.setdefault("request_id", request_id)

        client_key = _first_non_empty(channel_meta.get("client_key"), _client_channel_suffix(client_id), client_id)
        if client_key:
            channel_meta.setdefault("client_key", client_key)

        thread_id = _first_non_empty(payload.get("thread_id"), channel_meta.get("thread_id"), session_id, request_id)
        if thread_id:
            channel_meta.setdefault("thread_id", thread_id)

        channel_meta.setdefault("replyable", True)
        return channel_meta


class DiscordSubAgent(ChannelSubAgent):
    """Extrai guild_id, user_id, interaction_id do payload Discord."""

    def normalize_payload(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        normalized_payload = super().normalize_payload(client_id=client_id, payload=payload, session=session)
        if not normalized_payload.get("content_type"):
            normalized_payload["content_type"] = "text"
        return normalized_payload

    def build_channel_meta(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        channel_meta = super().build_channel_meta(client_id=client_id, payload=payload, session=session)

        # discord client_id = "discord:{guild_id}:{user_id}"
        parts = client_id.split(":")
        guild_id = _first_non_empty(
            payload.get("guild_id"),
            channel_meta.get("guild_id"),
            parts[1] if len(parts) > 1 else "",
        )
        if guild_id:
            channel_meta.setdefault("guild_id", guild_id)

        user_id = _first_non_empty(
            payload.get("user_id"),
            channel_meta.get("user_id"),
            parts[2] if len(parts) > 2 else "",
        )
        if user_id:
            channel_meta.setdefault("user_id", user_id)

        interaction_id = _first_non_empty(
            payload.get("interaction_id"),
            channel_meta.get("interaction_id"),
        )
        if interaction_id:
            channel_meta.setdefault("interaction_id", interaction_id)

        if guild_id:
            channel_meta.setdefault("replyable", True)
        return channel_meta


class SlackSubAgent(ChannelSubAgent):
    """Extrai thread_ts, team_id, slack channel do payload Slack."""

    def normalize_payload(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        normalized_payload = super().normalize_payload(client_id=client_id, payload=payload, session=session)
        # thread_ts é o equivalente ao thread_id no Slack
        normalized_payload.setdefault(
            "thread_id",
            _first_non_empty(
                normalized_payload.get("thread_id"),
                normalized_payload.get("thread_ts"),
                normalized_payload.get("ts"),
            ),
        )
        if not normalized_payload.get("content_type"):
            normalized_payload["content_type"] = "text"
        return normalized_payload

    def build_channel_meta(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        channel_meta = super().build_channel_meta(client_id=client_id, payload=payload, session=session)

        thread_ts = _first_non_empty(
            payload.get("thread_ts"),
            payload.get("ts"),
            channel_meta.get("thread_ts"),
        )
        if thread_ts:
            channel_meta.setdefault("thread_ts", thread_ts)
            channel_meta.setdefault("thread_id", thread_ts)

        team_id = _first_non_empty(
            payload.get("team_id"),
            channel_meta.get("team_id"),
        )
        if team_id:
            channel_meta.setdefault("team_id", team_id)

        # "channel" no Slack = ID do canal Slack (não confundir com self.channel)
        slack_channel = _first_non_empty(
            payload.get("channel"),
            payload.get("slack_channel"),
            channel_meta.get("slack_channel"),
        )
        if slack_channel:
            channel_meta.setdefault("slack_channel", slack_channel)

        channel_meta.setdefault("replyable", True)
        return channel_meta


class WhatsAppSubAgent(ChannelSubAgent):
    """Extrai wa_id, message_id do payload WhatsApp."""

    def normalize_payload(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        normalized_payload = super().normalize_payload(client_id=client_id, payload=payload, session=session)
        if not normalized_payload.get("content_type"):
            normalized_payload["content_type"] = "text"
        return normalized_payload

    def build_channel_meta(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        channel_meta = super().build_channel_meta(client_id=client_id, payload=payload, session=session)

        wa_id = _first_non_empty(
            payload.get("wa_id"),
            channel_meta.get("wa_id"),
            _client_channel_suffix(client_id),
        )
        if wa_id:
            channel_meta.setdefault("wa_id", wa_id)
            channel_meta.setdefault("replyable", True)

        message_id = _first_non_empty(
            payload.get("message_id"),
            channel_meta.get("message_id"),
        )
        if message_id:
            channel_meta.setdefault("message_id", message_id)

        return channel_meta


class IoTSubAgent(ChannelSubAgent):
    def normalize_payload(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        normalized_payload = super().normalize_payload(client_id=client_id, payload=payload, session=session)
        if not normalized_payload.get("content_type"):
            normalized_payload["content_type"] = "event"
        return normalized_payload

    def build_channel_meta(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> dict[str, Any]:
        channel_meta = super().build_channel_meta(client_id=client_id, payload=payload, session=session)
        if "anomaly" in payload:
            channel_meta.setdefault("anomaly", bool(payload.get("anomaly")))
        sensor_id = _first_non_empty(
            payload.get("sensor_id"),
            channel_meta.get("sensor_id"),
            _client_channel_suffix(client_id),
        )
        if sensor_id:
            channel_meta.setdefault("sensor_id", sensor_id)
        return channel_meta


def create_channel_subagent(
    daemon: "RecursionDaemon",
    *,
    client_id: str,
    source_name: str = "runtime",
) -> ChannelSubAgent:
    channel = _normalize_channel_name(client_id, source_name)
    if channel == "tui":
        return TuiSubAgent(daemon=daemon, channel=channel, source_name=source_name, agent_name="tui")
    if channel == "telegram":
        return TelegramSubAgent(daemon=daemon, channel=channel, source_name=source_name, agent_name="telegram")
    if channel == "discord":
        return DiscordSubAgent(daemon=daemon, channel=channel, source_name=source_name, agent_name="discord")
    if channel == "slack":
        return SlackSubAgent(daemon=daemon, channel=channel, source_name=source_name, agent_name="slack")
    if channel == "whatsapp":
        return WhatsAppSubAgent(daemon=daemon, channel=channel, source_name=source_name, agent_name="whatsapp")
    if channel == "iot":
        return IoTSubAgent(daemon=daemon, channel=channel, source_name=source_name, agent_name="iot")
    if channel in {"webchat", "api"}:
        return WebChatSubAgent(daemon=daemon, channel=channel, source_name=source_name, agent_name=channel)
    return ChannelSubAgent(daemon=daemon, channel=channel, source_name=source_name, agent_name=channel)


__all__ = [
    "ChannelSubAgent",
    "DiscordSubAgent",
    "IoTSubAgent",
    "SlackSubAgent",
    "TelegramSubAgent",
    "TuiSubAgent",
    "WebChatSubAgent",
    "WhatsAppSubAgent",
    "create_channel_subagent",
]