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

from rlm.logging import get_runtime_logger
from rlm.server.auth_helpers import build_internal_auth_headers

log = get_runtime_logger("webchat")

router = APIRouter(prefix="/webchat", tags=["webchat"])

# Diretório de arquivos estáticos (rlm/static/)
_STATIC_DIR = Path(__file__).parent.parent / "static"

def _tail(items: list[dict], limit: int) -> list[dict]:
    if limit <= 0:
        return list(items)
    return list(items[-limit:])


def _build_runtime_snapshot(session: object) -> dict | None:
    rlm_instance = getattr(session, "rlm_instance", None)
    env = getattr(rlm_instance, "_persistent_env", None)
    snapshot = getattr(env, "get_runtime_state_snapshot", None)
    if not callable(snapshot):
        return None

    runtime = snapshot()
    recursive = dict(runtime.get("recursive_session") or {})
    tasks = dict(runtime.get("tasks") or {})
    attachments = dict(runtime.get("attachments") or {})
    timeline = dict(runtime.get("timeline") or {})
    coordination = dict(runtime.get("coordination") or {})

    recursive["messages"] = _tail(list(recursive.get("messages") or []), 40)
    recursive["commands"] = _tail(list(recursive.get("commands") or []), 20)
    recursive["events"] = _tail(list(recursive.get("events") or []), 40)
    tasks["items"] = _tail(list(tasks.get("items") or []), 20)
    attachments["items"] = _tail(list(attachments.get("items") or []), 10)
    timeline["entries"] = _tail(list(timeline.get("entries") or []), 20)
    coordination["events"] = _tail(list(coordination.get("events") or []), 20)

    runtime["recursive_session"] = recursive
    runtime["tasks"] = tasks
    runtime["attachments"] = attachments
    runtime["timeline"] = timeline
    runtime["coordination"] = coordination
    return runtime


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


def _update_command_status(env: Any, command_id: int, *, status: str, outcome: dict[str, Any] | None = None) -> dict[str, Any] | None:
    update_command = getattr(env, "update_recursive_command", None)
    if not callable(update_command):
        return None
    return update_command(command_id, status=status, outcome=outcome)


