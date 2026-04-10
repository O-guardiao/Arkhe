"""
BrainRouter — endpoints dedicados à camada de raciocínio (brain) do RLM.

Integra:
  - ToolDispatcher: executa ferramentas com permissão + auditoria
  - PermissionPolicy: lida com autorização granular
  - SessionJournal: persiste histórico de conversas
  - ExecutionFence: barreira de segurança de execução

Endpoints:
    POST   /brain/prompt              — Enviar prompt ao agente brain
    POST   /brain/exec/{tool_name}    — Executar ferramenta diretamente
    GET    /brain/tools               — Listar ferramentas disponíveis
    GET    /brain/session/{sid}       — Histórico de uma sessão
    DELETE /brain/session/{sid}       — Limpar histórico de sessão
    GET    /brain/health              — Health do subsistema brain
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rlm.core.engine.permission_policy import PermissionPolicy
from rlm.core.engine.session_journal import SessionJournal
from rlm.core.security.execution_fence import (
    ApprovalRequiredError,
    ExecutionFence,
    PermissionDeniedError,
)
from rlm.core.tools import get_registry
from rlm.gateway.auth_helpers import require_token
from rlm.server.runtime_pipeline import build_runtime_dispatch_services, dispatch_runtime_prompt_sync

logger = logging.getLogger(__name__)
_BRAIN_AUTH_ENV_NAMES = ("RLM_ADMIN_TOKEN", "RLM_API_TOKEN", "RLM_WS_TOKEN")

# ---------------------------------------------------------------------------
# Router FastAPI
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/brain", tags=["brain"])


def _empty_tool_calls() -> list[dict[str, object]]:
    return []

# ---------------------------------------------------------------------------
# Schemas Pydantic
# ---------------------------------------------------------------------------

class PromptRequest(BaseModel):
    session_id: str = Field(..., description="Identificador único da sessão")
    content: str = Field(..., description="Conteúdo do prompt do usuário")
    actor: str = Field(default="user", description="Identificador do actor")
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromptResponse(BaseModel):
    session_id: str
    response: str
    elapsed_ms: float
    tool_calls: list[dict[str, object]] = Field(default_factory=_empty_tool_calls)


class ExecToolRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="api")


class ExecToolResponse(BaseModel):
    tool_name: str
    success: bool
    result: Any = None
    error: str | None = None
    elapsed_ms: float
    denied: bool = False
    requires_approval: bool = False


class ToolListItem(BaseModel):
    name: str
    description: str
    layer: str
    required_permission: str


# ---------------------------------------------------------------------------
# Dependências compartilhadas
# ---------------------------------------------------------------------------

def _get_fence() -> ExecutionFence:
    """Cria ExecutionFence a partir das variáveis de ambiente."""
    policy_path_str = os.environ.get("RLM_POLICY_FILE")
    if policy_path_str:
        path = Path(policy_path_str)
        if path.exists():
            policy = PermissionPolicy.from_file(path)
        else:
            logger.warning("RLM_POLICY_FILE não encontrado: %s — usando default", path)
            policy = PermissionPolicy.default()
    else:
        policy = PermissionPolicy.default()

    return ExecutionFence(policy=policy)


def _get_journal(session_id: str) -> SessionJournal:
    data_dir = Path(os.environ.get("RLM_SESSIONS_DIR", "~/.rlm/sessions")).expanduser()
    return SessionJournal(data_dir=data_dir, session_id=session_id)


def _require_brain_api_auth(request: Request) -> str:
    return require_token(
        request,
        env_names=_BRAIN_AUTH_ENV_NAMES,
        scope="brain API",
    )


# ---------------------------------------------------------------------------
# POST /brain/prompt
# ---------------------------------------------------------------------------

@router.post("/prompt", response_model=PromptResponse)
async def handle_prompt(
    req: PromptRequest,
    request: Request,
    _token: str = Depends(_require_brain_api_auth),
) -> PromptResponse:
    """Encaminha prompt ao agente brain e retorna resposta.

    Registra no SessionJournal antes e depois da chamada.
    """
    t0 = time.monotonic()
    journal = _get_journal(req.session_id)
    journal.push_message("user", req.content, extra=req.metadata or None)

    try:
        dispatch_result = await _run_prompt_async(request, req)
        result_text = str(dispatch_result.get("response", "") or "")
        response_session_id = str(dispatch_result.get("session_id", req.session_id) or req.session_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro ao processar prompt para sessão %s", req.session_id)
        result_text = f"[ERROR] {exc}"
        response_session_id = req.session_id

    journal.push_message("assistant", result_text)
    elapsed = round((time.monotonic() - t0) * 1000, 2)

    return PromptResponse(
        session_id=response_session_id,
        response=result_text,
        elapsed_ms=elapsed,
    )


async def _run_prompt_async(request: Request, req: PromptRequest) -> dict[str, Any]:
    """Delega ao runtime pipeline compartilhado usando o mesmo path lógico do daemon."""
    import asyncio
    metadata = dict(req.metadata or {})
    client_id = str(metadata.get("client_id") or req.session_id)
    services = build_runtime_dispatch_services(request.app.state)
    session_manager = cast(Any, services.session_manager)
    session = session_manager.get_or_create(client_id)
    payload_metadata = dict(metadata)
    payload_metadata.setdefault("actor", req.actor)
    payload_metadata.setdefault("transport", "brain_router")
    payload_metadata.setdefault("requested_session_id", req.session_id)

    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(
            None,
            lambda: dispatch_runtime_prompt_sync(
                services,
                client_id,
                {
                    "text": req.content,
                    "content_type": "text",
                    "metadata": payload_metadata,
                },
                session=session,
                source_name="brain_router",
            ),
        ),
        timeout=120.0,
    )


# ---------------------------------------------------------------------------
# POST /brain/exec/{tool_name}
# ---------------------------------------------------------------------------

@router.post("/exec/{tool_name}", response_model=ExecToolResponse)
async def execute_tool(
    tool_name: str,
    body: ExecToolRequest,
    _token: str = Depends(_require_brain_api_auth),
) -> ExecToolResponse:
    """Executa ferramenta diretamente, passando pelo ExecutionFence."""
    fence = _get_fence()

    try:
        dispatch_result = await fence.execute(tool_name, body.inputs, actor=body.actor)
    except PermissionDeniedError as e:
        return ExecToolResponse(
            tool_name=tool_name,
            success=False,
            denied=True,
            error=str(e),
            elapsed_ms=0.0,
        )
    except ApprovalRequiredError as e:
        return ExecToolResponse(
            tool_name=tool_name,
            success=False,
            requires_approval=True,
            error=str(e),
            elapsed_ms=0.0,
        )

    return ExecToolResponse(
        tool_name=dispatch_result.tool_name,
        success=dispatch_result.success,
        result=dispatch_result.result,
        error=dispatch_result.error,
        elapsed_ms=dispatch_result.elapsed_ms,
        denied=dispatch_result.denied,
        requires_approval=dispatch_result.requires_approval,
    )


# ---------------------------------------------------------------------------
# GET /brain/tools
# ---------------------------------------------------------------------------

@router.get("/tools", response_model=list[ToolListItem])
async def list_tools(
    _token: str = Depends(_require_brain_api_auth),
) -> list[ToolListItem]:
    """Lista todas as ferramentas registradas no ToolRegistry."""
    registry = get_registry()
    items: list[ToolListItem] = []
    for spec in registry.all_specs():
        items.append(
            ToolListItem(
                name=spec.name,
                description=spec.description,
                layer=spec.layer.value,
                required_permission=spec.required_permission.value,
            )
        )
    return items


# ---------------------------------------------------------------------------
# GET /brain/session/{session_id}
# ---------------------------------------------------------------------------

@router.get("/session/{session_id}")
async def get_session(
    session_id: str,
    include_rotated: bool = False,
    _token: str = Depends(_require_brain_api_auth),
) -> JSONResponse:
    """Retorna histórico de mensagens de uma sessão."""
    journal = _get_journal(session_id)
    messages = journal.load_messages(include_rotated=include_rotated)
    return JSONResponse({"session_id": session_id, "messages": messages, "count": len(messages)})


# ---------------------------------------------------------------------------
# DELETE /brain/session/{session_id}
# ---------------------------------------------------------------------------

@router.delete("/session/{session_id}")
async def clear_session(
    session_id: str,
    _token: str = Depends(_require_brain_api_auth),
) -> JSONResponse:
    """Remove histórico persistido de uma sessão."""
    journal = _get_journal(session_id)
    journal.clear()
    return JSONResponse({"session_id": session_id, "cleared": True})


# ---------------------------------------------------------------------------
# GET /brain/health
# ---------------------------------------------------------------------------

@router.get("/health")
async def brain_health() -> JSONResponse:
    """Health check do subsistema brain (sem autenticação — liveness probe)."""
    registry = get_registry()
    tool_count = len(registry.all_specs())

    return JSONResponse(
        {
            "status": "healthy",
            "tool_count": tool_count,
            "timestamp": time.time(),
        }
    )


# ---------------------------------------------------------------------------
# Admin: gestão de clientes/dispositivos  (/brain/admin/clients/*)
# ---------------------------------------------------------------------------

class ClientRegisterRequest(BaseModel):
    client_id: str = Field(..., description="ID único do cliente/dispositivo")
    profile: str = Field(default="default", description="Perfil do cliente")
    description: str = Field(default="", description="Descrição livre")
    context_hint: str = Field(default="", description="Contexto preferencial")
    permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _get_auth_db() -> str:
    """Resolve caminho do SQLite de autenticação via env ou padrão."""
    from pathlib import Path as _Path
    env_val = os.environ.get("RLM_AUTH_DB", "")
    if env_val:
        return env_val
    return str(_Path(os.environ.get("RLM_DATA_DIR", "~/.rlm")).expanduser() / "rlm_sessions.db")


@router.post("/admin/clients", status_code=201)
async def admin_client_register(
    body: ClientRegisterRequest,
    _token: str = Depends(_require_brain_api_auth),
) -> JSONResponse:
    """Registra novo cliente/dispositivo. Retorna token em claro — exibido uma única vez."""
    from rlm.core.auth import register_client

    try:
        raw_token = register_client(
            db_path=_get_auth_db(),
            client_id=body.client_id,
            profile=body.profile,
            description=body.description,
            context_hint=body.context_hint,
            permissions=body.permissions,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return JSONResponse(
        {"client_id": body.client_id, "token": raw_token, "profile": body.profile},
        status_code=201,
    )


@router.get("/admin/clients")
async def admin_client_list(
    include_inactive: bool = False,
    _token: str = Depends(_require_brain_api_auth),
) -> JSONResponse:
    """Lista clientes registrados."""
    from rlm.core.auth import list_clients

    clients = list_clients(_get_auth_db(), active_only=not include_inactive)
    return JSONResponse({"clients": clients, "count": len(clients)})


@router.get("/admin/clients/{client_id}")
async def admin_client_status(
    client_id: str,
    _token: str = Depends(_require_brain_api_auth),
) -> JSONResponse:
    """Retorna status detalhado de um cliente."""
    from rlm.core.auth import get_client_status

    info = get_client_status(_get_auth_db(), client_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Cliente '{client_id}' não encontrado.")
    return JSONResponse(info)


@router.delete("/admin/clients/{client_id}")
async def admin_client_revoke(
    client_id: str,
    _token: str = Depends(_require_brain_api_auth),
) -> JSONResponse:
    """Revoga um cliente (marca active=0, sem DELETE para manter auditoria)."""
    from rlm.core.auth import revoke_client

    ok = revoke_client(_get_auth_db(), client_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Cliente '{client_id}' não encontrado ou já revogado.")
    return JSONResponse({"client_id": client_id, "revoked": True})
