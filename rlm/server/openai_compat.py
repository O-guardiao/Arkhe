"""
RLM OpenAI-Compatible API Layer — Fase 9.2

Implementa POST /v1/chat/completions compatível com a SDK OpenAI.
Qualquer cliente que fala com a API OpenAI (LangChain, Cursor, n8n,
litellm, scripts Python com `openai` SDK) passa a falar com o RLM
sem mudança de código — só troca a `base_url`.

Análogo ao openai-http.ts do OpenClaw Gateway.

Diferença chave vs OpenClaw:
- OpenClaw: repassa para um LLM real cada turno
- RLM: o "chat" é continuação de sessão multi-turno com memória vetorial,
  REPL Python, tools, e decay temporal. O histórico não é passado
  pela request — está na sessão RLM.

Mapeamento de campo:
    OpenAI `messages[-1].content` → prompt para o agente RLM
    OpenAI `model`                → sobrescreve RLM_MODEL (opcional)
    OpenAI `user`                 → client_id (sessão RLM)
    OpenAI `stream`               → True = SSE, False = JSON completo
    OpenAI `max_tokens`           → ignorado (RLM tem max_iterations)
    OpenAI `temperature`          → ignorado (LLM configurado no RLM)

Autenticação:
    Authorization: Bearer {RLM_API_TOKEN}
    RLM_API_TOKEN não configurado → endpoint desabilitado.

Exemplo Python:
    from openai import OpenAI
    client = OpenAI(
        base_url="http://localhost:5000/v1",
        api_key="meu_token",   # = RLM_API_TOKEN
    )
    resp = client.chat.completions.create(
        model="rlm",
        messages=[{"role": "user", "content": "liste as vendas de hoje"}],
    )
    print(resp.choices[0].message.content)

Exemplo stream:
    for chunk in client.chat.completions.create(
        model="rlm",
        messages=[{"role": "user", "content": "faça uma análise longa"}],
        stream=True,
    ):
        print(chunk.choices[0].delta.content or "", end="", flush=True)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from rlm.core.structured_log import get_logger
from rlm.gateway.auth_helpers import token_matches, authenticate_request
from rlm.server.runtime_pipeline import build_runtime_dispatch_services, dispatch_runtime_prompt_sync

compat_log = get_logger("openai_compat")

RLM_MODEL_ID = "rlm"  # model name reportado ao cliente


# ---------------------------------------------------------------------------
# Schemas OpenAI
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str = RLM_MODEL_ID
    messages: list[ChatMessage]
    stream: bool = False
    user: str | None = None          # → client_id no RLM
    max_tokens: int | None = None    # ignorado
    temperature: float | None = None # ignorado
    n: int = 1                       # apenas 1 choice


# ---------------------------------------------------------------------------
# Builders de resposta no formato OpenAI
# ---------------------------------------------------------------------------

def _make_completion_response(
    run_id: str,
    model: str,
    content: str,
    finish_reason: str = "stop",
) -> dict:
    """Resposta JSON completa (stream=False)."""
    return {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            # RLM não contabiliza tokens nativo — retorna -1 como placeholder
            "prompt_tokens": -1,
            "completion_tokens": -1,
            "total_tokens": -1,
        },
    }


def _iter_sse_chunks(
    run_id: str,
    model: str,
    content: str,
    chunk_size: int = 20,
) -> Iterator[str]:
    """
    Gera events SSE para simular streaming.

    O RLM roda o agente de forma síncrona (uma única execução),
    então o conteúdo já está completo ao retornar. Emitimos em
    chunks para compatibilidade com clientes que esperam SSE.
    """
    created = int(time.time())

    # Role chunk
    role_chunk = {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(role_chunk)}\n\n"

    # Content chunks
    for i in range(0, len(content), chunk_size):
        piece = content[i : i + chunk_size]
        content_chunk = {
            "id": f"chatcmpl-{run_id}",
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(content_chunk)}\n\n"

    # Stop chunk
    stop_chunk = {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(stop_chunk)}\n\n"
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------

def create_openai_compat_router(expected_token: str) -> APIRouter:
    """
    Cria e retorna um APIRouter com o endpoint /v1/chat/completions.

    Args:
        expected_token: Token Bearer para autenticação.
                        Vazio → sem autenticação (inseguro, só para dev).

    Usage em api.py:
        from rlm.server.openai_compat import create_openai_compat_router
        token = os.environ.get("RLM_API_TOKEN", "")
        app.include_router(create_openai_compat_router(token), prefix="")
    """
    router = APIRouter(tags=["OpenAI-Compatible API"])

    def _auth(request: Request):
        """Authenticate via clients table → legacy token fallback.
        Returns ClientIdentity or None.

        Quando nenhum token está configurado (nem expected_token, nem env),
        retorna 503 — endpoint desabilitado por design (sem auth = inseguro).
        """
        from rlm.gateway.auth_helpers import configured_tokens as _ct

        sm = getattr(request.app.state, "session_manager", None)
        _raw = getattr(sm, "db_path", None) if sm else None
        db_path = _raw if isinstance(_raw, str) else "rlm_sessions.db"

        has_any_token = bool(expected_token) or bool(_ct("RLM_API_TOKEN"))
        if not has_any_token:
            raise HTTPException(503, "api authentication is not configured")

        return authenticate_request(
            request,
            db_path=db_path,
            env_names=("RLM_API_TOKEN",),
            scope="api",
            require=True,
            extra_legacy_tokens=(expected_token,) if expected_token else (),
        )

    def _extract_prompt(body: ChatCompletionRequest) -> str:
        """
        Extrai o prompt do último user message.
        Suporta content como string ou lista de partes (multimodal OpenAI).
        """
        for msg in reversed(body.messages):
            if msg.role == "user":
                content = msg.content
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    # multimodal — concatena partes de texto
                    parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            parts.append(str(part.get("text", "")))
                    return " ".join(parts).strip()
        # system prompt only?
        for msg in body.messages:
            if msg.role == "system" and isinstance(msg.content, str):
                return msg.content.strip()
        return ""

    @router.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionRequest, request: Request):
        """
        OpenAI-compatible chat completion endpoint.

        Mapeia a última mensagem `user` como prompt para o agente RLM.
        Suporta stream=True (SSE) e stream=False (JSON).
        """
        identity = _auth(request)

        prompt = _extract_prompt(body)
        if not prompt:
            raise HTTPException(422, "No user message found in 'messages'")

        # client_id: identity > body.user > header > UUID
        client_id = (
            (identity.client_id if identity and identity.client_id != "legacy" else "")
            or body.user
            or request.headers.get("X-Session-ID", "")
            or f"openai_compat_{uuid.uuid4().hex[:8]}"
        ).strip()

        # model reportado ao cliente
        model = body.model if body.model != "gpt" else RLM_MODEL_ID

        run_id = uuid.uuid4().hex[:12]

        compat_log.info(
            f"[openai_compat] client_id={client_id!r} model={model!r} "
            f"stream={body.stream} prompt={prompt[:60]!r}"
        )

        # --- Executar via supervisor ou runtime pipeline do daemon ---
        sm = request.app.state.session_manager
        supervisor = request.app.state.supervisor
        hooks = getattr(request.app.state, "hooks", None)
        recursion_daemon = getattr(request.app.state, "recursion_daemon", None)

        session = sm.get_or_create(client_id)

        # ── Camada 3: populate session metadata from client identity ──
        if identity and identity.client_id != "legacy":
            if identity.preferred_channel:
                session.metadata["preferred_channel"] = identity.preferred_channel
            if identity.broadcast_channels:
                session.metadata["broadcast_channels"] = identity.broadcast_channels
            if identity.context_hint:
                session.metadata["context_hint"] = identity.context_hint
            session.metadata["client_profile"] = identity.profile

        if hooks:
            hooks.trigger(
                "openai_compat.request",
                session_id=session.session_id,
                client_id=client_id,
                model=model,
            )

        import asyncio
        loop = asyncio.get_event_loop()
        try:
            if recursion_daemon is not None:
                services = build_runtime_dispatch_services(request.app.state)
                dispatch_result = await loop.run_in_executor(
                    None,
                    lambda: dispatch_runtime_prompt_sync(
                        services,
                        client_id,
                        {
                            "text": prompt,
                            "content_type": "text",
                            "metadata": {
                                "model": model,
                                "transport": "openai_compat",
                                "run_id": run_id,
                            },
                        },
                        session=session,
                        source_name="api",
                    ),
                )
                content = str(dispatch_result.get("response", "") or "")
                finish_reason = "stop" if dispatch_result.get("status") != "error" else "error"
            else:
                result = await loop.run_in_executor(
                    None,
                    lambda: supervisor.execute(session, prompt),
                )
                content = result.response or ""
                finish_reason = "stop" if result.status != "error" else "error"
        except Exception as e:
            compat_log.warn(f"[openai_compat] Execution failed: {e}")
            raise HTTPException(500, f"Agent execution failed: {e}")

        sm.update_session(session)

        if body.stream:
            return StreamingResponse(
                _iter_sse_chunks(run_id, model, content),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "X-Session-ID": session.session_id,
                },
            )

        return JSONResponse(
            content=_make_completion_response(run_id, model, content, finish_reason),
            headers={"X-Session-ID": session.session_id},
        )

    @router.get("/v1/models")
    async def list_models(request: Request):
        """
        Lista de modelos disponíveis (compatível com openai.models.list()).
        Retorna apenas o modelo RLM para identificação.
        """
        _auth(request)  # validate auth (identity not needed here)
        return {
            "object": "list",
            "data": [
                {
                    "id": RLM_MODEL_ID,
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": "rlm",
                    "permission": [],
                }
            ],
        }

    return router
