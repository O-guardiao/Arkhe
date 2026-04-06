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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rlm.core.engine.permission_policy import PermissionPolicy, PolicyAction
from rlm.core.engine.session_journal import SessionJournal
from rlm.core.security.execution_fence import (
    ApprovalRequiredError,
    ExecutionFence,
    PermissionDeniedError,
)
from rlm.core.tools import ToolDispatcher, get_registry
from rlm.core.tools.specs import PermissionMode
from rlm.server.auth_helpers import require_token

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router FastAPI
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/brain", tags=["brain"])

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
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# POST /brain/prompt
# ---------------------------------------------------------------------------

@router.post("/prompt", response_model=PromptResponse)
async def handle_prompt(
    req: PromptRequest,
    _token: str = Depends(require_token),
) -> PromptResponse:
    """Encaminha prompt ao agente brain e retorna resposta.

    Registra no SessionJournal antes e depois da chamada.
    """
    t0 = time.monotonic()
    journal = _get_journal(req.session_id)
    journal.push_message("user", req.content, extra=req.metadata or None)

    # Integração com o pipeline existente via importação lazy
    # (evita acoplamento circular com runtime_pipeline)
    try:
        from rlm.server.runtime_pipeline import dispatch_runtime_prompt_sync

        result_text = await _run_prompt_async(req.content, req.session_id, req.actor)
    except ImportError:
        result_text = "[brain] Pipeline de runtime não disponível neste ambiente."
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro ao processar prompt para sessão %s", req.session_id)
        result_text = f"[ERROR] {exc}"

    journal.push_message("assistant", result_text)
    elapsed = round((time.monotonic() - t0) * 1000, 2)

    return PromptResponse(
        session_id=req.session_id,
        response=result_text,
        elapsed_ms=elapsed,
    )


async def _run_prompt_async(content: str, session_id: str, actor: str) -> str:
    """Delega ao runtime_pipeline de forma assíncrona, com fallback gracioso."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    try:
        from rlm.server.runtime_pipeline import dispatch_runtime_prompt_sync

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = loop.run_in_executor(
                pool,
                dispatch_runtime_prompt_sync,
                content,
                session_id,
            )
            return await asyncio.wait_for(future, timeout=120.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dispatch_runtime_prompt_sync falhou: %s", exc)
        return f"[brain] Sem resposta disponível: {exc}"


# ---------------------------------------------------------------------------
# POST /brain/exec/{tool_name}
# ---------------------------------------------------------------------------

@router.post("/exec/{tool_name}", response_model=ExecToolResponse)
async def execute_tool(
    tool_name: str,
    body: ExecToolRequest,
    _token: str = Depends(require_token),
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
    _token: str = Depends(require_token),
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
    _token: str = Depends(require_token),
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
    _token: str = Depends(require_token),
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
