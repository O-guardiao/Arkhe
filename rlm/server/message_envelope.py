"""
RLM Message Envelope — Formato canônico de mensagem entre gateways e agente.

Problema real:
    Cada gateway constrói seu próprio dict adhoc para enviar ao RLM via
    /webhook/{client_id}. WhatsApp envia {"wa_id", "message_id", "type"},
    Slack envia {"channel", "thread_ts", "team_id"}, Telegram nem passa
    pelo webhook (usa SessionManager direto). Sem formato padrão, cada
    skill/plugin que precisa saber "de onde veio" tem que fazer parsing
    diferente.

Solução:
    InboundMessage é o envelope canônico. Cada gateway normaliza sua
    mensagem nativa para esse formato antes de enviar ao RLM. O agente
    e skills trabalham com um contrato único.

Design RLM-nativo:
    - Dataclass imutável (frozen) — evita mutação acidental no pipeline
    - Normalizers são funções puras (sem estado) — fácil de testar
    - to_dict() / from_dict() para serialização JSON sem dependências
    - channel_meta: dict livre para dados específicos do canal
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class InboundMessage:
    """
    Envelope canônico de mensagem inbound — formato único para todos os canais.

    Campos obrigatórios:
        channel:    Nome do canal (telegram, whatsapp, discord, slack, webhook)
        client_id:  Identificador único do remetente no padrão RLM
                    (ex: "whatsapp:5511999...", "slack:T12:C34", "tg:12345")
        text:       Conteúdo textual principal da mensagem

    Campos opcionais:
        msg_id:        ID original da mensagem no canal de origem
        from_user:     Nome/ID do remetente (display ou ID numérico)
        content_type:  Tipo de conteúdo (text, image, audio, document, location, ...)
        timestamp:     Epoch float do recebimento (default: time.time())
        channel_meta:  Dict livre para metadados específicos do canal
    """
    channel: str                                  # "telegram", "whatsapp", "slack", "discord"
    client_id: str                                # "whatsapp:5511...", "tg:12345"
    text: str                                     # conteúdo textual principal

    msg_id: str = ""                              # ID nativo da mensagem
    from_user: str = ""                           # nome ou ID do remetente
    content_type: str = "text"                    # "text", "image", "audio", "location"
    timestamp: float = 0.0                        # epoch — preenchido em __post_init__
    channel_meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            # frozen=True impede atribuição direta, usar object.__setattr__
            object.__setattr__(self, "timestamp", time.time())

    def to_dict(self) -> dict[str, Any]:
        """Serializa para dict JSON-compatível."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InboundMessage:
        """Reconstrói a partir de dict. Ignora campos desconhecidos."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Normalizers — funções puras por canal
# ---------------------------------------------------------------------------

def normalize_telegram(
    chat_id: int,
    text: str,
    username: str = "",
    msg_id: int = 0,
) -> InboundMessage:
    """Normaliza mensagem do Telegram para InboundMessage."""
    return InboundMessage(
        channel="telegram",
        client_id=f"tg:{chat_id}",
        text=text,
        msg_id=str(msg_id) if msg_id else "",
        from_user=username,
        content_type="text",
    )


def normalize_whatsapp(
    message: dict[str, Any],
    contact_map: dict[str, str] | None = None,
) -> InboundMessage | None:
    """
    Normaliza mensagem WhatsApp para InboundMessage.
    Retorna None para tipos não-processáveis (status, reaction).
    """
    msg_type = message.get("type", "")
    wa_id = message.get("from", "")
    msg_id = message.get("id", "")
    contact_map = contact_map or {}
    from_name = contact_map.get(wa_id, wa_id)

    if msg_type in ("status", "reaction"):
        return None

    text = ""
    content_type = msg_type

    if msg_type == "text":
        text = message.get("text", {}).get("body", "")

    elif msg_type in {"image", "audio", "document", "video", "sticker"}:
        media_obj = message.get(msg_type, {})
        media_id = media_obj.get("id", "")
        caption = media_obj.get("caption", "")
        mime = media_obj.get("mime_type", "")
        text = f"[{msg_type.upper()} recebido] media_id={media_id} mime={mime}"
        if caption:
            text += f" caption={caption!r}"

    elif msg_type == "location":
        loc = message.get("location", {})
        text = (
            f"Localização recebida: lat={loc.get('latitude')}, "
            f"lon={loc.get('longitude')}, nome={loc.get('name', '')}"
        )

    elif msg_type == "interactive":
        interactive = message.get("interactive", {})
        itype = interactive.get("type", "")
        if itype == "button_reply":
            btn = interactive.get("button_reply", {})
            text = f"[Botão] {btn.get('title', '')} (id: {btn.get('id', '')})"
        elif itype == "list_reply":
            item = interactive.get("list_reply", {})
            text = f"[Lista] {item.get('title', '')} (id: {item.get('id', '')})"
        else:
            text = f"[interactive:{itype}]"
    else:
        return None

    if not text.strip():
        return None

    return InboundMessage(
        channel="whatsapp",
        client_id=f"whatsapp:{wa_id}",
        text=text,
        msg_id=msg_id,
        from_user=from_name,
        content_type=content_type,
        channel_meta={"wa_id": wa_id},
    )


def normalize_slack(
    event: dict[str, Any],
    team_id: str = "",
) -> InboundMessage | None:
    """
    Normaliza evento Slack para InboundMessage.
    Retorna None para eventos sem texto processável.
    """
    import re

    user_id = event.get("user", "")
    text = event.get("text", "").strip()
    channel = event.get("channel", "")

    # Remove menção @bot
    text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
    if not text:
        return None

    return InboundMessage(
        channel="slack",
        client_id=f"slack:{team_id}:{channel}",
        text=text,
        msg_id=event.get("client_msg_id", ""),
        from_user=user_id,
        content_type="text",
        channel_meta={
            "thread_ts": event.get("thread_ts") or event.get("ts", ""),
            "channel": channel,
            "team_id": team_id,
        },
    )


def normalize_discord(
    interaction_data: dict[str, Any],
) -> InboundMessage | None:
    """
    Normaliza interação Discord para InboundMessage.
    Retorna None se não houver conteúdo processável.
    """
    text = ""
    interaction_type = interaction_data.get("type", "")

    if interaction_type == "command":
        # Slash command: nome + args
        cmd = interaction_data.get("command", "")
        args = interaction_data.get("args", {})
        text = f"/{cmd}"
        if args:
            text += " " + " ".join(f"{k}={v}" for k, v in args.items())
    elif interaction_type == "message":
        text = interaction_data.get("content", "")
    else:
        text = str(interaction_data.get("content", ""))

    if not text.strip():
        return None

    return InboundMessage(
        channel="discord",
        client_id=f"discord:{interaction_data.get('guild_id', 'dm')}:{interaction_data.get('user_id', '')}",
        text=text,
        from_user=interaction_data.get("user_id", ""),
        content_type="text",
        channel_meta={
            "guild_id": interaction_data.get("guild_id", ""),
            "interaction_id": interaction_data.get("interaction_id", ""),
        },
    )
