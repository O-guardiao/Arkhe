"""
Slack Gateway — rlm/server/slack_gateway.py

Receptor de eventos Slack Events API para o RLM.

Arquitetura RLM-nativa:
    Mensagem Slack (app_mention / message.im)
        → POST /slack/events
        → Verificação HMAC-SHA256 via stdlib hmac (sem dependências externas)
        → Roteamento para POST /webhook/slack:{team_id}:{user_id}
        → EventRouter "slack:*" → agente RLM processa
        → ChannelRegistry.reply("slack:{channel}", resposta)

Setup Slack (única vez):
    1. api.slack.com → seu app → Event Subscriptions → Enable Events
    2. Request URL: https://seu-dominio.com/slack/events
    3. O Slack enviará um GET de verificação (url_verification) que este endpoint responde
    4. Subscribe to bot events: app_mention, message.im (DM)
    5. Instale o app no workspace e copie SLACK_BOT_TOKEN (xoxb-...)

Variáveis de ambiente:
    SLACK_BOT_TOKEN      — xoxb-... token do bot (para responder)
    SLACK_SIGNING_SECRET — Signing Secret do app (aba Basic Information)
    SLACK_APP_ID         — ID do app (opcional, para filtrar eventos próprios)

Verificação HMAC-SHA256:
    Slack usa v0=sha256(v0:{timestamp}:{body}) com o SLACK_SIGNING_SECRET.
    Implementado com stdlib hmac.compare_digest — sem dependências externas.

Eventos suportados:
    - url_verification  → retorna challenge (setup do webhook)
    - app_mention       → @bot no canal/thread
    - message.im        → DM direto com o bot
    - message           → mensagem em canal (se subscrito)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.background import BackgroundTask

from rlm.logging import get_runtime_logger

log = get_runtime_logger("slack_gateway")

router = APIRouter(prefix="/slack", tags=["slack"])

# Tolerância de timestamp: 5 minutos (padrão Slack)
_TIMESTAMP_TOLERANCE_S = 300


# ---------------------------------------------------------------------------
# Verificação de assinatura HMAC-SHA256 (stdlib)
# ---------------------------------------------------------------------------

def _verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: bytes,
    slack_signature: str,
) -> bool:
    """
    Verifica a assinatura Slack usando HMAC-SHA256 (100% stdlib).

    Slack assina cada request com:
        v0=HMAC-SHA256(SLACK_SIGNING_SECRET, "v0:{timestamp}:{raw_body}")

    Args:
        signing_secret: SLACK_SIGNING_SECRET do ambiente.
        timestamp: Header X-Slack-Request-Timestamp.
        body: Corpo bruto da requisição em bytes.
        slack_signature: Header X-Slack-Signature (ex: "v0=abc123...").

    Raises:
        ValueError: Timestamp muito antigo (replay attack) ou assinatura inválida.
    """
    # Proteção contra replay: rejeita requests com timestamp > 5 minutos
    try:
        req_time = int(timestamp)
    except (ValueError, TypeError):
        raise ValueError("X-Slack-Request-Timestamp inválido ou ausente.")

    if abs(time.time() - req_time) > _TIMESTAMP_TOLERANCE_S:
        raise ValueError(
            f"Timestamp Slack expirado ({req_time}). "
            "Possível ataque de replay. Verifique o relógio do servidor."
        )

    # Calcula assinatura esperada
    base = f"v0:{timestamp}:".encode("utf-8") + body
    expected_sig = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        base,
        hashlib.sha256,
    ).hexdigest()

    # Comparação segura (timing-safe)
    if not hmac.compare_digest(expected_sig, slack_signature):
        raise ValueError("Assinatura Slack inválida — request não é do Slack.")

    return True


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------

@router.post("/events", response_model=None)
async def slack_events(request: Request) -> JSONResponse | PlainTextResponse:
    """
    Endpoint Slack Events API — recebe todos os eventos do workspace.

    Fluxo:
    1. url_verification: retorna o challenge de texto para validar o endpoint
    2. Verificação HMAC da assinatura Slack
    3. Despacha evento ao agente RLM de forma assíncrona
    4. Retorna HTTP 200 imediatamente (Slack exige resposta em 3 segundos)
    """
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    slack_app_id = os.environ.get("SLACK_APP_ID", "")

    body = await request.body()

    # Decodifica payload
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON inválido")

    event_type = payload.get("type", "")

    # --- url_verification: responde antes de verificar assinatura (é usado no setup) ---
    if event_type == "url_verification":
        challenge = payload.get("challenge", "")
        log.info("Slack url_verification recebido, retornando challenge.")
        return JSONResponse(content={"challenge": challenge})

    # --- Verificação de assinatura HMAC para todos os outros eventos ---
    if signing_secret:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        slack_sig = request.headers.get("X-Slack-Signature", "")
        try:
            _verify_slack_signature(signing_secret, timestamp, body, slack_sig)
        except ValueError as exc:
            log.warn("Slack signature check falhou", error=str(exc))
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        log.warn(
            "SLACK_SIGNING_SECRET não configurado — verificação de assinatura desabilitada",
            recommendation="Configure para produção",
        )

    # Retorna 200 imediatamente — Slack não espera mais que 3 segundos
    return JSONResponse(
        content={"ok": True},
        background=BackgroundTask(_process_slack_event, request, payload, slack_app_id),
    )


async def _process_slack_event(
    request: Request,
    payload: dict,
    slack_app_id: str,
) -> None:
    """
    Processa o evento Slack e encaminha ao agente RLM se relevante.

    Filtra:
    - Eventos gerados pelo próprio bot (evita loop)
    - Mensagens de subtipo (edits, deletes)
    - Eventos sem texto útil
    """
    event = payload.get("event", {})
    event_type = event.get("type", "")
    team_id = payload.get("team_id", "unknown")

    # Ignora eventos que não são mensagens diretas ao bot
    if event_type not in {"app_mention", "message"}:
        log.debug("Slack event type ignorado", event_type=event_type)
        return

    # Ignora subtypes (bot_message, message_changed, etc.)
    subtype = event.get("subtype")
    if subtype:
        log.debug("Slack event subtype ignorado", subtype=subtype)
        return

    user_id = event.get("user", "")
    bot_id = event.get("bot_id")

    # Ignora mensagens do próprio bot para evitar loop
    if bot_id:
        return
    if slack_app_id and user_id == slack_app_id:
        return

    text = event.get("text", "").strip()
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts") or event.get("ts", "")

    if not text or not user_id:
        return

    # Remove a menção @bot do texto (ex: "<@U12345> deploy" → "deploy")
    import re
    text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
    if not text:
        return

    # client_id no padrão RLM: "slack:{team_id}:{channel}"
    # O agente responde via ChannelRegistry.reply("slack:{channel}", ...)
    client_id = f"slack:{team_id}:{channel}"

    rlm_payload = {
        "from_user": user_id,
        "channel": channel,
        "thread_ts": thread_ts,
        "team_id": team_id,
        "text": text,
        "event_type": event_type,
        "source": "slack",
    }

    await _dispatch_to_rlm(client_id, rlm_payload)


async def _dispatch_to_rlm(client_id: str, payload: dict) -> None:
    """Encaminha evento ao endpoint /webhook/{client_id} do RLM (HTTP local)."""
    import urllib.error as uerror
    import urllib.request as urequest

    rlm_host = os.environ.get("RLM_INTERNAL_HOST", "http://127.0.0.1:5000")
    ws_token = os.environ.get("RLM_WS_TOKEN", "")
    url = f"{rlm_host}/webhook/{client_id}"
    if ws_token:
        url += f"?token={ws_token}"

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urequest.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urequest.urlopen(req, timeout=115):
            pass
    except uerror.HTTPError as e:
        log.error("Slack→RLM dispatch falhou HTTP", status_code=e.code, client_id=client_id)
    except Exception as exc:
        log.error("Slack→RLM dispatch falhou", client_id=client_id, error=str(exc))
