from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rlm.core.observability.operator_surface import (
    apply_operator_command,
    build_activity_payload,
    dispatch_operator_prompt,
)
from rlm.logging import get_runtime_logger
from rlm.plugins.channel_registry import ChannelAdapter
from rlm.gateway.auth_helpers import require_token
from rlm.server.runtime_pipeline import RuntimeDispatchServices, build_runtime_dispatch_services, dispatch_runtime_prompt_sync


log = get_runtime_logger("operator_bridge")


# ---------------------------------------------------------------------------
# TuiAdapter — entrega cross-channel para sessoes TUI via session event log
# ---------------------------------------------------------------------------

class TuiAdapter(ChannelAdapter):
    """
    Adapter que entrega respostas para sessoes TUI via SessionManager.

    O TUI cliente faz polling em /operator/session/{id}/activity.
    A entrega consiste em registrar o evento na sessao para que
    o proximo poll do TUI veja a resposta cross-channel.

    Segue o mesmo padrao do WebChatAdapter.
    """

    def __init__(self, session_manager: Any) -> None:
        self._sm = session_manager

    def send_message(self, target_id: str, text: str) -> bool:
        client_id = f"tui:{target_id}"
        try:
            session = self._sm.get_or_create(client_id)
            self._sm.log_event(
                session.session_id,
                "tui_response_delivered",
                {
                    "response_preview": text[:500],
                    "delivery_source": "message_bus",
                    "target_channel": "tui",
                },
            )
            return True
        except Exception as exc:
            log.error("TuiAdapter: falha na entrega", client_id=client_id, error=str(exc))
            return False

    def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
        description = f"[Midia: {media_url_or_path}]"
        if caption:
            description += f" {caption}"
        return self.send_message(target_id, description)

router = APIRouter(prefix="/operator", tags=["operator"])

_INTERNAL_AUTH_ENV_NAMES = ("RLM_ADMIN_TOKEN", "RLM_INTERNAL_TOKEN", "RLM_WS_TOKEN", "RLM_API_TOKEN")


def _require_operator_auth(request: Request) -> None:
    require_token(
        request,
        env_names=_INTERNAL_AUTH_ENV_NAMES,
        scope="internal operator API",
        allow_query=True,
    )


def _get_session_manager(request: Request) -> Any:
    session_manager = getattr(request.app.state, "session_manager", None)
    if session_manager is None:
        raise HTTPException(status_code=503, detail="Session manager indisponivel")
    return session_manager


def _get_runtime_services(request: Request) -> RuntimeDispatchServices:
    return build_runtime_dispatch_services(request.app.state)


def _get_runtime_session(request: Request, session_id: str) -> tuple[Any, Any]:
    session_manager = _get_session_manager(request)
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessao nao encontrada")
    return session_manager, session


class OperatorSessionRequest(BaseModel):
    client_id: str = Field(..., min_length=1)


class OperatorMessageRequest(BaseModel):
    text: str = Field(..., min_length=1)
    client_id: str | None = None


class OperatorCommandRequest(BaseModel):
    command_type: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    branch_id: int | None = None
    client_id: str | None = None


@router.post("/session")
async def operator_attach_session(body: OperatorSessionRequest, request: Request) -> JSONResponse:
    _require_operator_auth(request)
    session_manager = _get_session_manager(request)

    try:
        session = session_manager.get_or_create(body.client_id)
    except Exception as exc:
        log.error("Operator bridge failed to attach session", client_id=body.client_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Falha ao anexar sessao viva") from exc

    payload = session_manager.session_to_dict(session)
    payload.update(
        {
            "activity_url": f"/operator/session/{session.session_id}/activity",
            "message_url": f"/operator/session/{session.session_id}/message",
            "commands_url": f"/operator/session/{session.session_id}/commands",
        }
    )
    return JSONResponse(content=payload)


@router.get("/session/{session_id}/activity")
async def operator_session_activity(session_id: str, request: Request) -> JSONResponse:
    _require_operator_auth(request)
    session_manager, session = _get_runtime_session(request, session_id)
    return JSONResponse(content=build_activity_payload(session_manager, session))


@router.post("/session/{session_id}/message")
async def operator_session_message(
    session_id: str,
    body: OperatorMessageRequest,
    request: Request,
) -> JSONResponse:
    _require_operator_auth(request)
    session_manager, session = _get_runtime_session(request, session_id)
    client_id = (body.client_id or session.client_id or "tui:default").strip()
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Mensagem vazia")

    try:
        session_manager.bind_originating_channel(session, client_id, source="operator_bridge")
        dispatch_operator_prompt(
            session_manager,
            request.app.state.supervisor,
            session,
            text=text,
            origin="tui",
            runtime_services=_get_runtime_services(request),
            client_id=client_id,
            dispatch_fn=dispatch_runtime_prompt_sync,
        )
    except Exception as exc:
        status_code = 409 if "executando um turno" in str(exc) else 500
        log.error(
            "Operator bridge failed to dispatch prompt",
            session_id=session_id,
            client_id=client_id,
            error=str(exc),
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return JSONResponse(
        content={
            "status": "accepted",
            "session_id": session.session_id,
            "client_id": client_id,
        }
    )


@router.post("/session/{session_id}/commands")
async def operator_session_command(
    session_id: str,
    body: OperatorCommandRequest,
    request: Request,
) -> JSONResponse:
    _require_operator_auth(request)
    session_manager, session = _get_runtime_session(request, session_id)
    client_id = (body.client_id or session.client_id or "tui:default").strip()
    command_type = (body.command_type or "").strip()
    if not command_type:
        raise HTTPException(status_code=400, detail="command_type vazio")

    try:
        session_manager.bind_originating_channel(session, client_id, source="operator_bridge")
        entry, runtime = apply_operator_command(
            session_manager,
            session,
            supervisor=getattr(request.app.state, "supervisor", None),
            command_type=command_type,
            payload=dict(body.payload or {}),
            branch_id=body.branch_id,
            origin="tui",
        )
    except Exception as exc:
        status_code = 400 if isinstance(exc, (ValueError, RuntimeError)) else 500
        log.error(
            "Operator bridge failed to apply command",
            session_id=session_id,
            client_id=client_id,
            command_type=command_type,
            error=str(exc),
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return JSONResponse(
        content={
            "session_id": session.session_id,
            "command": entry,
            "runtime": runtime,
            "client_id": client_id,
        }
    )