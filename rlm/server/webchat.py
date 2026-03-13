"""
WebChat — rlm/server/webchat.py

Interface de chat web servida diretamente pelo servidor RLM.

Arquitetura RLM-nativa:
    GET  /webchat              → serve webchat.html (interface do usuário)
    POST /webchat/message      → encaminha mensagem ao /webhook/webchat:{session_id}
    GET  /webchat/stream/{id}  → SSE do resultado do agente (long-poll)
    GET  /webchat/health       → health check da UI

Design:
    - Zero dependências novas: usa FastAPI nativo + FileResponse
    - O HTML é um arquivo single-page em rlm/static/webchat.html
    - Comunicação: POST message → EventRouter "webchat:*" → resposta por SSE
    - Session IDs armazenados em localStorage do navegador
    - Autenticação opcional via RLM_WS_TOKEN

Nota: o webchat está sempre ativo (sem condicional de env).
Para desabilitar em produção, defina RLM_WEBCHAT_DISABLED=true.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
import urllib.request as urequest
import urllib.error as uerror
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from rlm.logging import get_runtime_logger

log = get_runtime_logger("webchat")

router = APIRouter(prefix="/webchat", tags=["webchat"])

# Diretório de arquivos estáticos (rlm/static/)
_STATIC_DIR = Path(__file__).parent.parent / "static"

# Buffer de resultados por session_id: {session_id: [texto, ...]}
# Em produção isso viria de um event bus; aqui usamos dict em memória simples.
_result_buffer: dict[str, list[str]] = {}
_result_events: dict[str, asyncio.Event] = {}
_buffer_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    text: str
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
@router.get("/")
async def webchat_ui() -> FileResponse:
    """Serve a interface de chat (webchat.html)."""
    html_path = _STATIC_DIR / "webchat.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="webchat.html não encontrado")
    return FileResponse(str(html_path), media_type="text/html")


@router.get("/health")
async def webchat_health() -> JSONResponse:
    """Health check para o frontend verificar se o servidor está online."""
    return JSONResponse(content={
        "status": "ok",
        "webchat": True,
        "timestamp": int(time.time()),
    })


@router.post("/message")
async def webchat_message(msg: ChatMessage, request: Request) -> JSONResponse:
    """
    Recebe uma mensagem do usuário e a encaminha ao agente RLM.

    O resultado é entregue via SSE em GET /webchat/stream/{session_id}.
    Retorna imediatamente com o session_id para o cliente abrir o stream.
    """
    if os.environ.get("RLM_WEBCHAT_DISABLED", "false").lower() == "true":
        raise HTTPException(status_code=503, detail="WebChat desabilitado.")

    text = (msg.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")

    session_id = msg.session_id or secrets.token_hex(8)
    client_id = f"webchat:{session_id}"

    # Prepara buffer de resultado para este request
    result_key = f"{session_id}:{int(time.time() * 1000)}"
    with _buffer_lock:
        _result_buffer[result_key] = []
        _result_events[result_key] = asyncio.Event()

    # Despacha ao RLM de forma assíncrona
    return JSONResponse(
        content={
            "session_id": session_id,
            "result_key": result_key,
            "stream_url": f"/webchat/stream/{result_key}",
        },
        background=BackgroundTask(
            _dispatch_to_rlm,
            client_id=client_id,
            text=text,
            session_id=session_id,
            result_key=result_key,
        ),
    )


@router.get("/stream/{result_key}")
async def webchat_stream(result_key: str, request: Request) -> StreamingResponse:
    """
    Stream SSE com o resultado do agente para uma mensagem específica.

    O cliente abre este endpoint com EventSource e recebe o texto do agente
    conforme o RLM processa. Retorna event: done ao final.

    Protocolo SSE:
        data: {"text": "parte do texto"}\n\n   ← chunk de texto
        data: [DONE]\n\n                        ← fim do stream
    """
    async def event_stream() -> AsyncIterator[str]:
        # Aguarda resultado com timeout de 120 segundos (tempo máximo do RLM)
        with _buffer_lock:
            event = _result_events.get(result_key)
        if not event:
            yield "data: {\"error\": \"session não encontrada\"}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Heartbeat a cada 15 segundos para manter conexão viva
        deadline = time.time() + 120
        while not event.is_set():
            if time.time() > deadline:
                yield "data: {\"error\": \"timeout\"}\n\n"
                yield "data: [DONE]\n\n"
                _cleanup_result(result_key)
                return
            # Verifica se cliente desconectou
            if await request.is_disconnected():
                _cleanup_result(result_key)
                return
            yield ": heartbeat\n\n"  # SSE comment (keepalive)
            await asyncio.sleep(15)

        # Entrega resultado acumulado
        with _buffer_lock:
            chunks = list(_result_buffer.get(result_key, []))
        full_text = "".join(chunks)

        # Emite em chunks de 80 chars para aparência de streaming
        for i in range(0, max(1, len(full_text)), 80):
            piece = full_text[i : i + 80]
            payload = json.dumps({"text": piece}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            await asyncio.sleep(0.02)

        yield "data: [DONE]\n\n"
        _cleanup_result(result_key)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# Interno
# ---------------------------------------------------------------------------

async def _dispatch_to_rlm(
    client_id: str,
    text: str,
    session_id: str,
    result_key: str,
) -> None:
    """Envia mensagem ao /webhook/{client_id} e armazena o resultado no buffer."""
    rlm_host = os.environ.get("RLM_INTERNAL_HOST", "http://127.0.0.1:5000")
    ws_token = os.environ.get("RLM_WS_TOKEN", "")
    url = f"{rlm_host}/webhook/{client_id}"
    if ws_token:
        url += f"?token={ws_token}"

    payload = {
        "from_user": "webchat",
        "session_id": session_id,
        "text": text,
        "channel": "webchat",
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urequest.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    result_text = "(sem resposta do agente)"
    try:
        with urequest.urlopen(req, timeout=115) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            result_text = (
                body.get("result")
                or body.get("output")
                or body.get("answer")
                or str(body)
            )
    except uerror.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        result_text = f"Erro ao processar: HTTP {e.code} — {err_body[:200]}"
        log.error("WebChat→RLM HTTP error", status_code=e.code, client_id=client_id, body_preview=err_body[:200])
    except Exception as exc:
        result_text = f"Erro interno: {exc}"
        log.error("WebChat→RLM falhou", client_id=client_id, error=str(exc))

    # Armazena resultado e sinaliza o event
    with _buffer_lock:
        if result_key in _result_buffer:
            _result_buffer[result_key] = [result_text]
        event = _result_events.get(result_key)
    if event:
        event.set()


def _cleanup_result(result_key: str) -> None:
    """Remove resultado do buffer após entrega (evita vazamento de memória)."""
    with _buffer_lock:
        _result_buffer.pop(result_key, None)
        _result_events.pop(result_key, None)
