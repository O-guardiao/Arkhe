from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from rlm.core.exec_approval import ExecApprovalGate
from rlm.core.handoff import VALID_HANDOFF_ROLES, make_handoff_fn
from rlm.core.role_orchestrator import PENDING_HANDOFFS_KEY, orchestrate_roles
from rlm.core.session import SessionManager
from rlm.core.skill_loader import SkillLoader
from rlm.core.skill_telemetry import get_skill_telemetry
from rlm.core.supervisor import ExecutionResult, RLMSupervisor
from rlm.plugins import PluginLoader
from rlm.server.event_router import EventRouter


@dataclass(slots=True)
class RuntimeDispatchServices:
    session_manager: SessionManager
    supervisor: RLMSupervisor
    plugin_loader: PluginLoader
    event_router: EventRouter
    hooks: Any
    skill_loader: SkillLoader
    eligible_skills: list[Any] = field(default_factory=list)
    skill_context: str = ""
    exec_approval: ExecApprovalGate | None = None
    exec_approval_required: bool = False


class RuntimeDispatchRejected(RuntimeError):
    def __init__(self, detail: str, *, status_code: int = 400, payload: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.payload = payload or {"error": detail}


def _extract_query_text(prompt: str | list[dict[str, Any]] | dict[str, Any]) -> str:
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        return " ".join(part.get("text", "") for part in prompt if isinstance(part, dict))
    if isinstance(prompt, dict):
        return str(prompt.get("text", "") or prompt.get("message", ""))
    return str(prompt)


def _record_recursive_message(session: Any, role: str, content: str, *, origin: str, metadata: dict[str, Any] | None = None) -> None:
    record_message = getattr(getattr(session, "rlm_instance", None), "_record_recursive_message", None)
    if not callable(record_message):
        return
    body = dict(metadata or {})
    body.setdefault("source", origin)
    try:
        record_message(role, content, metadata=body)
    except Exception:
        pass


def _prepare_repl_locals(
    services: RuntimeDispatchServices,
    *,
    session: Any,
    client_id: str,
    prompt: str | list[dict[str, Any]] | dict[str, Any],
    query_text: str,
    plugins_to_load: list[str],
    prompt_plan: Any,
    dynamic_skill_context: str,
) -> dict[str, Any] | None:
    if session.rlm_instance and (dynamic_skill_context or services.skill_context):
        session.rlm_instance.skills_context = dynamic_skill_context or services.skill_context

    env = getattr(session.rlm_instance, "_persistent_env", None)
    if env is None or not hasattr(env, "locals"):
        return None

    repl_locals = env.locals
    if plugins_to_load:
        services.plugin_loader.inject_multiple(plugins_to_load, repl_locals)

    from rlm.plugins.channel_registry import ChannelRegistry

    def reply(message: str) -> bool:
        return ChannelRegistry.reply(session.client_id, message)

    def reply_audio(text: str, voice: str = "alloy", output_format: str = "mp3") -> bool:
        return ChannelRegistry.reply_audio(session.client_id, text, voice=voice, output_format=output_format)

    def send_media(media_url_or_path: str, caption: str = "") -> bool:
        return ChannelRegistry.send_media(session.client_id, media_url_or_path, caption)

    repl_locals["reply"] = reply
    repl_locals["reply_audio"] = reply_audio
    repl_locals["send_media"] = send_media

    services.skill_loader.activate_all(
        services.eligible_skills,
        repl_locals,
        activation_scope=session.session_id,
    )
    if dynamic_skill_context:
        repl_locals["__rlm_skills__"] = dynamic_skill_context
    elif services.skill_context:
        repl_locals["__rlm_skills__"] = services.skill_context

    services.skill_loader.inject_sif_callables(services.eligible_skills, repl_locals)
    skill_doc_fn, skill_list_fn = services.skill_loader.build_skill_doc_fn(services.eligible_skills)
    repl_locals["skill_doc"] = skill_doc_fn
    repl_locals["skill_list"] = skill_list_fn

    approval_gate = services.exec_approval
    if approval_gate is not None:
        repl_locals["confirm_exec"] = approval_gate.make_repl_fn(session.session_id)

    repl_locals.setdefault(PENDING_HANDOFFS_KEY, [])

    def _handoff_task_sink(payload: dict[str, Any]) -> dict[str, Any] | None:
        create_task = getattr(env, "create_runtime_task", None)
        current_task_id = getattr(env, "current_runtime_task_id", None)
        if not callable(create_task):
            return None
        parent_task_id = current_task_id() if callable(current_task_id) else None
        if parent_task_id is None:
            return None
        handoff_task = create_task(
            f"[handoff:{payload.get('target_role', 'unknown')}] {str(payload.get('remaining_goal', ''))[:120]}",
            parent_task_id=parent_task_id,
            status="in-progress",
            metadata={
                "mode": "handoff",
                "target_role": payload.get("target_role", ""),
                "reason": payload.get("reason", ""),
                "suggested_mode": payload.get("suggested_mode", ""),
            },
            current=False,
        )
        return {
            "task_id": handoff_task.get("task_id"),
            "parent_task_id": parent_task_id,
        }

    repl_locals["request_handoff"] = make_handoff_fn(
        session_id=session.session_id,
        log_event=services.session_manager.log_event,
        hooks=services.hooks,
        telemetry=get_skill_telemetry(),
        client_id=client_id,
        state_sink=lambda payload: repl_locals[PENDING_HANDOFFS_KEY].append(dict(payload)),
        task_sink=_handoff_task_sink,
    )
    repl_locals["handoff_roles"] = list(VALID_HANDOFF_ROLES)
    return repl_locals


def dispatch_runtime_prompt_sync(
    services: RuntimeDispatchServices,
    client_id: str,
    payload: dict[str, Any],
    *,
    session: Any | None = None,
    record_conversation: bool = False,
    source_name: str = "runtime",
    on_complete: Callable[[dict[str, Any], Any], None] | None = None,
) -> dict[str, Any]:
    session_obj = session or services.session_manager.get_or_create(client_id)

    prepared_payload = EventRouter.preprocess_audio(client_id, dict(payload))
    prompt, plugins_to_load = services.event_router.route(client_id, prepared_payload)
    query_text = _extract_query_text(prompt).strip()

    from rlm.core.security import auditor as security_auditor

    if query_text:
        threat = security_auditor.audit_input(query_text, session_id=session_obj.session_id)
        if threat.threat_level == "high":
            services.session_manager.log_event(
                session_obj.session_id,
                "security_threat_high",
                {
                    "patterns": threat.patterns_found,
                    "text_preview": query_text[:200],
                },
            )
            raise RuntimeDispatchRejected(
                "Input rejected by security policy.",
                status_code=400,
                payload={
                    "error": "Input rejected by security policy.",
                    "threat_level": threat.threat_level,
                    "patterns": threat.patterns_found,
                },
            )
        if threat.is_suspicious:
            services.session_manager.log_event(
                session_obj.session_id,
                "security_threat_low",
                {
                    "patterns": threat.patterns_found,
                    "level": threat.threat_level,
                },
            )

    prompt_plan = services.skill_loader.plan_prompt_context(services.eligible_skills, query=query_text, mode="auto")
    dynamic_skill_context = services.skill_loader.build_system_prompt_context(
        services.eligible_skills,
        query=query_text,
        mode="auto",
    )
    if query_text:
        estimate = services.skill_loader.estimate_tokens(services.eligible_skills, query=query_text)
        ranked_payload = [
            {
                "name": rank.skill.name,
                "score": rank.score,
                "telemetry": rank.telemetry_score,
                "trace": rank.trace_score,
                "cost_penalty": rank.cost_penalty,
                "risk_penalty": rank.risk_penalty,
            }
            for rank in prompt_plan.ranked_skills[:8]
        ]
        blocked_payload = [
            {
                "name": availability.skill.name,
                "reasons": availability.reasons,
            }
            for availability in prompt_plan.blocked_skills[:8]
        ]
        services.session_manager.log_event(
            session_obj.session_id,
            "skill_routing",
            {
                "query": query_text[:300],
                "effective_mode": prompt_plan.effective_mode,
                "selected_skills": [skill.name for skill in prompt_plan.expanded_skills],
                "ranked_skills": ranked_payload,
                "blocked_skills": blocked_payload,
                "estimate": estimate,
            },
        )
        get_skill_telemetry().record_routing(
            mode=prompt_plan.effective_mode,
            query=query_text,
            ranked_skills=ranked_payload,
            selected_skills=[skill.name for skill in prompt_plan.expanded_skills],
            blocked_skills=blocked_payload,
            session_id=session_obj.session_id,
            client_id=client_id,
        )

    repl_locals = _prepare_repl_locals(
        services,
        session=session_obj,
        client_id=client_id,
        prompt=prompt,
        query_text=query_text,
        plugins_to_load=plugins_to_load,
        prompt_plan=prompt_plan,
        dynamic_skill_context=dynamic_skill_context,
    )

    if record_conversation and query_text:
        _record_recursive_message(session_obj, "user", query_text, origin=source_name)

    services.hooks.trigger(
        "completion.started",
        session_id=session_obj.session_id,
        context={"client_id": client_id, "source": source_name},
    )

    def _execute_with_skill_context() -> ExecutionResult:
        skill_ctx_tokens = services.skill_loader.set_request_context(
            session_id=session_obj.session_id,
            client_id=client_id,
            query=query_text,
        )
        try:
            return services.supervisor.execute(session_obj, prompt)
        finally:
            services.skill_loader.clear_request_context(skill_ctx_tokens)

    try:
        result = _execute_with_skill_context()
    except Exception as exc:
        services.hooks.trigger(
            "completion.aborted",
            session_id=session_obj.session_id,
            context={"error": str(exc), "source": source_name},
        )
        services.session_manager.log_event(session_obj.session_id, "execution_error", {"error": str(exc), "source": source_name})
        error_payload = {
            "status": "error",
            "session_id": session_obj.session_id,
            "response": None,
            "execution_time": 0.0,
            "abort_reason": None,
            "error_detail": str(exc),
        }
        if record_conversation:
            _record_recursive_message(session_obj, "assistant", f"[error] {exc}", origin=source_name, metadata={"status": "error"})
        services.session_manager.update_session(session_obj)
        if on_complete is not None:
            on_complete(error_payload, session_obj)
        raise

    services.hooks.trigger(
        "completion.finished",
        session_id=session_obj.session_id,
        context={
            "status": result.status,
            "execution_time": result.execution_time,
            "source": source_name,
        },
    )

    if result.status == "completed" and session_obj.rlm_instance is not None and repl_locals is not None:
        role_outcome = orchestrate_roles(
            rlm=session_obj.rlm_instance,
            prompt=query_text or str(prompt),
            response=result.response or "",
            prompt_plan=prompt_plan,
            repl_locals=repl_locals,
            log_event=services.session_manager.log_event,
            session_id=session_obj.session_id,
            hooks=services.hooks,
        )
        result.response = role_outcome.response
        if role_outcome.steps:
            services.session_manager.log_event(
                session_obj.session_id,
                "agent_role_summary",
                {
                    "steps": role_outcome.steps,
                    "escalated": role_outcome.escalated,
                    "retried": role_outcome.retried,
                },
            )

    services.session_manager.update_session(session_obj)
    services.session_manager.log_event(
        session_obj.session_id,
        "execution_complete",
        {
            "status": result.status,
            "execution_time": result.execution_time,
            "source": source_name,
        },
    )

    response_text = result.response or ""
    error_text = result.error_detail or result.abort_reason or ""
    if record_conversation and (response_text or error_text):
        assistant_text = response_text if response_text else f"[{result.status}] {error_text}"
        _record_recursive_message(
            session_obj,
            "assistant",
            assistant_text,
            origin=source_name,
            metadata={"status": result.status},
        )

    payload_result = {
        "status": result.status,
        "session_id": session_obj.session_id,
        "response": result.response,
        "execution_time": round(result.execution_time, 2),
        "abort_reason": result.abort_reason,
        "error_detail": result.error_detail,
    }
    if on_complete is not None:
        on_complete(payload_result, session_obj)
    return payload_result