def _persist_session_state(session: Any, checkpoint_dir: Path) -> str:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    save_state = getattr(getattr(session, "rlm_instance", None), "save_state", None)
    if callable(save_state):
        return str(save_state(str(checkpoint_dir)))

    env = getattr(getattr(session, "rlm_instance", None), "_persistent_env", None)
    save_checkpoint = getattr(env, "save_checkpoint", None)
    if not callable(save_checkpoint):
        raise RuntimeError("Sessao sem mecanismo de checkpoint persistente")
    checkpoint_path = checkpoint_dir / "repl_checkpoint.json"
    return str(save_checkpoint(str(checkpoint_path)))


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
    reason = str(payload.get("reason") or payload.get("note") or "").strip()
    outcome: dict[str, Any] = {}
    session.metadata = dict(getattr(session, "metadata", {}) or {})

    if command_type == "pause_runtime":
        set_paused = getattr(env, "set_runtime_paused", None)
        if not callable(set_paused):
            raise RuntimeError("Runtime sem suporte a pausa operacional")
        control_state = set_paused(True, reason=reason or "Paused by operator", origin="webchat")
        supervisor = getattr(request.app.state, "supervisor", None)
        abort_result = None
        abort = getattr(supervisor, "abort", None)
        if callable(abort):
            abort_result = bool(abort(session.session_id, reason=reason or "Paused by operator"))
        session.metadata["operator_paused"] = True
        session.metadata["operator_pause_reason"] = reason
        session_manager.update_session(session)
        outcome = {"applied": True, "controls": control_state, "abort_requested": abort_result}
    elif command_type == "resume_runtime":
        set_paused = getattr(env, "set_runtime_paused", None)
        if not callable(set_paused):
            raise RuntimeError("Runtime sem suporte a retomada operacional")
        control_state = set_paused(False, reason=reason or "Resumed by operator", origin="webchat")
        session.metadata["operator_paused"] = False
        session.metadata["operator_pause_reason"] = ""
        if getattr(session, "status", "") == "aborted":
            session.status = "idle"
        session_manager.update_session(session)
        outcome = {"applied": True, "controls": control_state}
    elif command_type == "create_checkpoint":
        state_dir = Path(getattr(session, "state_dir", "") or ".")
        checkpoint_name = str(payload.get("checkpoint_name") or f"operator-{int(time.time())}").strip() or f"operator-{int(time.time())}"
        checkpoint_dir = state_dir / "operator_checkpoints" / checkpoint_name
        save_result = _persist_session_state(session, checkpoint_dir)
        mark_checkpoint = getattr(env, "mark_runtime_checkpoint", None)
        control_state = mark_checkpoint(str(checkpoint_dir), origin="webchat") if callable(mark_checkpoint) else None
        outcome = {
            "applied": True,
            "checkpoint_path": str(checkpoint_dir),
            "save_result": save_result,
            "controls": control_state,
        }
    elif command_type == "reprioritize_branch":
        if branch_id is None:
            raise ValueError("branch_id e obrigatorio para reprioritize_branch")
        reprioritize = getattr(env, "reprioritize_branch", None)
        if not callable(reprioritize):
            raise RuntimeError("Runtime sem suporte a repriorizacao operacional")
        priority = int(payload.get("priority", 0))
        control_state = reprioritize(branch_id, priority, reason=reason, origin="webchat")
        outcome = {"applied": True, "branch_id": branch_id, "priority": priority, "controls": control_state}
    elif command_type in {"focus_branch", "fix_winner_branch"}:
        if branch_id is None:
            raise ValueError(f"branch_id e obrigatorio para {command_type}")
        set_focus = getattr(env, "set_runtime_focus", None)
        if not callable(set_focus):
            raise RuntimeError("Runtime sem suporte a foco operacional de branch")
        control_state = set_focus(branch_id, fixed=command_type == "fix_winner_branch", reason=reason, origin="webchat")
        outcome = {
            "applied": True,
            "branch_id": branch_id,
            "fixed": command_type == "fix_winner_branch",
            "controls": control_state,
        }
    elif command_type == "operator_note":
        record_note = getattr(env, "record_operator_note", None)
        if not callable(record_note):
            raise RuntimeError("Runtime sem suporte a nota operacional")
        control_state = record_note(str(payload.get("note") or reason), branch_id=branch_id, origin="webchat")
        outcome = {"applied": True, "branch_id": branch_id, "controls": control_state}
    elif command_type in {"request_runtime_summary", "review_parallel_state"}:
        outcome = {"applied": True, "requested": command_type, "branch_id": branch_id}
    else:
        outcome = {"applied": False, "reason": "command_type sem executor dedicado"}

    updated = _update_command_status(env, int(entry["command_id"]), status="completed", outcome=outcome)
    session_manager.log_event(
        session.session_id,
        "webchat_command_applied",
        {
            "command_id": entry.get("command_id"),
            "command_type": command_type,
            "branch_id": branch_id,
            "outcome": outcome,
        },
    )
    return updated or {**entry, "status": "completed", "outcome": outcome}


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

    event_log = list(reversed(session_manager.get_events(session_id, limit=40)))
    return JSONResponse(
        content={
            "session": session_manager.session_to_dict(session),
            "event_log": event_log,
            "runtime": _build_runtime_snapshot(session),
        }
    )


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

    rlm_instance = getattr(session, "rlm_instance", None)
    env = getattr(rlm_instance, "_persistent_env", None)
    queue_command = getattr(env, "queue_recursive_command", None)
    if not callable(queue_command):
        raise HTTPException(status_code=409, detail="Sessao sem canal de comando recursivo")

    payload = dict(command.payload or {})
    payload.setdefault("source", "webchat")
    payload.setdefault("issued_at", int(time.time()))

    try:
        entry = queue_command(
            command_type,
            payload=payload,
            branch_id=command.branch_id,
        )
    except Exception as exc:
        log.error("WebChat falhou ao enfileirar comando", session_id=session_id, command_type=command_type, error=str(exc))
        raise HTTPException(status_code=500, detail="Falha ao enfileirar comando recursivo") from exc

    session_manager.log_event(
        session_id,
        "webchat_command_enqueued",
        {
            "command_id": entry.get("command_id"),
            "command_type": entry.get("command_type"),
            "branch_id": entry.get("branch_id"),
            "payload": payload,
        },
    )

    try:
        entry = _execute_runtime_command(
            request=request,
            session_manager=session_manager,
            session=session,
            env=env,
            command_type=command_type,
            payload=payload,
            branch_id=command.branch_id,
            entry=entry,
        )
    except Exception as exc:
        _update_command_status(env, int(entry["command_id"]), status="failed", outcome={"error": str(exc)})
        session_manager.log_event(
            session_id,
            "webchat_command_failed",
            {
                "command_id": entry.get("command_id"),
                "command_type": command_type,
                "branch_id": command.branch_id,
                "error": str(exc),
            },
        )
        log.error("WebChat falhou ao aplicar comando", session_id=session_id, command_type=command_type, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse(
        content={
            "session_id": session_id,
            "command": entry,
            "runtime": _build_runtime_snapshot(session),
        }
    )


# ---------------------------------------------------------------------------
# Interno
# ---------------------------------------------------------------------------

async def _dispatch_to_rlm(
    client_id: str,
    text: str,
    session_id: str,
    session_manager: object | None = None,
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
