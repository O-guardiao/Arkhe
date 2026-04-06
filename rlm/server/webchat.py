"""WebChat nativo do RLM.

O chat é apenas a ponte entre humano e máquina. A fonte de verdade é a
sessão recursiva viva, com runtime persistente, comandos operacionais,
coordenação paralela e observação do workbench.

Arquitetura:
    GET  /webchat                                 → serve webchat.html
    POST /webchat/message                         → dispara turno no runtime
    GET  /webchat/session/{session_id}/activity   → snapshot operacional
    POST /webchat/session/{session_id}/commands   → enqueue de comando recursivo
    GET  /webchat/health                          → health check da UI
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error as uerror
import urllib.request as urequest
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from rlm.core.observability.operator_surface import apply_operator_command, build_activity_payload, build_runtime_snapshot
from rlm.logging import get_runtime_logger
from rlm.plugins.channel_registry import ChannelAdapter
from rlm.server.auth_helpers import build_internal_auth_headers

log = get_runtime_logger("webchat")

router = APIRouter(prefix="/webchat", tags=["webchat"])

# Diretório de arquivos estáticos (rlm/static/)
_STATIC_DIR = Path(__file__).parent.parent / "static"


# ---------------------------------------------------------------------------
# WebChatAdapter — permite que DeliveryWorker entregue via SessionManager
# ---------------------------------------------------------------------------

class WebChatAdapter(ChannelAdapter):
    """
    Adapter que entrega respostas para sessões webchat via SessionManager.

    O frontend webchat usa polling em /webchat/session/{id}/activity.
    A entrega consiste em registrar o evento na sessão para que
    o próximo poll do frontend veja a resposta.
    """

    def __init__(self, session_manager: Any) -> None:
        self._sm = session_manager

    def send_message(self, target_id: str, text: str) -> bool:
        """
        Entrega texto para sessão webchat via log de evento.

        Args:
            target_id: ID da sessão webchat (parte após "webchat:")
            text: Texto da resposta
        """
        client_id = f"webchat:{target_id}"
        try:
            session = self._sm.get_or_create(client_id)
            self._sm.log_event(
                session.session_id,
                "webchat_response_delivered",
                {
                    "response_preview": text[:500],
                    "delivery_source": "message_bus",
                },
            )
            return True
        except Exception as exc:
            log.error("WebChatAdapter: falha na entrega", client_id=client_id, error=str(exc))
            return False

    def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
        """WebChat não suporta envio direto de mídia — loga descrição textual."""
        description = f"[Mídia: {media_url_or_path}]"
        if caption:
            description += f" {caption}"
        return self.send_message(target_id, description)


def _build_runtime_snapshot(session: object) -> dict | None:
    return build_runtime_snapshot(session)


def _get_session_manager(request: Request) -> Any:
    session_manager = getattr(request.app.state, "session_manager", None)
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Session manager indisponivel")
    return session_manager


def _get_runtime_session(request: Request, session_id: str) -> tuple[Any, Any]:
    session_manager = _get_session_manager(request)
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessao nao encontrada")
    return session_manager, session


def _execute_runtime_command(
    *,
    request: Request,
    session_manager: Any,
    session: Any,
    env: Any,
    command_type: str,
    payload: dict[str, Any],
    branch_id: int | None,
    entry: dict[str, Any],
) -> dict[str, Any]:
    applied, _runtime = apply_operator_command(
        session_manager,
        session,
        supervisor=getattr(request.app.state, "supervisor", None),
        command_type=command_type,
        payload=payload,
        branch_id=branch_id,
        origin="webchat",
    )
    return applied


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    text: str
    session_id: str | None = None


class RecursiveCommandRequest(BaseModel):
    command_type: str
    payload: dict[str, Any] | None = None
    branch_id: int | None = None


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

    A UI passa a observar a sessão viva pelo endpoint /activity. Não existe mais
    streaming artificial de texto pronto como contrato principal.
    """
    if os.environ.get("RLM_WEBCHAT_DISABLED", "false").lower() == "true":
        raise HTTPException(status_code=503, detail="WebChat desabilitado.")

    text = (msg.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")

    session_id = msg.session_id or secrets.token_hex(8)
    client_id = f"webchat:{session_id}"
    session_manager = _get_session_manager(request)
    try:
        runtime_session = session_manager.get_or_create(client_id)
    except Exception as exc:
        log.error("WebChat falhou ao preparar sessao viva", client_id=client_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Falha ao preparar sessao recursiva") from exc

    runtime_session_id = runtime_session.session_id
    try:
        session_manager.log_event(
            runtime_session_id,
            "webchat_message_enqueued",
            {
                "browser_session_id": session_id,
                "text_preview": text[:200],
            },
        )
    except Exception as exc:
        log.warn("WebChat nao conseguiu registrar fila da mensagem", session_id=runtime_session_id, error=str(exc))

    # Despacha ao RLM de forma assíncrona
    return JSONResponse(
        content={
            "session_id": session_id,
            "runtime_session_id": runtime_session_id,
            "activity_url": f"/webchat/session/{runtime_session_id}/activity" if runtime_session_id else None,
        },
        background=BackgroundTask(
            _dispatch_to_rlm,
            client_id=client_id,
            text=text,
            session_id=session_id,
            session_manager=session_manager,
            runtime_session_id=runtime_session_id,
        ),
    )


@router.get("/session/{session_id}/activity")
async def webchat_session_activity(session_id: str, request: Request) -> JSONResponse:
    """Expõe a sessão viva do webchat com eventos reais e snapshot do runtime."""
    session_manager, session = _get_runtime_session(request, session_id)
    return JSONResponse(content=build_activity_payload(session_manager, session))


@router.post("/session/{session_id}/commands")
async def webchat_session_command(
    session_id: str,
    command: RecursiveCommandRequest,
    request: Request,
) -> JSONResponse:
    """Enfileira e aplica um comando operacional na sessão recursiva viva."""
    session_manager, session = _get_runtime_session(request, session_id)
    command_type = (command.command_type or "").strip()
    if not command_type:
        raise HTTPException(status_code=400, detail="command_type vazio")

    payload = dict(command.payload or {})
    payload.setdefault("source", "webchat")
    payload.setdefault("issued_at", int(time.time()))

    try:
        entry, runtime = apply_operator_command(
            session_manager,
            session,
            supervisor=getattr(request.app.state, "supervisor", None),
            command_type=command_type,
            payload=payload,
            branch_id=command.branch_id,
            origin="webchat",
        )
    except Exception as exc:
        status_code = 400 if isinstance(exc, (ValueError, RuntimeError)) else 500
        log.error("WebChat falhou ao aplicar comando", session_id=session_id, command_type=command_type, error=str(exc))
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return JSONResponse(
        content={
            "session_id": session_id,
            "command": entry,
            "runtime": runtime,
        }
    )


# ---------------------------------------------------------------------------
# Interno
# ---------------------------------------------------------------------------

async def _dispatch_to_rlm(
    client_id: str,
    text: str,
    session_id: str,
    session_manager: Any | None = None,
    runtime_session_id: str | None = None,
) -> None:
    """Envia mensagem ao /webhook/{client_id} e registra o desfecho na sessão viva."""
    rlm_host = os.environ.get("RLM_INTERNAL_HOST", "http://127.0.0.1:5000")
    url = f"{rlm_host}/webhook/{client_id}"

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
        headers=build_internal_auth_headers(),
        method="POST",
    )

    result_text = "(sem resposta do agente)"
    try:
        with urequest.urlopen(req, timeout=115) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            result_text = (
                body.get("response")
                or body.get("result")
                or body.get("output")
                or body.get("answer")
                or str(body)
            )
            if session_manager is not None and runtime_session_id:
                try:
                    session_manager.log_event(
                        runtime_session_id,
                        "webchat_response_ready",
                        {
                            "response_preview": result_text[:200],
                        },
                    )
                except Exception as exc:
                    log.warn("WebChat nao conseguiu registrar resposta pronta", session_id=runtime_session_id, error=str(exc))
    except uerror.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        result_text = f"Erro ao processar: HTTP {e.code} — {err_body[:200]}"
        if session_manager is not None and runtime_session_id:
            try:
                session_manager.log_event(
                    runtime_session_id,
                    "webchat_response_error",
                    {
                        "status_code": e.code,
                        "body_preview": err_body[:200],
                    },
                )
            except Exception as exc:
                log.warn("WebChat nao conseguiu registrar erro HTTP", session_id=runtime_session_id, error=str(exc))
        log.error("WebChat→RLM HTTP error", status_code=e.code, client_id=client_id, body_preview=err_body[:200])
    except Exception as exc:
        result_text = f"Erro interno: {exc}"
        if session_manager is not None and runtime_session_id:
            try:
                session_manager.log_event(
                    runtime_session_id,
                    "webchat_response_error",
                    {
                        "error": str(exc),
                    },
                )
            except Exception as inner_exc:
                log.warn("WebChat nao conseguiu registrar erro interno", session_id=runtime_session_id, error=str(inner_exc))
        log.error("WebChat→RLM falhou", client_id=client_id, error=str(exc))

    _ = result_text
