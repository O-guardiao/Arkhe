"""
Plugin WhatsApp — rlm/plugins/whatsapp.py

Integração com WhatsApp via Meta Cloud API (Business API oficial).

Arquitetura RLM-nativa:
    - Outbound via Meta Graph API (POST JSON, stdlib urllib)
    - WhatsAppAdapter registrado no ChannelRegistry como prefixo "whatsapp"
    - Inbound: rlm/server/whatsapp_gateway.py recebe webhooks Meta

Variáveis de ambiente:
    WHATSAPP_PHONE_ID     — ID do número de telefone comercial (ex: 123456789)
    WHATSAPP_TOKEN        — Token de acesso permanente ou temporário da Meta
    WHATSAPP_VERIFY_TOKEN — Token de verificação do webhook (criado por você)

Uso no REPL:
    >>> from rlm.plugins.whatsapp import send_text, send_template
    >>> send_text("+5511999990000", "Pedido #4521 confirmado!")
    '✓ mensagem enviada'
    >>> send_template("+5511999990000", "hello_world", "pt_BR")
    '✓ template enviado'
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    from rlm.plugins import PluginManifest
except ImportError:
    from dataclasses import dataclass as _dc

    @_dc
    class PluginManifest:  # type: ignore
        name: str = ""
        version: str = ""
        description: str = ""
        functions: list = field(default_factory=list)
        author: str = ""
        requires: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Plugin Manifest
# ---------------------------------------------------------------------------

MANIFEST = PluginManifest(
    name="whatsapp",
    version="1.0.0",
    description="WhatsApp Business — Meta Cloud API (texto, templates, mídia, status de leitura).",
    functions=[
        "send_text",
        "send_template",
        "send_image",
        "send_document",
        "send_audio",
        "send_reaction",
        "mark_as_read",
        "get_media_url",
    ],
    author="RLM Engine",
    requires=[],  # stdlib urllib apenas
)

# URL base da Meta Graph API — versão estável
_META_API_BASE = "https://graph.facebook.com/v19.0"


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _get_phone_id() -> str:
    pid = os.environ.get("WHATSAPP_PHONE_ID", "")
    if not pid:
        raise ValueError("WHATSAPP_PHONE_ID não configurado no .env")
    return pid


def _get_token() -> str:
    tok = os.environ.get("WHATSAPP_TOKEN", "")
    if not tok:
        raise ValueError("WHATSAPP_TOKEN não configurado no .env")
    return tok


def _wa_request(method: str, path: str, payload: dict | None = None) -> dict:
    """Realiza chamada à Meta Graph API com autenticação Bearer."""
    token = _get_token()
    url = f"{_META_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib_request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {"ok": True}
    except urllib_error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}: {body[:400]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _send_message(phone_id: str, payload: dict) -> dict:
    """Envia um objeto de mensagem via /messages endpoint."""
    return _wa_request("POST", f"/{phone_id}/messages", payload)


def _normalize_to(to: str) -> str:
    """Remove espaços e normaliza número de telefone (ex: +55 11 9... → 5511...)."""
    return to.replace(" ", "").replace("-", "").replace("+", "").strip()


# ---------------------------------------------------------------------------
# Funções REPL
# ---------------------------------------------------------------------------

def send_text(to: str, text: str, preview_url: bool = False) -> str:
    """
    Envia mensagem de texto simples para um número WhatsApp.

    Args:
        to: Número no formato internacional (ex: '+5511999990000' ou '5511999990000').
        text: Corpo da mensagem (até 4096 chars, suporta emojis e newlines).
        preview_url: True para gerar preview de links embutidos.

    Returns:
        '✓ mensagem enviada' em sucesso, string de erro em falha.
    """
    phone_id = _get_phone_id()
    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(to),
        "type": "text",
        "text": {"body": text[:4096], "preview_url": preview_url},
    }
    result = _send_message(phone_id, payload)
    if result.get("ok") is False or "messages" not in result:
        err = result.get("error") or result.get("error", {})
        return f"Erro WhatsApp text: {err}"
    return "✓ mensagem enviada"


def send_template(
    to: str,
    template_name: str,
    language_code: str = "pt_BR",
    components: list[dict] | None = None,
) -> str:
    """
    Envia uma mensagem de template HSM aprovado pela Meta.

    Templates são obrigatórios para iniciar conversas pro-ativas (fora da janela de 24h).

    Args:
        to: Número do destinatário.
        template_name: Nome do template aprovado (ex: 'hello_world', 'order_confirmation').
        language_code: Código do idioma do template (ex: 'pt_BR', 'en_US').
        components: Lista de componentes com variáveis:
            [{"type": "body", "parameters": [{"type": "text", "text": "João"}]}]

    Returns:
        '✓ template enviado' em sucesso, string de erro em falha.
    """
    phone_id = _get_phone_id()
    template_obj: dict = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template_obj["components"] = components

    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(to),
        "type": "template",
        "template": template_obj,
    }
    result = _send_message(phone_id, payload)
    if result.get("ok") is False or "messages" not in result:
        return f"Erro WhatsApp template: {result.get('error', 'desconhecido')}"
    return "✓ template enviado"


def send_image(to: str, image_url: str, caption: str = "") -> str:
    """
    Envia uma imagem por URL para um número WhatsApp.

    Args:
        to: Número do destinatário.
        image_url: URL pública da imagem (JPEG, PNG, WebP — max 5 MB).
        caption: Legenda opcional (até 1024 chars).

    Returns:
        '✓ imagem enviada' em sucesso, string de erro em falha.
    """
    phone_id = _get_phone_id()
    image_obj: dict = {"link": image_url}
    if caption:
        image_obj["caption"] = caption[:1024]

    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(to),
        "type": "image",
        "image": image_obj,
    }
    result = _send_message(phone_id, payload)
    if result.get("ok") is False or "messages" not in result:
        return f"Erro WhatsApp image: {result.get('error', 'desconhecido')}"
    return "✓ imagem enviada"


def send_document(to: str, document_url: str, caption: str = "", filename: str = "") -> str:
    """
    Envia um documento (PDF, DOCX, etc.) por URL para um número WhatsApp.

    Args:
        to: Número do destinatário.
        document_url: URL pública do arquivo (max 100 MB).
        caption: Legenda opcional.
        filename: Nome do arquivo exibido ao destinatário.

    Returns:
        '✓ documento enviado' em sucesso, string de erro em falha.
    """
    phone_id = _get_phone_id()
    doc_obj: dict = {"link": document_url}
    if caption:
        doc_obj["caption"] = caption[:1024]
    if filename:
        doc_obj["filename"] = filename

    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(to),
        "type": "document",
        "document": doc_obj,
    }
    result = _send_message(phone_id, payload)
    if result.get("ok") is False or "messages" not in result:
        return f"Erro WhatsApp document: {result.get('error', 'desconhecido')}"
    return "✓ documento enviado"


def send_audio(to: str, audio_url: str) -> str:
    """
    Envia um arquivo de áudio por URL para um número WhatsApp.

    Args:
        to: Número do destinatário.
        audio_url: URL pública do áudio (MP3, OGG Opus — max 16 MB).

    Returns:
        '✓ áudio enviado' em sucesso, string de erro em falha.
    """
    phone_id = _get_phone_id()
    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(to),
        "type": "audio",
        "audio": {"link": audio_url},
    }
    result = _send_message(phone_id, payload)
    if result.get("ok") is False or "messages" not in result:
        return f"Erro WhatsApp audio: {result.get('error', 'desconhecido')}"
    return "✓ áudio enviado"


def send_reaction(to: str, message_id: str, emoji: str) -> str:
    """
    Envia uma reação (emoji) a uma mensagem recebida no WhatsApp.

    Args:
        to: Número do remetente da mensagem original.
        message_id: ID da mensagem a reagir (do payload do webhook).
        emoji: Emoji Unicode (ex: '👍', '✅', '❤️').

    Returns:
        '✓ reação enviada' ou string de erro.
    """
    phone_id = _get_phone_id()
    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_to(to),
        "type": "reaction",
        "reaction": {"message_id": message_id, "emoji": emoji},
    }
    result = _send_message(phone_id, payload)
    if result.get("ok") is False or "messages" not in result:
        return f"Erro WhatsApp reaction: {result.get('error', 'desconhecido')}"
    return "✓ reação enviada"


def mark_as_read(message_id: str) -> str:
    """
    Marca uma mensagem recebida como lida (exibe os dois ticks azuis).

    Deve ser chamado imediatamente ao processar uma mensagem inbound.

    Args:
        message_id: ID da mensagem (campo 'id' no payload do webhook).

    Returns:
        '✓ marcado como lido' ou string de erro.
    """
    phone_id = _get_phone_id()
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    result = _wa_request("POST", f"/{phone_id}/messages", payload)
    if result.get("ok") is False:
        return f"Erro mark_as_read: {result.get('error', 'desconhecido')}"
    return "✓ marcado como lido"


def get_media_url(media_id: str) -> str:
    """
    Obtém a URL temporária (válida por 5 min) de uma mídia recebida no webhook.

    Usado para baixar imagens, documentos ou áudios recebidos de usuários.

    Args:
        media_id: ID da mídia do payload inbound (ex: 'wamid.ABC...').

    Returns:
        URL temporária ou string de erro.
    """
    result = _wa_request("GET", f"/{media_id}")
    if result.get("ok") is False or "url" not in result:
        return f"Erro get_media_url: {result.get('error', 'desconhecido')}"
    return result["url"]


# ---------------------------------------------------------------------------
# Channel Registry Hook (WhatsAppAdapter)
# ---------------------------------------------------------------------------

try:
    from rlm.plugins.channel_registry import ChannelAdapter, ChannelRegistry

    class WhatsAppAdapter(ChannelAdapter):
        """
        Adapter WhatsApp para o ChannelRegistry do RLM.

        target_id = número de telefone normalizado (ex: '5511999990000').

        Uso pelo REPL via ChannelRegistry:
            ChannelRegistry.reply("whatsapp:5511999990000", "Pedido confirmado!")
        """

        def send_message(self, target_id: str, text: str) -> bool:
            res = send_text(target_id, text)
            return res.startswith("✓")

        def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
            """
            Envia mídia detectando o tipo pela extensão ou URL.

            - Imagem: .jpg, .png, .webp → send_image
            - Áudio:  .mp3, .ogg       → send_audio
            - Demais                   → send_document
            """
            raw = media_url_or_path.split("?")[0].split("#")[0].lower()
            ext = os.path.splitext(raw)[-1]

            _IMAGE = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})
            _AUDIO = frozenset({".mp3", ".ogg", ".aac", ".m4a", ".opus", ".wav"})

            if ext in _IMAGE:
                res = send_image(target_id, media_url_or_path, caption)
            elif ext in _AUDIO:
                res = send_audio(target_id, media_url_or_path)
            else:
                res = send_document(target_id, media_url_or_path, caption)

            return res.startswith("✓")

    # Auto-registro quando as variáveis obrigatórias estão presentes
    if os.environ.get("WHATSAPP_TOKEN") and os.environ.get("WHATSAPP_PHONE_ID"):
        ChannelRegistry.register("whatsapp", WhatsAppAdapter())

except ImportError:
    pass  # Permite uso standalone sem o registry
