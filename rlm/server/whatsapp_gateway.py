"""
WhatsApp Gateway — rlm/server/whatsapp_gateway.py

Receptor de webhooks da Meta Cloud API para o RLM.

Arquitetura RLM-nativa:
    Mensagem WhatsApp
        → Meta envia POST /whatsapp/webhook
        → Extração do número e texto da mensagem
        → Roteamento para POST /webhook/whatsapp:{wa_id}
        → EventRouter "whatsapp:*" → agente RLM processa
        → ChannelRegistry.reply("whatsapp:{wa_id}", resposta)

Setup Meta (única vez):
    1. Meta for Developers → seu app → WhatsApp → Configuração
    2. URL do callback: https://seu-dominio.com/whatsapp/webhook
    3. Token de verificação: valor de WHATSAPP_VERIFY_TOKEN no .env
    4. Se inscreva nos eventos: messages

Variáveis de ambiente:
    WHATSAPP_PHONE_ID      — ID do número comercial
    WHATSAPP_TOKEN         — Token de acesso Meta
    WHATSAPP_VERIFY_TOKEN  — Token de verificação do webhook (você define)

Tipos de mensagem suportados:
    - text        → encaminha o texto ao RLM
    - image/audio/document → encaminha a URL da mídia + tipo
    - reaction    → loga, não encaminha ao RLM
    - status      → loga status de entrega, não encaminha
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.background import BackgroundTask

from rlm.logging import get_runtime_logger
from rlm.server.auth_helpers import build_internal_auth_headers
from rlm.server.dedup import MessageDedup

log = get_runtime_logger("whatsapp_gateway")

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

# Dedup global — Meta re-entrega webhooks se 200 demora
_whatsapp_dedup = MessageDedup(ttl_s=300.0, max_entries=10_000)


# ---------------------------------------------------------------------------
# Verificação do webhook Meta (GET — validação única)
# ---------------------------------------------------------------------------

@router.get("/webhook")
async def whatsapp_verify(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> PlainTextResponse:
    """
    Valida o webhook com a Meta durante o setup inicial.

    A Meta envia uma requisição GET com:
    - hub.mode = "subscribe"
    - hub.verify_token = valor configurado em WHATSAPP_VERIFY_TOKEN
    - hub.challenge = string aleatória que devemos retornar

    Retorna o hub.challenge em texto puro se verificação for bem-sucedida.
    """
    expected_token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if not expected_token:
        log.error("WHATSAPP_VERIFY_TOKEN não configurado.")
        raise HTTPException(status_code=500, detail="Gateway não configurado.")

    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        log.info("WhatsApp webhook verificado com sucesso.")
        return PlainTextResponse(content=hub_challenge)

    log.warn(
        "Falha na verificação WhatsApp",
        hub_mode=hub_mode,
        hub_verify_token=hub_verify_token,
    )
    raise HTTPException(status_code=403, detail="Verification failed")


# ---------------------------------------------------------------------------
# Processamento de mensagens inbound (POST)
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def whatsapp_inbound(request: Request) -> JSONResponse:
    """
    Recebe eventos de mensagem da Meta Cloud API.

    A Meta espera HTTP 200 em até 20 segundos. O processamento do RLM
    acontece de forma assíncrona — retornamos 200 imediatamente.

    Estrutura do payload Meta:
    {
      "entry": [{
        "changes": [{
          "value": {
            "messages": [{"from": "5511...", "id": "wamid...", "type": "text",
                          "text": {"body": "olá"}}],
            "metadata": {"phone_number_id": "123..."}
          }
        }]
      }]
    }
    """
    body = await request.body()

    # Verifica assinatura HMAC-SHA256 da Meta (se WHATSAPP_APP_SECRET configurado)
    app_secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    if app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not signature.startswith("sha256="):
            log.warn("WhatsApp webhook sem assinatura válida — rejeitando")
            raise HTTPException(status_code=403, detail="Assinatura ausente")
        expected = "sha256=" + hmac.new(
            app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            log.warn("WhatsApp webhook com assinatura inválida — rejeitando")
            raise HTTPException(status_code=403, detail="Assinatura inválida")

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON inválido")

    # Aceita imediatamente — Meta exige resposta rápida
    return JSONResponse(
        content={"status": "accepted"},
        background=BackgroundTask(_process_whatsapp_payload, request, payload),
    )


async def _process_whatsapp_payload(request: Request, payload: dict) -> None:
    """
    Processa cada mensagem recebida no payload da Meta.

    Encaminha para POST /webhook/whatsapp:{wa_id} (endpoint interno do RLM).
    """
    try:
        entries = payload.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                metadata = value.get("metadata", {})
                contacts = value.get("contacts", [])

                # Mapa de wa_id → nome do contato (opcional)
                contact_map: dict[str, str] = {}
                for contact in contacts:
                    wa_id = contact.get("wa_id", "")
                    name = contact.get("profile", {}).get("name", wa_id)
                    contact_map[wa_id] = name

                for message in messages:
                    await _handle_message(request, message, metadata, contact_map)

    except Exception as exc:
        log.error("Erro ao processar payload WhatsApp", error=str(exc))


async def _handle_message(
    request: Request,
    message: dict,
    metadata: dict,
    contact_map: dict[str, str],
) -> None:
    """
    Processa uma única mensagem WhatsApp e a encaminha ao agente RLM.

    Suporta: text, image, audio, document, video, sticker.
    Statuses e reactions são logados e descartados (não enviam ao RLM).
    """
    msg_type = message.get("type", "")
    wa_id = message.get("from", "")
    msg_id = message.get("id", "")
    from_name = contact_map.get(wa_id, wa_id)

    # Dedup — Meta pode re-entregar o mesmo webhook
    if msg_id and _whatsapp_dedup.is_duplicate(msg_id):
        log.debug("WhatsApp mensagem duplicada descartada", msg_id=msg_id)
        return

    # Statuses (entregue, lido) — não são mensagens, apenas log
    if msg_type == "status":
        log.debug("WhatsApp status update ignorado", wa_id=wa_id)
        return

    # Reaction — log, não roteia ao agente
    if msg_type == "reaction":
        log.debug("WhatsApp reaction ignorada", wa_id=wa_id, reaction=json.dumps(message.get("reaction", {}), ensure_ascii=False))
        return

    # Extrai conteúdo principal conforme o tipo
    rlm_text = ""

    if msg_type == "text":
        rlm_text = message.get("text", {}).get("body", "")

    elif msg_type in {"image", "audio", "document", "video", "sticker"}:
        media_obj = message.get(msg_type, {})
        media_id = media_obj.get("id", "")
        caption = media_obj.get("caption", "")
        mime = media_obj.get("mime_type", "")
        rlm_text = (
            f"[{msg_type.upper()} recebido] media_id={media_id} mime={mime}"
        )
        if caption:
            rlm_text += f" caption={caption!r}"
        rlm_text += (
            "\n\nUse get_media_url(media_id) para obter a URL temporária do arquivo "
            "e processá-lo se necessário."
        )

    elif msg_type == "location":
        loc = message.get("location", {})
        rlm_text = (
            f"Localização recebida: lat={loc.get('latitude')}, "
            f"lon={loc.get('longitude')}, nome={loc.get('name', '')}"
        )

    elif msg_type == "interactive":
        interactive = message.get("interactive", {})
        itype = interactive.get("type", "")
        if itype == "button_reply":
            btn = interactive.get("button_reply", {})
            rlm_text = f"[Botão] {btn.get('title', '')} (id: {btn.get('id', '')})"
        elif itype == "list_reply":
            item = interactive.get("list_reply", {})
            rlm_text = f"[Lista] {item.get('title', '')} (id: {item.get('id', '')})"
        else:
            rlm_text = f"[interactive:{itype}]"

    else:
        log.warn("Tipo de mensagem WhatsApp desconhecido", message_type=msg_type)
        return

    if not rlm_text.strip():
        return

    # Marca como lida de forma assíncrona (sem bloquear o processamento)
    if msg_id:
        import asyncio
        asyncio.ensure_future(_mark_read_async(msg_id))

    # Monta client_id e payload para o RLM
    client_id = f"whatsapp:{wa_id}"
    rlm_payload = {
        "from_user": from_name,
        "wa_id": wa_id,
        "message_id": msg_id,
        "type": msg_type,
        "text": rlm_text,
        "channel": "whatsapp",
    }

    await _dispatch_to_rlm(client_id, rlm_payload)


async def _dispatch_to_rlm(client_id: str, payload: dict) -> None:
    """Encaminha evento ao endpoint /webhook/{client_id} do RLM (HTTP local)."""
    import urllib.error as uerror
    import urllib.request as urequest

    rlm_host = os.environ.get("RLM_INTERNAL_HOST", "http://127.0.0.1:5000")
    url = f"{rlm_host}/webhook/{client_id}"

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urequest.Request(
        url,
        data=data,
        headers=build_internal_auth_headers(),
        method="POST",
    )
    try:
        with urequest.urlopen(req, timeout=115):
            pass  # resultado entregue por ChannelRegistry.reply() de forma assíncrona
    except uerror.HTTPError as e:
        log.error("WhatsApp→RLM dispatch falhou HTTP", status_code=e.code, client_id=client_id)
    except Exception as exc:
        log.error("WhatsApp→RLM dispatch falhou", client_id=client_id, error=str(exc))


async def _mark_read_async(message_id: str) -> None:
    """Marca mensagem como lida de forma não-bloqueante."""
    try:
        from rlm.plugins.whatsapp import mark_as_read
        mark_as_read(message_id)
    except Exception as exc:
        log.debug("mark_as_read falhou", message_id=message_id, error=str(exc))
