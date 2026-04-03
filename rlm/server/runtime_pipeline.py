from __future__ import annotations

import json
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from rlm.core.security.execution_policy import RuntimeExecutionPolicy, infer_runtime_execution_policy
from rlm.core.orchestration.handoff import VALID_HANDOFF_ROLES, make_handoff_fn
from rlm.core.orchestration.role_orchestrator import PENDING_HANDOFFS_KEY, orchestrate_roles
from rlm.core.session import SessionManager
from rlm.core.skillkit.skill_loader import SkillLoader
from rlm.core.skillkit.skill_telemetry import get_skill_telemetry
from rlm.core.orchestration.supervisor import ExecutionResult, RLMSupervisor, SupervisorConfig
from rlm.plugins import PluginLoader
from rlm.plugins.channel_registry import sanitize_text_payload
from rlm.runtime import RuntimeGuard
from rlm.runtime.contracts import RuntimeApprovalPort
from rlm.server.event_router import EventRouter


@dataclass(slots=True)
class RuntimeDispatchServices:
    session_manager: SessionManager
    supervisor: RLMSupervisor
    plugin_loader: PluginLoader
    event_router: EventRouter
    hooks: Any
    skill_loader: SkillLoader
    runtime_guard: RuntimeGuard | None = None
    eligible_skills: list[Any] = field(default_factory=list)
    skill_context: str = ""
    exec_approval: RuntimeApprovalPort | None = None
    exec_approval_required: bool = False


