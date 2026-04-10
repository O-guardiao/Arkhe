"""Fábrica do runtime local para o workbench TUI.

Usa ``bootstrap_channel_infrastructure()`` para inicializar a mesma
infraestrutura multichannel que o servidor (CSR, MessageBus, adapters).
Assim, qualquer canal futuro funciona automaticamente no modo embedded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rlm.daemon import RecursionDaemon
from rlm.core.comms.channel_bootstrap import (
    ChannelInfrastructure,
    bootstrap_channel_infrastructure,
)
from rlm.core.engine.hooks import HookSystem
from rlm.core.orchestration.scheduler import RLMScheduler
from rlm.core.orchestration.supervisor import RLMSupervisor, SupervisorConfig
from rlm.core.security.execution_policy import build_tier_backends
from rlm.core.session import SessionManager
from rlm.core.skillkit.skill_loader import SkillLoader
from rlm.plugins import PluginLoader
from rlm.runtime import build_runtime_guard_from_env
from rlm.server.event_router import EventRouter
from rlm.server.runtime_pipeline import RuntimeDispatchServices
from rlm.server.ws_server import RLMEventBus


@dataclass(slots=True)
class WorkbenchRuntime:
    session_manager: SessionManager
    supervisor: RLMSupervisor
    dispatch_services: RuntimeDispatchServices | None = None
    channel_infra: ChannelInfrastructure | None = None
    recursion_daemon: RecursionDaemon | None = None
    scheduler: RLMScheduler | None = None

    def close(self) -> None:
        if self.scheduler is not None:
            self.scheduler.stop()
        self.session_manager.close_all()
        if self.dispatch_services is not None:
            self.dispatch_services.skill_loader.deactivate_all()
        if self.channel_infra is not None:
            self.channel_infra.close()
        if self.recursion_daemon is not None:
            self.recursion_daemon.stop()
        self.supervisor.shutdown()


def build_local_workbench_runtime() -> WorkbenchRuntime:
    event_bus = RLMEventBus()
    db_path = os.environ.get("RLM_DB_PATH", "rlm_sessions.db")
    _rlm_backend = os.environ.get("RLM_BACKEND", "openai")
    _rlm_backend_kwargs = {"model_name": os.environ.get("RLM_MODEL_PLANNER", os.environ.get("RLM_MODEL", "gpt-4o-mini"))}
    _tier_backends, _tier_kwargs = build_tier_backends(_rlm_backend, _rlm_backend_kwargs)
    session_manager = SessionManager(
        db_path=db_path,
        state_root=os.environ.get("RLM_STATE_ROOT", "./rlm_states"),
        default_rlm_kwargs={
            "backend": _rlm_backend,
            "backend_kwargs": _rlm_backend_kwargs,
            "environment": "local",
            "max_depth": int(os.environ.get("RLM_MAX_DEPTH", "3")),
            "max_iterations": int(os.environ.get("RLM_MAX_ITERATIONS", "30")),
            "persistent": True,
            "verbose": True,
            "event_bus": event_bus,
            "other_backends": _tier_backends or None,
            "other_backend_kwargs": _tier_kwargs or None,
        },
    )
    supervisor = RLMSupervisor(
        default_config=SupervisorConfig(
            max_execution_time=int(os.environ.get("RLM_TIMEOUT", "120")),
            max_consecutive_errors=int(os.environ.get("RLM_MAX_ERRORS", "5")),
        )
    )
    plugin_loader = PluginLoader()
    event_router = EventRouter()
    hooks = HookSystem()
    skill_loader = SkillLoader()
    skills_dir = os.environ.get(
        "RLM_SKILLS_DIR",
        str(Path(__file__).resolve().parents[2] / "skills"),
    )
    all_skills = skill_loader.load_from_dir(skills_dir)
    eligible_skills = skill_loader.filter_eligible(all_skills)
    skill_context = skill_loader.build_system_prompt_context(eligible_skills, mode="compact")

    def _deactivate_scope_on_close(session: Any) -> None:
        skill_loader.deactivate_scope(session.session_id)

    session_manager.add_close_callback(_deactivate_scope_on_close)
    runtime_guard = build_runtime_guard_from_env()
    recursion_daemon = RecursionDaemon(event_bus=event_bus)
    recursion_daemon.start()
    session_manager.set_recursion_daemon(recursion_daemon)
    recursion_daemon.attach_session_manager(session_manager)
    dispatch_services = RuntimeDispatchServices(
        session_manager=session_manager,
        supervisor=supervisor,
        plugin_loader=plugin_loader,
        event_router=event_router,
        hooks=hooks,
        skill_loader=skill_loader,
        runtime_guard=runtime_guard,
        eligible_skills=eligible_skills,
        skill_context=skill_context,
        exec_approval=runtime_guard.approvals,
        exec_approval_required=runtime_guard.exec_approval_required,
        recursion_daemon=recursion_daemon,
    )

    def _run_scheduled_job(client_id: str, prompt: str) -> dict[str, object]:
        session = session_manager.get_or_create(client_id)
        result = supervisor.execute(session, prompt)
        return {
            "status": result.status,
            "response": str(result.response or ""),
            "session_id": result.session_id,
            "execution_time": result.execution_time,
            "iterations_used": result.iterations_used,
            "error_detail": result.error_detail,
        }

    scheduler = RLMScheduler(
        execute_fn=lambda client_id, prompt: recursion_daemon.dispatch_scheduled_sync(
            client_id=client_id,
            prompt=prompt,
            fallback=lambda event: _run_scheduled_job(event.client_id, event.text),
        )
    )
    scheduler.start()
    recursion_daemon.attach_scheduler(scheduler)

    # ── Multichannel bootstrapping ────────────────────────────────────
    # Mesmo bootstrap do api.py lifespan → TUI local ganha CSR,
    # MessageBus, ChannelRegistry e adapter registration.
    # start_gateways=False: sem server HTTP, gateways ativos (Telegram
    # polling) não funcionariam — mas os canais ficam registrados no CSR.
    channel_infra = bootstrap_channel_infrastructure(
        session_manager=session_manager,
        event_bus=event_bus,
        db_path=db_path,
        start_gateways=False,
    )
    recursion_daemon.attach_channel_runtime(
        channel_status_registry=channel_infra.csr,
        message_bus=channel_infra.message_bus,
        outbox=channel_infra.outbox,
        delivery_worker=channel_infra.delivery_worker,
    )

    return WorkbenchRuntime(
        session_manager=session_manager,
        supervisor=supervisor,
        dispatch_services=dispatch_services,
        channel_infra=channel_infra,
        recursion_daemon=recursion_daemon,
        scheduler=scheduler,
    )
