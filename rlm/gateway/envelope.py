"""
Envelope — Unidade de transferência de mensagem entre Gateway TypeScript e Brain Python.

Espelha exatamente schemas/envelope.v1.json.
É o único objeto que cruza a fronteira TS↔Python via WebSocket.

Razão de existir:
    InboundMessage (message_envelope.py) usa campos legados (channel, client_id, epoch
    float) incompatíveis com o schema JSON v1 que o Gateway TypeScript produz.
    Este módulo fornece:
      - Envelope: dataclass com validação completa contra o schema
      - create_envelope(): factory com UUID e timestamp automáticos
      - inbound_message_to_envelope(): adapter retrocompatível
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

# ---------------------------------------------------------------------------
# Tipos derivados do schema
# ---------------------------------------------------------------------------

SupportedChannel = Literal["telegram", "discord", "slack", "whatsapp", "webchat", "api", "internal"]
MessageDirection = Literal["inbound", "outbound", "internal"]
MessageType = Literal[
    "text", "image", "audio", "video", "document",
    "location", "command", "event", "action", "system"
]
MessagePriority = Literal[-1, 0, 1]

# ---------------------------------------------------------------------------
# Conjuntos de valores válidos (para validação sem isinstance de Literal)
# ---------------------------------------------------------------------------

def _empty_metadata() -> dict[str, Any]:
    return {}


@lru_cache(maxsize=1)
def _envelope_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "schemas" / "envelope.v1.json"


@lru_cache(maxsize=1)
def _load_envelope_schema() -> dict[str, Any]:
    schema_text = _envelope_schema_path().read_text(encoding="utf-8")
    loaded = json.loads(schema_text)
    return cast(dict[str, Any], loaded)


@lru_cache(maxsize=1)
def _envelope_validator() -> Any:
    return Draft202012Validator(
        _load_envelope_schema(),
        format_checker=FormatChecker(),
    )


def validate_envelope_payload(data: Mapping[str, Any]) -> None:
    try:
        _envelope_validator().validate(dict(data))
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        prefix = f"{path}: " if path else ""
        raise ValueError(f"Envelope inválido pelo schema: {prefix}{exc.message}") from exc

# ---------------------------------------------------------------------------
# Dataclass principal
# ---------------------------------------------------------------------------


@dataclass
class Envelope:
    """
    Envelope canônico — espelha envelope.v1.json.

    Todos os campos obrigatórios são posicionais; opcionais têm defaults
    que espelham os defaults do schema.

    Uso recomendado: use create_envelope() para mensagens novas;
    use Envelope.from_dict() para desserializar JSON vindo de TypeScript.
    """

    # --- Campos obrigatórios (sem default) ---
    id: str                 # UUID hex 32 chars, sem hífens: "a1b2c3d4e5f6a1b2..."
    source_channel: str     # telegram | discord | slack | whatsapp | webchat | api | internal
    source_id: str          # identificador do remetente no canal de origem
    source_client_id: str   # "{channel}:{source_id}"
    direction: str          # inbound | outbound | internal
    text: str               # conteúdo textual (max 65536 chars)
    timestamp: str          # ISO 8601 com timezone: "2026-04-05T10:00:00+00:00"

    # --- Campos opcionais de roteamento ---
    correlation_id: str | None = None
    reply_to_id: str | None = None
    target_channel: str | None = None
    target_id: str | None = None
    target_client_id: str | None = None

    # --- Campos opcionais de conteúdo ---
    message_type: str = "text"
    media_url: str | None = None
    media_mime: str | None = None
    metadata: dict[str, Any] = field(default_factory=_empty_metadata)

    # --- Campos de delivery ---
    delivery_attempts: int = 0
    max_retries: int = 3
    priority: int = 0       # -1 (baixa) | 0 (normal) | 1 (alta)

    def __post_init__(self) -> None:
        """Valida o envelope contra o JSON Schema canônico."""
        validate_envelope_payload(self.to_dict())

    # ---------------------------------------------------------------------------
    # Serialização
    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serializa para dict JSON-compatível (campo nullable mantém None como null)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Envelope":
        """
        Desserializa a partir de dict (vindo de JSON TypeScript).

        Valida o payload bruto contra o JSON Schema canônico antes de instanciar.
        """
        validate_envelope_payload(data)
        return cls(**data)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_envelope(
    source_channel: str,
    source_id: str,
    text: str,
    *,
    direction: str = "inbound",
    message_type: str = "text",
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    reply_to_id: str | None = None,
    target_channel: str | None = None,
    target_id: str | None = None,
    priority: int = 0,
) -> Envelope:
    """
    Cria um Envelope com UUID e timestamp UTC gerados automaticamente.

    Equivalente ao createEnvelope() do TypeScript (envelope.ts).

    Args:
        source_channel: Canal de origem (e.g. "telegram").
        source_id: ID do remetente no canal.
        text: Conteúdo textual da mensagem.
        direction: "inbound" | "outbound" | "internal".
        message_type: Tipo semântico (default "text").
        metadata: Dados adicionais específicos do canal.
        correlation_id: ID da conversa pai (rastreio).
        reply_to_id: ID da mensagem à qual esta responde.
        target_channel: Canal de destino (outbound).
        target_id: ID do destinatário (outbound).
        priority: -1 | 0 | 1.
    """
    env_id = uuid.uuid4().hex  # 32-char hex sem hífens
    target_client_id = (
        f"{target_channel}:{target_id}"
        if target_channel and target_id
        else None
    )
    return Envelope(
        id=env_id,
        source_channel=source_channel,
        source_id=source_id,
        source_client_id=f"{source_channel}:{source_id}",
        direction=direction,
        text=text,
        timestamp=datetime.now(timezone.utc).isoformat(),
        message_type=message_type,
        metadata=metadata or {},
        correlation_id=correlation_id,
        reply_to_id=reply_to_id,
        target_channel=target_channel,
        target_id=target_id,
        target_client_id=target_client_id,
        priority=priority,
    )


def create_reply_envelope(
    inbound: Envelope,
    reply_text: str,
    *,
    message_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> Envelope:
    """
    Cria um Envelope de resposta a partir de um Envelope inbound.

    Inverte source↔target e marca direction="outbound".
    """
    env_id = uuid.uuid4().hex
    return Envelope(
        id=env_id,
        source_channel="internal",
        source_id="brain",
        source_client_id="internal:brain",
        direction="outbound",
        text=reply_text,
        timestamp=datetime.now(timezone.utc).isoformat(),
        message_type=message_type,
        metadata=metadata or {},
        correlation_id=inbound.id,
        reply_to_id=inbound.id,
        target_channel=inbound.source_channel,
        target_id=inbound.source_id,
        target_client_id=inbound.source_client_id,
        priority=inbound.priority,
    )


# ---------------------------------------------------------------------------
# Adapter retrocompatível: InboundMessage → Envelope
# ---------------------------------------------------------------------------

# Mapa de prefixos legados para canais canônicos
_LEGACY_CHANNEL_MAP: dict[str, str] = {
    "tg": "telegram",
    "telegram": "telegram",
    "discord": "discord",
    "slack": "slack",
    "whatsapp": "whatsapp",
    "webchat": "webchat",
    "webhook": "api",
    "api": "api",
    "internal": "internal",
}

# Mapa de content_type legado para message_type canônico
_CONTENT_TYPE_MAP: dict[str, str] = {
    "text": "text",
    "image": "image",
    "audio": "audio",
    "video": "video",
    "document": "document",
    "location": "location",
    "command": "command",
}


def inbound_message_to_envelope(msg: Any) -> Envelope:
    """
    Converte InboundMessage (formato legado de message_envelope.py) para Envelope.

    Características:
    - Mapeia prefixos legados ("tg" → "telegram", "webhook" → "api")
    - Converte timestamp epoch float → ISO 8601 UTC
    - Preserva channel_meta em metadata
    - Gera ID determinístico quando msg_id presente, aleatório caso contrário
    """
    import hashlib

    # Resolver canal canônico
    raw_channel: str = getattr(msg, "channel", "api")
    # source_client_id pode ter prefixo "tg:123" — extraímos o prefixo para mapear
    client_id: str = getattr(msg, "client_id", "")
    prefix = client_id.split(":", 1)[0] if ":" in client_id else raw_channel
    canonical_channel = _LEGACY_CHANNEL_MAP.get(prefix.lower(), "api")

    # source_id: parte após o primeiro ":" do client_id legado
    parts = client_id.split(":", 1)
    source_id = parts[1] if len(parts) == 2 else client_id

    # Gerar source_client_id canônico
    source_client_id = f"{canonical_channel}:{source_id}"

    # Converter timestamp epoch → ISO 8601
    ts_epoch: float = getattr(msg, "timestamp", 0.0) or 0.0
    if ts_epoch > 0:
        ts_iso = datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat()
    else:
        ts_iso = datetime.now(timezone.utc).isoformat()

    # Gerar ID
    msg_id: str = getattr(msg, "msg_id", "") or ""
    if msg_id:
        raw = f"{canonical_channel}:{source_client_id}:{msg_id}"
        env_id = hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()
    else:
        env_id = uuid.uuid4().hex

    # Mapear message_type
    content_type: str = getattr(msg, "content_type", "text") or "text"
    message_type = _CONTENT_TYPE_MAP.get(content_type, "text")

    # Metadata: preservar channel_meta + from_user + msg_id originais
    channel_meta: dict[str, Any] = dict(getattr(msg, "channel_meta", {}) or {})
    from_user: str = getattr(msg, "from_user", "") or ""
    if from_user:
        channel_meta["from_user"] = from_user
    if msg_id:
        channel_meta["original_msg_id"] = msg_id

    text: str = getattr(msg, "text", "") or ""

    return Envelope(
        id=env_id,
        source_channel=canonical_channel,
        source_id=source_id,
        source_client_id=source_client_id,
        direction="inbound",
        text=text,
        timestamp=ts_iso,
        message_type=message_type,
        metadata=channel_meta,
    )
