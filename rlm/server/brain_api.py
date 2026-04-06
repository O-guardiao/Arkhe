"""Contrato do brain para a fronteira Gateway TypeScript ↔ Python."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from rlm.server.envelope import Envelope, create_reply_envelope


class BrainAPI(Protocol):
    async def dispatch_prompt(self, envelope: Envelope) -> Envelope: ...


def _build_services(app_state: Any) -> Any:
    """Constrói RuntimeDispatchServices a partir do estado do app FastAPI."""
    from rlm.server.runtime_pipeline import RuntimeDispatchServices

    return RuntimeDispatchServices(
        session_manager=app_state.session_manager,
        supervisor=app_state.supervisor,
        plugin_loader=app_state.plugin_loader,
        event_router=app_state.event_router,
        hooks=app_state.hooks,
        skill_loader=app_state.skill_loader,
        runtime_guard=getattr(app_state, "runtime_guard", None),
        eligible_skills=getattr(app_state, "skills_eligible", []),
        skill_context=getattr(app_state, "skill_context", ""),
        exec_approval=getattr(app_state, "exec_approval", None),
        exec_approval_required=getattr(app_state, "exec_approval_required", False),
    )


def _envelope_to_payload(envelope: Envelope) -> dict[str, Any]:
    meta = dict(envelope.metadata or {})
    return {
        "text": envelope.text,
        "from_user": meta.get("from_user", envelope.source_id),
        "channel": envelope.source_channel,
        "content_type": envelope.message_type,
        "envelope_id": envelope.id,
        "correlation_id": envelope.correlation_id,
        "timestamp": envelope.timestamp,
        "channel_meta": meta,
    }


class RuntimePipelineBrainAPI:
    def __init__(self, app_state: Any, *, dispatch_timeout_s: float) -> None:
        self._app_state = app_state
        self._dispatch_timeout_s = dispatch_timeout_s

    async def dispatch_prompt(self, envelope: Envelope) -> Envelope:
        from rlm.server.runtime_pipeline import (
            RuntimeDispatchRejected,
            dispatch_runtime_prompt_sync,
        )

        services = _build_services(self._app_state)
        client_id = envelope.source_client_id
        payload = _envelope_to_payload(envelope)
        loop = asyncio.get_event_loop()
        start_ms = time.monotonic() * 1000

        try:
            result: dict[str, Any] = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    dispatch_runtime_prompt_sync,
                    services,
                    client_id,
                    payload,
                ),
                timeout=self._dispatch_timeout_s,
            )
        except RuntimeDispatchRejected as exc:
            result = {"reply": exc.detail, "error": True}
        except asyncio.TimeoutError:
            result = {"reply": "Timeout: brain não respondeu a tempo.", "error": True}
        except Exception as exc:  # noqa: BLE001
            result = {"reply": f"Erro interno: {exc}", "error": True}

        elapsed_ms = time.monotonic() * 1000 - start_ms
        reply_text = str(result.get("reply") or result.get("response") or "")
        reply_meta: dict[str, Any] = {
            "elapsed_ms": elapsed_ms,
            "brain_session_id": result.get("session_id", ""),
        }
        if result.get("error"):
            reply_meta["error"] = True

        return create_reply_envelope(
            inbound=envelope,
            reply_text=reply_text,
            metadata=reply_meta,
        )