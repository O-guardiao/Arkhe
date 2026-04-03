"""
Envelope — Unidade atômica do MessageBus multichannel.

Coexiste com ``InboundMessage`` (rlm/server/message_envelope.py) sem duplicação:

- ``InboundMessage``: formato canônico que gateways produzem ao normalizar
  mensagens nativas de cada plataforma. Imutável (frozen), sem campos de
  entrega. É consumido pelo pipeline existente.

- ``Envelope``: formato do MessageBus para **roteamento e entrega**. Contém
  campos de retry, prioridade, direção, correlação. Pode encapsular um
  ``InboundMessage`` ou ser criado diretamente (outbound, cross-channel,
  IoT events).

Fluxo:
    1. Gateway → ``InboundMessage`` (normalização)
    2. ``MessageBus.ingest(InboundMessage)`` → ``Envelope`` (routing)
    3. RLM processa → resposta
    4. ``RoutingPolicy`` → ``Envelope(direction=OUTBOUND)``
    5. Outbox persiste → DeliveryWorker entrega via ChannelRegistry
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from rlm.server.message_envelope import InboundMessage


class MessageType(Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    LOCATION = "location"
    COMMAND = "command"
    EVENT = "event"
    ACTION = "action"
    SYSTEM = "system"


class Direction(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL = "internal"


@dataclass
class Envelope:
    """
    Unidade atômica do MessageBus.
    Toda mensagem — de qualquer canal, em qualquer direção — é um Envelope.
    """

    # ── Identificação ────────────────────────────
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str | None = None
    reply_to_id: str | None = None

    # ── Roteamento ───────────────────────────────
    source_channel: str = ""
    source_id: str = ""
    source_client_id: str = ""

    target_channel: str | None = None
    target_id: str | None = None
    target_client_id: str | None = None

    # ── Conteúdo ─────────────────────────────────
    direction: Direction = Direction.INBOUND
    message_type: MessageType = MessageType.TEXT
    text: str = ""
    media_url: str | None = None
    media_mime: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Temporal ─────────────────────────────────
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # ── Entrega ──────────────────────────────────
    delivery_attempts: int = 0
    max_retries: int = 3
    priority: int = 0  # 0=normal, 1=alta, -1=baixa

    # ── Conveniência ─────────────────────────────

    @property
    def client_id(self) -> str:
        """Formato ChannelRegistry: 'canal:id'."""
        return self.source_client_id or f"{self.source_channel}:{self.source_id}"

    @property
    def delivery_target(self) -> str:
        """client_id de destino (cross-channel ou echo-back)."""
        return self.target_client_id or self.source_client_id

    def reply(self, text: str, **overrides: Any) -> Envelope:
        """Cria envelope de resposta invertendo source↔target."""
        defaults = dict(
            correlation_id=self.id,
            source_channel="rlm",
            source_id="system",
            source_client_id="rlm:system",
            target_channel=self.source_channel,
            target_id=self.source_id,
            target_client_id=self.source_client_id,
            direction=Direction.OUTBOUND,
            message_type=MessageType.TEXT,
            text=text,
            priority=self.priority,
        )
        defaults.update(overrides)
        return Envelope(**defaults)

    def to_dict(self) -> dict[str, Any]:
        """Serializa para dict JSON-compatível."""
        return {
            "id": self.id,
            "correlation_id": self.correlation_id,
            "reply_to_id": self.reply_to_id,
            "source_channel": self.source_channel,
            "source_id": self.source_id,
            "source_client_id": self.source_client_id,
            "target_channel": self.target_channel,
            "target_id": self.target_id,
            "target_client_id": self.target_client_id,
            "direction": self.direction.value,
            "message_type": self.message_type.value,
            "text": self.text,
            "media_url": self.media_url,
            "media_mime": self.media_mime,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
            "delivery_attempts": self.delivery_attempts,
            "max_retries": self.max_retries,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Envelope:
        """Reconstrói a partir de dict. Ignora campos desconhecidos."""
        d = dict(data)
        if "direction" in d and isinstance(d["direction"], str):
            d["direction"] = Direction(d["direction"])
        if "message_type" in d and isinstance(d["message_type"], str):
            d["message_type"] = MessageType(d["message_type"])
        if "timestamp" in d and isinstance(d["timestamp"], str):
            d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        known = {f.name for f in Envelope.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def from_inbound_message(cls, msg: InboundMessage) -> Envelope:
        """
        Converte InboundMessage (gateway normalisation) → Envelope (bus routing).

        Preserva todos os campos mapeáveis e adiciona os campos de routing
        que o InboundMessage não possui.
        """
        content_map = {
            "text": MessageType.TEXT,
            "image": MessageType.IMAGE,
            "audio": MessageType.AUDIO,
            "video": MessageType.VIDEO,
            "document": MessageType.DOCUMENT,
            "location": MessageType.LOCATION,
        }
        return cls(
            correlation_id=msg.msg_id or None,
            source_channel=msg.channel,
            source_id=msg.client_id.split(":", 1)[1] if ":" in msg.client_id else msg.client_id,
            source_client_id=msg.client_id,
            direction=Direction.INBOUND,
            message_type=content_map.get(msg.content_type, MessageType.TEXT),
            text=msg.text,
            metadata={
                "from_user": msg.from_user,
                **msg.channel_meta,
            },
            timestamp=datetime.fromtimestamp(msg.timestamp, tz=timezone.utc),
        )