class RuntimeDispatchRejected(RuntimeError):
    def __init__(self, detail: str, *, status_code: int = 400, payload: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.payload = payload or {"error": detail}


# ---------------------------------------------------------------------------
# Memory bridge — conecta o pipeline de memória de RLMSession.chat()
# aos canais de servidor (webchat, telegram, slack, discord, whatsapp, API).
#
# O Supervisor chama session.rlm_instance.completion() diretamente,
# contornando RLMSession.chat() e portanto o budget gate + mini agent.
# Estas duas funções restauram esse comportamento sem alterar a arquitetura
# do Supervisor nem das gateways de canal.
# ---------------------------------------------------------------------------

import threading as _threading


def _prepend_memory_block(rlm_session: Any, query_text: str, prompt: Any) -> Any:
    """
    Injeta memórias relevantes de longo prazo no prompt via budget gate tripartito.

    Tenta ler do hot cache primeiro (síncrono, <1 ms). Se o cache estiver
    vazio (primeiro turno da sessão), executa busca direta via
    ``inject_memory_with_budget``.  Falha silenciosa — nunca trava o prompt.

    Returns:
        Prompt enriquecido com bloco de memória, ou o prompt original se não
        houver memórias relevantes ou se qualquer etapa falhar.
    """
    if rlm_session is None or not query_text:
        return prompt
    try:
        inject_prompt = getattr(rlm_session, "inject_memory_prompt", None)
        if callable(inject_prompt):
            return inject_prompt(prompt, query_text, available_tokens=2500)

        memory = getattr(rlm_session, "memory", None) or getattr(rlm_session, "_memory", None)
        if memory is None:
            return prompt
        session_id: str = getattr(rlm_session, "session_id", "") or getattr(rlm_session, "_session_id", "")
        memory_cache = getattr(rlm_session, "_memory_cache", None)

        cached_chunks: list = []
        if memory_cache is not None:
            try:
                cached_chunks = memory_cache.read_sync()
            except Exception:
                pass

        if cached_chunks:
            selected_chunks = cached_chunks
        else:
            from rlm.core.memory.memory_budget import inject_memory_with_budget
            selected_chunks, _ = inject_memory_with_budget(
                query=query_text,
                session_id=session_id,
                memory_manager=memory,
                available_tokens=2500,
            )

        if not selected_chunks:
            return prompt

        from rlm.core.memory.memory_budget import format_memory_block
        mem_block = format_memory_block(selected_chunks)
        if not mem_block:
            return prompt

        if isinstance(prompt, str):
            return mem_block + "\n\n" + prompt
        if isinstance(prompt, list):
            # Chat-format: insere como mensagem de sistema no início
            return [{"role": "system", "content": mem_block}] + list(prompt)
        return prompt
    except Exception:
        return prompt


def _fire_post_turn_memory(rlm_session: Any, query_text: str, response_text: str) -> None:
    """
    Dispara o mini agent de memória em daemon thread após o turno.

    Extrai nuggets memorizáveis, avalia importância, persiste no
    MultiVectorMemory e agenda atualização do hot cache para o próximo turno.
    Falha silenciosa — nunca propaga exceção nem bloqueia o chamador.
    """
    if rlm_session is None or not query_text or not response_text:
        return
    schedule_post_turn = getattr(rlm_session, "schedule_post_turn_memory", None)
    if callable(schedule_post_turn):
        schedule_post_turn(query_text, response_text)
        return
    post_turn = getattr(rlm_session, "_post_turn_async", None)
    if not callable(post_turn):
        return
    _threading.Thread(
        target=post_turn,
        args=(query_text, response_text),
        daemon=True,
        name="rlm-memory-post-turn-chan",
    ).start()


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
    content = sanitize_text_payload(content)
    try:
        record_message(role, content, metadata=body)
    except Exception:
        pass


def _collect_processed_telegram_updates(
    session_manager: Any,
    session_id: str,
    *,
    limit: int = 10,
    chat_id: str | int | None = None,
) -> list[dict[str, Any]]:
    if session_manager is None or not session_id:
        return []

    try:
        requested = int(limit)
    except (TypeError, ValueError):
        requested = 10

    if requested <= 0:
        return []

    normalized_chat_id = str(chat_id).strip() if chat_id not in (None, "") else ""
    scan_limit = max(requested * 8, 40)

    try:
        events = session_manager.get_events(session_id, limit=scan_limit)
    except Exception:
        return []

    updates: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "webhook_received":
            continue

        payload = event.get("payload") or {}
        channel = str(payload.get("client_id") or "")
        if not channel.startswith("telegram:"):
            continue

        _, _, event_chat_id = channel.partition(":")
        if normalized_chat_id and event_chat_id != normalized_chat_id:
            continue

        text = sanitize_text_payload(
            payload.get("text_preview") or payload.get("text") or payload.get("message") or ""
        )
        updates.append(
            {
                "timestamp": event.get("timestamp", ""),
                "client_id": channel,
                "chat_id": event_chat_id,
                "from_user": sanitize_text_payload(payload.get("from_user") or ""),
                "text": text,
                "payload_size": payload.get("payload_size", 0),
                "source": payload.get("channel") or payload.get("source") or "webhook",
            }
        )
        if len(updates) >= requested:
            break

    return updates


def _apply_repl_injections(
    services: RuntimeDispatchServices,
    repl_locals: dict[str, Any],
    *,
    session: Any,
    client_id: str,
    plugins_to_load: list[str],
    dynamic_skill_context: str,
    execution_policy: RuntimeExecutionPolicy,
) -> None:
    """Inject all server-mode callables into the REPL namespace.

    Uses a sentinel key ``__rlm_injected__`` to skip full re-injection on
    subsequent turns of the same session.  Only lightweight updates (skill
    context, dynamic plugins) run again.
    """
    already_injected = repl_locals.get("__rlm_injected__", False)

    if plugins_to_load:
        services.plugin_loader.inject_multiple(plugins_to_load, repl_locals)

    from rlm.plugins.channel_registry import ChannelRegistry

    # Capture originating_channel at call time for thread-safe reply routing.
    # In unified sessions, session.client_id may change between requests;
    # the parameter ``client_id`` is the actual originating channel.
    _originating_channel = client_id

    def reply(message: str) -> bool:
        delivered = ChannelRegistry.reply(_originating_channel, sanitize_text_payload(message))
        if delivered:
            session.__reply_delivered__ = True
        return delivered

    def reply_audio(text: str, voice: str = "alloy", output_format: str = "mp3") -> bool:
        return ChannelRegistry.reply_audio(
            _originating_channel,
            sanitize_text_payload(text),
            voice=voice,
            output_format=output_format,
        )

    def send_media(media_url_or_path: str, caption: str = "") -> bool:
        return ChannelRegistry.send_media(_originating_channel, media_url_or_path, sanitize_text_payload(caption))

    def telegram_get_updates(limit: int = 10, chat_id: str | int | None = None) -> list[dict[str, Any]]:
        return _collect_processed_telegram_updates(
            services.session_manager,
            getattr(session, "session_id", ""),
            limit=limit,
            chat_id=chat_id,
        )

    repl_locals["reply"] = reply
    repl_locals["reply_audio"] = reply_audio
    repl_locals["send_media"] = send_media
    repl_locals["telegram_get_updates"] = telegram_get_updates
    repl_locals["execution_policy"] = execution_policy

    # Cross-channel send — Phase 4 multichannel skill
    def cross_channel_send(target_client_id: str, message: str) -> str:
        """Envia mensagem para outro canal/destino via MessageBus.

        Args:
            target_client_id: Formato "canal:id" (ex: "telegram:12345").
            message: Texto ou JSON a enviar.

        Returns:
            "ok" se enfileirado, "error: <motivo>" caso contrário.
        """
        if ":" not in target_client_id:
            return f"error: formato inválido '{target_client_id}', esperado 'canal:id'"
        try:
            from rlm.core.comms.message_bus import get_message_bus
            from rlm.core.comms.envelope import Direction, Envelope, MessageType

            bus = get_message_bus()
            ch, tid = target_client_id.split(":", 1)
            envelope = Envelope(
                source_channel=_originating_channel.split(":")[0] if ":" in _originating_channel else "rlm",
                source_id="agent",
                source_client_id=_originating_channel,
                target_channel=ch,
                target_id=tid,
                target_client_id=target_client_id,
                direction=Direction.OUTBOUND,
                message_type=MessageType.TEXT,
                text=sanitize_text_payload(message),
            )
            bus.enqueue_outbound(envelope, session_id=getattr(session, "session_id", None))
            return "ok"
        except RuntimeError:
            # Bus não inicializado — fallback direto via ChannelRegistry
            try:
                delivered = ChannelRegistry.reply(target_client_id, sanitize_text_payload(message))
                return "ok (direct)" if delivered else "error: adapter not found or delivery failed"
            except Exception as exc:
                return f"error: {exc}"
        except Exception as exc:
            return f"error: {exc}"

    repl_locals["cross_channel_send"] = cross_channel_send

    # Session memory tools — expose conversational long-term memory to the REPL
    _rlm_session = getattr(session, "rlm_instance", None)
    if _rlm_session is not None and not already_injected:
        try:
            from rlm.tools.session_memory_tools import get_session_memory_tools
            _session_tools = get_session_memory_tools(_rlm_session)
            for _name, _fn in _session_tools.items():
                repl_locals[_name] = _fn
            if _session_tools:
                import logging as _logging
                _logging.getLogger("rlm.skill_loader").info(
                    "Session memory tools injected → %s", list(_session_tools.keys())
                )
        except Exception as _exc:
            import logging as _logging
            _logging.getLogger("rlm.skill_loader").warning(
                "Session memory tools injection failed: %s", _exc
            )

        # Knowledge Base tools — cross-session persistent memory
        try:
            from rlm.tools.kb_tools import get_kb_tools
            _kb_tools = get_kb_tools(_rlm_session)
            for _name, _fn in _kb_tools.items():
                repl_locals[_name] = _fn
            if _kb_tools:
                import logging as _logging
                _logging.getLogger("rlm.skill_loader").info(
                    "KB tools injected → %s", list(_kb_tools.keys())
                )
        except Exception as _exc:
            import logging as _logging
            _logging.getLogger("rlm.skill_loader").warning(
                "KB tools injection failed: %s", _exc
            )

        # Vault tools — Obsidian vault search / read / corrections
        try:
            if services.runtime_guard is not None:
                _vault_tools = services.runtime_guard.vaults.get_tools(_rlm_session)
            else:
                from rlm.tools.vault_tools import get_vault_tools

                _vault_tools = get_vault_tools(_rlm_session)
            for _name, _fn in _vault_tools.items():
                repl_locals[_name] = _fn
            if _vault_tools:
                import logging as _logging
                _logging.getLogger("rlm.skill_loader").info(
                    "Vault tools injected → %s", list(_vault_tools.keys())
                )
        except Exception as _exc:
            import logging as _logging
            _logging.getLogger("rlm.skill_loader").warning(
                "Vault tools injection failed: %s", _exc
            )

        # Introspection tools — self-awareness for the agent
        try:
            from rlm.tools.introspection_tools import get_introspection_tools
            _intro_tools = get_introspection_tools(session)
            for _name, _fn in _intro_tools.items():
                repl_locals[_name] = _fn
            if _intro_tools:
                import logging as _logging
                _logging.getLogger("rlm.skill_loader").info(
                    "Introspection tools injected → %s", list(_intro_tools.keys())
                )
        except Exception as _exc:
            import logging as _logging
            _logging.getLogger("rlm.skill_loader").warning(
                "Introspection tools injection failed: %s", _exc
            )

    if not already_injected:
        services.skill_loader.activate_all(
            services.eligible_skills,
            repl_locals,
            activation_scope=session.session_id,
        )
    if dynamic_skill_context:
        repl_locals["__rlm_skills__"] = dynamic_skill_context
    elif services.skill_context:
        repl_locals["__rlm_skills__"] = services.skill_context

    if not already_injected:
        services.skill_loader.inject_sif_callables(services.eligible_skills, repl_locals)
        skill_doc_fn, skill_list_fn = services.skill_loader.build_skill_doc_fn(services.eligible_skills)
        repl_locals["skill_doc"] = skill_doc_fn
        repl_locals["skill_list"] = skill_list_fn

    approval_gate = services.exec_approval
    if services.runtime_guard is not None:
        approval_gate = services.runtime_guard.approvals
    if approval_gate is not None:
        repl_locals["confirm_exec"] = approval_gate.make_repl_fn(session.session_id)

    repl_locals.setdefault(PENDING_HANDOFFS_KEY, [])

    def _handoff_task_sink(payload: dict[str, Any]) -> dict[str, Any] | None:
        _env = getattr(session.rlm_instance, "_persistent_env", None)
        create_task = getattr(_env, "create_runtime_task", None) if _env else None
        current_task_id = getattr(_env, "current_runtime_task_id", None) if _env else None
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
        task_id = handoff_task.get("task_id") if isinstance(handoff_task, Mapping) else None
        return {
            "task_id": task_id,
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

    repl_locals["__rlm_injected__"] = True


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
    execution_policy: RuntimeExecutionPolicy,
) -> dict[str, Any] | None:
    if session.rlm_instance and (dynamic_skill_context or services.skill_context):
        session.rlm_instance.skills_context = dynamic_skill_context or services.skill_context

    env = getattr(session.rlm_instance, "_persistent_env", None)
    if env is None or not hasattr(env, "locals"):
        # Env not created yet (first turn). Store a deferred injection closure
        # on the RLM core so it runs after _spawn_completion_context creates the env.
        rlm_core = getattr(getattr(session, "rlm_instance", None), "_rlm", None)
        if rlm_core is not None:
            rlm_core._pending_repl_injection = lambda locals_dict: _apply_repl_injections(
                services, locals_dict,
                session=session, client_id=client_id,
                plugins_to_load=plugins_to_load,
                dynamic_skill_context=dynamic_skill_context,
                execution_policy=execution_policy,
            )
        return None

    repl_locals = env.locals
    _apply_repl_injections(
        services, repl_locals,
        session=session, client_id=client_id,
        plugins_to_load=plugins_to_load,
        dynamic_skill_context=dynamic_skill_context,
        execution_policy=execution_policy,
    )
    return repl_locals


@contextmanager
def _temporary_root_model_override(rlm_session: Any, model_name: str | None):
    if not model_name:
        yield
        return

    rlm_core = getattr(rlm_session, "_rlm", rlm_session)
    backend_kwargs = dict(getattr(rlm_core, "backend_kwargs", None) or {})
    previous_model = backend_kwargs.get("model_name")
    if previous_model == model_name:
        yield
        return

    previous_handler = getattr(rlm_core, "_persistent_lm_handler", None)
    backend_kwargs["model_name"] = model_name
    rlm_core.backend_kwargs = backend_kwargs
    rlm_core._persistent_lm_handler = None
    try:
        yield
    finally:
        new_handler = getattr(rlm_core, "_persistent_lm_handler", None)
        if new_handler is not None and new_handler is not previous_handler:
            try:
                new_handler.stop()
            except Exception:
                pass
        restored_kwargs = dict(getattr(rlm_core, "backend_kwargs", None) or {})
        if previous_model is None:
            restored_kwargs.pop("model_name", None)
        else:
            restored_kwargs["model_name"] = previous_model
        rlm_core.backend_kwargs = restored_kwargs
        rlm_core._persistent_lm_handler = previous_handler


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
    session_obj.__reply_delivered__ = False  # reset per-request

    prepared_payload = EventRouter.preprocess_audio(client_id, dict(payload))
    prompt, plugins_to_load = services.event_router.route(client_id, prepared_payload)
    query_text = _extract_query_text(prompt).strip()

    # Sanitize: lone surrogates (e.g. from terminal locale mismatch) break
    # JSON serialization in the OpenAI client (ensure_ascii=False path).
    # Replace them early so every downstream consumer sees clean UTF-8 strings.
    from rlm.plugins.channel_registry import sanitize_text_payload as _sanitize_input
    query_text = _sanitize_input(query_text)
    if isinstance(prompt, str):
        prompt = _sanitize_input(prompt)

    if query_text:
        if services.runtime_guard is not None:
            threat = services.runtime_guard.security.audit_input(
                query_text,
                session_id=session_obj.session_id,
            )
        else:
            from rlm.core.security import auditor as security_auditor

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
    root_model = None
    if session_obj.rlm_instance is not None:
        root_model = ((getattr(getattr(session_obj.rlm_instance, "_rlm", None), "backend_kwargs", None) or {}).get("model_name"))
    if services.runtime_guard is not None:
        execution_policy = services.runtime_guard.policy.infer_runtime_execution_policy(
            query_text,
            client_id=client_id,
            prompt_plan=prompt_plan,
            default_model=str(root_model) if root_model is not None else None,
        )
    else:
        execution_policy = infer_runtime_execution_policy(
            query_text,
            client_id=client_id,
            prompt_plan=prompt_plan,
            default_model=str(root_model) if root_model is not None else None,
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
                "execution_policy": {
                    "task_class": execution_policy.task_class,
                    "allow_recursion": execution_policy.allow_recursion,
                    "allow_role_orchestrator": execution_policy.allow_role_orchestrator,
                    "max_iterations_override": execution_policy.max_iterations_override,
                    "root_model_override": execution_policy.root_model_override,
                },
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
        execution_policy=execution_policy,
    )

    # --- Memory bridge: injeta memórias de longo prazo no prompt -----------
    # Ativa o budget gate tripartito para TODOS os canais (webchat, telegram,
    # slack, discord, whatsapp, REST API, TUI) sem alterar o Supervisor nem
    # as gateways de canal. RLMSession.chat() faz isso internamente; aqui
    # replicamos apenas a injeção, mantendo o Supervisor intacto.
    _rlm_session = getattr(session_obj, "rlm_instance", None)
    if query_text:
        prompt = _prepend_memory_block(_rlm_session, query_text, prompt)
    # -----------------------------------------------------------------------

    if record_conversation and query_text:
        _record_recursive_message(session_obj, "user", query_text, origin=source_name)

    dispatch_operation_id = services.session_manager.log_operation(
        session_obj.session_id,
        "runtime.dispatch",
        phase="started",
        status="running",
        source=source_name,
        payload={
            "client_id": client_id,
            "originating_channel": client_id,
            "delivery_context": getattr(session_obj, "delivery_context", {}),
            "query_preview": query_text[:200],
        },
    )

    services.hooks.trigger(
        "completion.started",
        session_id=session_obj.session_id,
        context={"client_id": client_id, "source": source_name},
    )

    rlm_core = getattr(getattr(session_obj, "rlm_instance", None), "_rlm", None)
    if rlm_core is not None:
        rlm_core._runtime_execution_policy = execution_policy
        # Multichannel: inject originating channel into env kwargs and live env
        _ekw = getattr(rlm_core, "environment_kwargs", None)
        if _ekw is None:
            try:
                rlm_core.environment_kwargs = {"_originating_channel": client_id}
            except AttributeError:
                pass  # mock / read-only object
        else:
            _ekw["_originating_channel"] = client_id
        _live_env = getattr(rlm_core, "_persistent_env", None)
        if _live_env is not None:
            _live_env._originating_channel = client_id
            _live_ctx = getattr(getattr(_live_env, "_memory", None), "_agent_context", None)
            if _live_ctx is not None:
                _live_ctx.channel = client_id

    def _execute_with_skill_context() -> ExecutionResult:
        skill_ctx_tokens = services.skill_loader.set_request_context(
            session_id=session_obj.session_id,
            client_id=client_id,
            query=query_text,
        )
        try:
            config = None
            if execution_policy.max_iterations_override is not None:
                default_cfg = services.supervisor.default_config
                config = SupervisorConfig(
                    max_execution_time=default_cfg.max_execution_time,
                    max_consecutive_errors=default_cfg.max_consecutive_errors,
                    max_iterations_override=execution_policy.max_iterations_override,
                    poll_interval=default_cfg.poll_interval,
                )
            with _temporary_root_model_override(session_obj.rlm_instance, execution_policy.root_model_override):
                return services.supervisor.execute(session_obj, prompt, config=config, root_prompt=query_text or None)
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
        services.session_manager.log_operation(
            session_obj.session_id,
            "runtime.dispatch",
            phase="finished",
            status="error",
            source=source_name,
            operation_id=dispatch_operation_id,
            payload={
                "error": str(exc),
                "client_id": client_id,
            },
        )
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

    if (
        result.status == "completed"
        and session_obj.rlm_instance is not None
        and repl_locals is not None
        and execution_policy.allow_role_orchestrator
    ):
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
    elif result.status == "completed" and not execution_policy.allow_role_orchestrator:
        services.session_manager.log_event(
            session_obj.session_id,
            "agent_role_summary_skipped",
            {
                "reason": execution_policy.note,
                "task_class": execution_policy.task_class,
            },
        )

    # --- Memory bridge: persiste nuggets do turno em background ------------
    # Roda após role_orchestrator para capturar a resposta final (pode ter
    # sido modificada por handoff/escalation). Daemon thread — nunca bloqueia.
    if result.status == "completed" and query_text and result.response:
        _fire_post_turn_memory(_rlm_session, query_text, result.response)
    # -----------------------------------------------------------------------

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
    services.session_manager.log_operation(
        session_obj.session_id,
        "runtime.dispatch",
        phase="finished",
        status=result.status,
        source=source_name,
        operation_id=dispatch_operation_id,
        payload={
            "execution_time": result.execution_time,
            "client_id": client_id,
            "response_preview": (result.response or result.error_detail or result.abort_reason or "")[:200],
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
        "already_replied": getattr(session_obj, "__reply_delivered__", False),
    }
    if on_complete is not None:
        on_complete(payload_result, session_obj)
    return payload_result