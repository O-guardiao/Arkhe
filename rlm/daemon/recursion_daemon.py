from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Callable, cast

from rlm.daemon.channel_subagents import ChannelSubAgent, create_channel_subagent
from rlm.daemon.contracts import (
    ChannelEvent,
    DaemonSessionState,
    DaemonTaskRequest,
    DaemonTaskResult,
    DispatchClass,
    EventPriority,
    RecursionResult,
    TaskDispatchRoute,
)
from rlm.daemon.llm_gate import LLMGate
from rlm.daemon.memory_access import DaemonMemoryAccess
from rlm.daemon.task_agents import EvaluatorTaskAgent, PlannerTaskAgent, TaskAgentRouter, TextWorkerTaskAgent
from rlm.daemon.warm_runtime import WarmRuntimePool


DispatchHandler = Callable[[ChannelEvent], dict[str, Any]]
TaskFallbackHandler = Callable[[DaemonTaskRequest], DaemonTaskResult]


class RecursionDaemon:
    """Daemon central de runtime para recursao persistente.

    Nesta primeira entrega ele funciona como a camada de despacho e
    classificacao central, sem substituir ainda o pipeline existente.
    O comportamento atual continua atras do fallback handler.
    """

    def __init__(
        self,
        *,
        name: str = "main",
        event_bus: Any | None = None,
        llm_gate: LLMGate | None = None,
        warm_runtime_pool: WarmRuntimePool | None = None,
        task_router: TaskAgentRouter | None = None,
        evaluator_agent: EvaluatorTaskAgent | None = None,
        planner_agent: PlannerTaskAgent | None = None,
        text_worker_agent: TextWorkerTaskAgent | None = None,
        memory_access: DaemonMemoryAccess | None = None,
    ) -> None:
        self.name = name
        self._event_bus = event_bus
        self._llm_gate = llm_gate or LLMGate()
        self._warm_runtime_pool = warm_runtime_pool or WarmRuntimePool()
        self._task_router = task_router or TaskAgentRouter()
        self._evaluator_agent = evaluator_agent or EvaluatorTaskAgent()
        self._planner_agent = planner_agent or PlannerTaskAgent()
        self._text_worker_agent = text_worker_agent or TextWorkerTaskAgent()
        self._memory_access = memory_access or DaemonMemoryAccess()
        self._lock = threading.RLock()
        self._dispatch_condition = threading.Condition(self._lock)
        self._running = False
        self._ready = False
        self._draining = False
        self._inflight_dispatches = 0
        self._attached_channels: dict[str, set[str]] = {}
        self._dispatch_handler: DispatchHandler | None = None
        # Always-on environment: daemon holds references so the REPL and
        # LMHandler survive session lifecycle.  When the session that
        # created them is garbage-collected, the daemon keeps the objects
        # alive — embodying the paper's persistent REPL concept (ℰ).
        self._live_env: Any | None = None
        self._live_lm_handler: Any | None = None
        self._live_rlm_core: Any | None = None
        self._live_session_id: str = ""
        self._live_client_id: str = ""
        self._stats: dict[str, int] = {
            "received": 0,
            "dispatched": 0,
            "deterministic": 0,
            "llm_required": 0,
            "task_agent_required": 0,
            "reject": 0,
            "llm_invoked": 0,
            "deterministic_used": 0,
            "task_agent_invoked": 0,
            "maintenance_runs": 0,
            "sessions_pruned": 0,
        }
        self._latency_ns: dict[str, list[int]] = {
            "dispatch_sync": [],
            "dispatch_task_sync": [],
            "deterministic": [],
            "llm_required": [],
            "task_agent_required": [],
        }
        self._last_dispatch_result: RecursionResult | None = None
        self._scheduler: Any | None = None
        self._session_manager: Any | None = None
        self._channel_status_registry: Any | None = None
        self._message_bus: Any | None = None
        self._outbox: Any | None = None
        self._delivery_worker: Any | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def is_draining(self) -> bool:
        return self._draining

    @property
    def last_dispatch_result(self) -> RecursionResult | None:
        return self._last_dispatch_result

    @property
    def scheduler(self) -> Any | None:
        return self._scheduler

    @property
    def session_manager(self) -> Any | None:
        return self._session_manager

    @property
    def memory_access(self) -> DaemonMemoryAccess:
        return self._memory_access

    def attach_scheduler(self, scheduler: Any) -> None:
        self._scheduler = scheduler
        self._emit("recursion_daemon.scheduler_attached", {"name": self.name})

    def attach_session_manager(self, session_manager: Any) -> None:
        self._session_manager = session_manager
        self._emit("recursion_daemon.session_manager_attached", {"name": self.name})
        self.bootstrap_session()

    def attach_channel_runtime(
        self,
        *,
        channel_status_registry: Any | None = None,
        message_bus: Any | None = None,
        outbox: Any | None = None,
        delivery_worker: Any | None = None,
    ) -> None:
        self._channel_status_registry = channel_status_registry
        self._message_bus = message_bus
        self._outbox = outbox
        self._delivery_worker = delivery_worker
        self._emit(
            "recursion_daemon.channel_runtime_attached",
            {
                "name": self.name,
                "channel_status_registry": channel_status_registry is not None,
                "message_bus": message_bus is not None,
                "outbox": outbox is not None,
                "delivery_worker": delivery_worker is not None,
            },
        )

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._draining = False
            self._ready = False
            self._emit("recursion_daemon.started", {"name": self.name})
            self._ready = True
            self._emit("recursion_daemon.ready", {"name": self.name})
        self.bootstrap_session()

    def stop(self, *, timeout_s: float = 1.0) -> None:
        detached_channels: list[tuple[str, str]] = []
        with self._lock:
            if not self._running:
                return
            if not self._draining:
                self._draining = True
                self._ready = False
                self._emit(
                    "recursion_daemon.shutdown_drain_started",
                    {"name": self.name, "inflight_dispatches": self._inflight_dispatches},
                )

            deadline = time.time() + max(0.0, timeout_s)
            while self._inflight_dispatches > 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._dispatch_condition.wait(timeout=remaining)

            for channel, client_ids in list(self._attached_channels.items()):
                for client_id in sorted(client_ids):
                    detached_channels.append((channel, client_id))
            self._attached_channels.clear()
            self._running = False
            self._ready = False
            self._draining = False
            # Release always-on environment references on shutdown.
            self._live_env = None
            self._live_lm_handler = None
            self._live_rlm_core = None
            self._live_session_id = ""
            self._live_client_id = ""
            snapshot = self.snapshot()
            self._emit("recursion_daemon.stopped", snapshot)

        for channel, client_id in detached_channels:
            self._emit(
                "recursion_daemon.channel_detached",
                {"channel": channel, "client_id": client_id},
            )
        self._emit("recursion_daemon.shutdown_complete", snapshot)

    def register_dispatch_handler(self, handler: DispatchHandler) -> None:
        with self._lock:
            self._dispatch_handler = handler

    def snapshot(self) -> dict[str, Any]:
        outbox_snapshot = self._outbox_snapshot()
        channel_runtime = self._channel_runtime_snapshot()
        active_sessions = len(self._list_active_sessions())
        with self._lock:
            latency_summary: dict[str, dict[str, float]] = {}
            for key, samples in self._latency_ns.items():
                if samples:
                    sorted_s = sorted(samples)
                    count = len(sorted_s)
                    latency_summary[key] = {
                        "count": count,
                        "mean_ms": round(sum(sorted_s) / count / 1_000_000, 3),
                        "p50_ms": round(sorted_s[count // 2] / 1_000_000, 3),
                        "p95_ms": round(sorted_s[min(int(count * 0.95), count - 1)] / 1_000_000, 3),
                        "max_ms": round(sorted_s[-1] / 1_000_000, 3),
                    }
            return {
                "name": self.name,
                "running": self._running,
                "ready": self._ready,
                "draining": self._draining,
                "inflight_dispatches": self._inflight_dispatches,
                "stats": dict(self._stats),
                "latency": latency_summary,
                "attached_channels": {
                    channel: len(client_ids) for channel, client_ids in self._attached_channels.items()
                },
                "active_sessions": active_sessions,
                "channel_runtime": channel_runtime,
                "outbox": outbox_snapshot,
                "warm_runtime": self._warm_runtime_pool.snapshot(),
                "memory_access": self._memory_access.snapshot(),
                "live_env_active": self._live_env is not None,
                "live_lm_handler_active": self._live_lm_handler is not None,
                "live_session_id": self._live_session_id,
                "live_client_id": self._live_client_id,
                "scheduler_attached": self._scheduler is not None,
                "session_manager_attached": self._session_manager is not None,
            }

    def inject_memory_prompt(
        self,
        rlm_session: Any,
        query_text: str,
        prompt: Any,
        *,
        session: Any | None = None,
    ) -> Any:
        return self._memory_access.inject_prompt(
            rlm_session,
            query_text,
            prompt,
            session=session,
        )

    def record_post_turn_memory(
        self,
        rlm_session: Any,
        query_text: str,
        response_text: str,
        *,
        session: Any | None = None,
    ) -> None:
        self._memory_access.record_post_turn(
            rlm_session,
            query_text,
            response_text,
            session=session,
        )

    def get_channel_subagent(self, *, client_id: str, source_name: str = "runtime") -> ChannelSubAgent:
        return create_channel_subagent(self, client_id=client_id, source_name=source_name)

    def attach_channel(self, *, channel: str, client_id: str = "", session_id: str = "") -> None:
        normalized_channel = str(channel or "runtime").strip().lower()
        client_key = str(client_id or session_id or normalized_channel).strip().lower()
        with self._lock:
            clients = self._attached_channels.setdefault(normalized_channel, set())
            was_new = client_key not in clients
            clients.add(client_key)
        if was_new:
            self._emit(
                "recursion_daemon.channel_attached",
                {
                    "channel": normalized_channel,
                    "client_id": client_id,
                    "session_id": session_id,
                },
            )

    def warm_session(self, session: Any) -> bool:
        rlm_session = getattr(session, "rlm_instance", None)
        rlm_core = getattr(rlm_session, "_rlm", None)
        if rlm_core is not None:
            setattr(rlm_core, "_recursion_daemon", self)
        warmed = self._warm_runtime_pool.warm_session(session)

        # Capture always-on references from the warm runtime so the
        # environment (REPL + LMHandler) persists beyond session lifecycle.
        if rlm_core is not None:
            with self._lock:
                self._live_env = getattr(rlm_core, "_persistent_env", None)
                self._live_lm_handler = getattr(rlm_core, "_persistent_lm_handler", None)
                self._live_rlm_core = rlm_core
                self._live_session_id = str(getattr(session, "session_id", "") or "")
                self._live_client_id = str(getattr(session, "client_id", "") or "")

        self._emit(
            "recursion_daemon.session_resumed",
            {
                "session_id": str(getattr(session, "session_id", "") or ""),
                "client_id": str(getattr(session, "client_id", "") or ""),
                "warmed": warmed,
            },
        )
        return warmed

    def should_preserve_session(self, session: Any | None = None, *, session_id: str = "") -> bool:
        candidate_session_id = str(session_id or getattr(session, "session_id", "") or "")
        if not candidate_session_id:
            return False
        with self._lock:
            return (
                self._running
                and not self._draining
                and bool(self._live_session_id)
                and candidate_session_id == self._live_session_id
            )

    def release_session(self, session: Any | None = None, *, session_id: str = "") -> bool:
        candidate_session_id = str(session_id or getattr(session, "session_id", "") or "")
        if not candidate_session_id:
            return False
        with self._lock:
            if candidate_session_id != self._live_session_id:
                return False
            self._live_env = None
            self._live_lm_handler = None
            self._live_rlm_core = None
            self._live_session_id = ""
            self._live_client_id = ""
        self._emit(
            "recursion_daemon.live_session_released",
            {"session_id": candidate_session_id},
        )
        return True

    def bootstrap_session(self, *, client_id: str | None = None) -> Any | None:
        session_manager = self._session_manager
        if session_manager is None:
            return None
        with self._lock:
            if not self._running or self._draining or self._live_session_id:
                return None

        get_or_create = getattr(session_manager, "get_or_create", None)
        if not callable(get_or_create):
            return None

        bootstrap_client_id = str(client_id or self._default_bootstrap_client_id() or "").strip()
        if not bootstrap_client_id:
            return None

        try:
            session = get_or_create(bootstrap_client_id)
        except Exception:
            return None

        if not self.should_preserve_session(session):
            self.warm_session(session)
        self._emit(
            "recursion_daemon.bootstrap_session_ready",
            {
                "session_id": str(getattr(session, "session_id", "") or ""),
                "client_id": str(getattr(session, "client_id", "") or bootstrap_client_id),
            },
        )
        return session

    def _default_bootstrap_client_id(self) -> str:
        configured = str(os.environ.get("RLM_DAEMON_BOOTSTRAP_CLIENT_ID", "") or "").strip()
        if configured:
            return configured
        scope = str(os.environ.get("RLM_SESSION_SCOPE", "main") or "main").strip().lower()
        if scope == "main":
            return "daemon:main"
        return ""

    def run_maintenance(self, *, idle_timeout_s: float = 900.0) -> dict[str, Any]:
        session_manager = self._session_manager
        if session_manager is None:
            result: dict[str, Any] = {
                "status": "skipped",
                "response": "maintenance: no session manager attached",
                "idle_timeout_s": idle_timeout_s,
                "pruned_session_ids": [],
            }
            self._emit("recursion_daemon.maintenance_completed", result)
            return result

        prune_idle_sessions = getattr(session_manager, "prune_idle_sessions", None)
        if not callable(prune_idle_sessions):
            result = {
                "status": "skipped",
                "response": "maintenance: session manager does not support prune_idle_sessions",
                "idle_timeout_s": idle_timeout_s,
                "pruned_session_ids": [],
            }
            self._emit("recursion_daemon.maintenance_completed", result)
            return result

        pruned_session_ids = cast(list[str], prune_idle_sessions(idle_timeout_s))
        channel_reconciliation = self.reconcile_channels()
        with self._lock:
            self._stats["maintenance_runs"] += 1
            self._stats["sessions_pruned"] += len(pruned_session_ids)

        result = {
            "status": "completed",
            "response": (
                f"maintenance: pruned {len(pruned_session_ids)} idle session(s); "
                f"reconciled +{channel_reconciliation['added_client_count']}/-{channel_reconciliation['removed_client_count']} clients"
            ),
            "idle_timeout_s": idle_timeout_s,
            "pruned_session_ids": pruned_session_ids,
            "channel_reconciliation": channel_reconciliation,
        }
        self._emit("recursion_daemon.maintenance_completed", result)
        return result

    def reconcile_channels(self) -> dict[str, Any]:
        session_channel_map: dict[str, set[str]] = {}
        for session in self._list_active_sessions():
            for channel, client_ids in self._session_channel_map(session).items():
                session_channel_map.setdefault(channel, set()).update(client_ids)

        registered_channels = set(self._channel_runtime_snapshot().get("registered_channels", []))
        preserved_channels = {"scheduler"}
        added_clients: list[tuple[str, str]] = []
        removed_clients: list[tuple[str, str]] = []

        with self._lock:
            normalized_attached: dict[str, set[str]] = {}
            for channel, client_ids in self._attached_channels.items():
                if channel in preserved_channels or channel in session_channel_map or channel in registered_channels:
                    normalized_attached[channel] = set(client_ids)
                    continue
                removed_clients.extend((channel, client_id) for client_id in sorted(client_ids))

            for channel, client_ids in session_channel_map.items():
                normalized_channel = normalized_attached.setdefault(channel, set())
                for client_id in sorted(client_ids):
                    if client_id in normalized_channel:
                        continue
                    normalized_channel.add(client_id)
                    added_clients.append((channel, client_id))

            self._attached_channels = {
                channel: client_ids
                for channel, client_ids in normalized_attached.items()
                if client_ids
            }

        for channel, client_id in removed_clients:
            self._emit(
                "recursion_daemon.channel_detached",
                {"channel": channel, "client_id": client_id, "reason": "maintenance_reconcile"},
            )
        for channel, client_id in added_clients:
            self._emit(
                "recursion_daemon.channel_attached",
                {"channel": channel, "client_id": client_id, "reason": "maintenance_reconcile"},
            )

        return {
            "status": "completed",
            "active_session_channels": sorted(session_channel_map),
            "registered_channels": sorted(registered_channels),
            "added_client_count": len(added_clients),
            "removed_client_count": len(removed_clients),
            "added_channels": sorted({channel for channel, _client_id in added_clients}),
            "removed_channels": sorted({channel for channel, _client_id in removed_clients}),
        }

    def build_event(
        self,
        *,
        client_id: str,
        payload: dict[str, Any],
        session: Any | None = None,
    ) -> ChannelEvent:
        channel = str(payload.get("channel") or client_id.partition(":")[0] or "runtime")
        text = str(payload.get("text") or payload.get("message") or "")
        attachments = self._normalize_attachments(payload.get("attachments"))
        metadata = dict(payload.get("channel_meta") or {})
        if payload.get("content_type"):
            metadata.setdefault("content_type", payload.get("content_type"))

        priority = self._infer_priority(channel=channel, metadata=metadata)

        return ChannelEvent(
            event_id=str(uuid.uuid4()),
            timestamp=time.time(),
            channel=channel,
            client_id=client_id,
            session=self._build_session_state(session),
            user_id=str(payload.get("from_user") or getattr(session, "user_id", "") or ""),
            thread_id=str(payload.get("thread_id") or metadata.get("thread_id") or ""),
            message_type=str(payload.get("content_type") or payload.get("message_type") or "text"),
            text=text,
            payload=dict(payload),
            attachments=attachments,
            metadata=metadata,
            priority=priority,
        )

    def classify_event(self, event: ChannelEvent) -> DispatchClass:
        return self._llm_gate.classify_event(event)

    def dispatch_task_sync(
        self,
        owner: Any,
        request: DaemonTaskRequest,
        *,
        fallback: TaskFallbackHandler | None = None,
    ) -> DaemonTaskResult:
        t0 = time.perf_counter_ns()
        self._enter_dispatch()
        try:
            route = self._task_router.classify(request)
            self._emit(
                "recursion_daemon.task_received",
                {
                    "session_id": request.session_id,
                    "client_id": request.client_id,
                    "route": route,
                    "model_role": request.model_role,
                },
            )

            if route == "internal_evaluator":
                self._record_task_agent_invoked(route)
                self._emit(
                    "recursion_daemon.task_agent_invoked",
                    {"route": route, "model_role": request.model_role, "client_id": request.client_id},
                )
                result = self._evaluator_agent.run(owner, request)
            elif route == "internal_planner":
                self._record_task_agent_invoked(route)
                self._emit(
                    "recursion_daemon.task_agent_invoked",
                    {"route": route, "model_role": request.model_role, "client_id": request.client_id},
                )
                result = self._planner_agent.run(owner, request)
            elif route == "internal_text_worker":
                self._record_task_agent_invoked(route)
                self._emit(
                    "recursion_daemon.task_agent_invoked",
                    {"route": route, "model_role": request.model_role, "client_id": request.client_id},
                )
                result = self._text_worker_agent.run(owner, request)
            elif fallback is not None:
                result = fallback(request)
            else:
                raise RuntimeError("RecursionDaemon has no fallback for task dispatch.")

            self._emit(
                "recursion_daemon.task_dispatched",
                {
                    "session_id": request.session_id,
                    "client_id": request.client_id,
                    "route": result.route,
                    "model_role": request.model_role,
                },
            )
            return result
        finally:
            elapsed_ns = time.perf_counter_ns() - t0
            self._record_latency("dispatch_task_sync", elapsed_ns)
            self._leave_dispatch()

    def dispatch_sync(
        self,
        event: ChannelEvent,
        *,
        fallback: DispatchHandler | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter_ns()
        self._enter_dispatch()
        try:
            route = self.classify_event(event)
            handler = self._dispatch_handler or fallback
            if route in {"llm_required", "task_agent_required"} and handler is None:
                raise RuntimeError("RecursionDaemon has no dispatch handler.")

            self._record_received(route)
            self._emit(
                "recursion_daemon.event_received",
                {
                    "event_id": event.event_id,
                    "channel": event.channel,
                    "client_id": event.client_id,
                    "route": route,
                    "priority": event.priority,
                },
            )
            self._emit(
                "recursion_daemon.event_classified",
                {
                    "event_id": event.event_id,
                    "channel": event.channel,
                    "client_id": event.client_id,
                    "route": route,
                },
            )

            result: dict[str, Any]
            if route == "reject":
                result = {
                    "status": "rejected",
                    "response": "",
                    "reason": "empty_event",
                }
            elif route == "deterministic":
                self._record_deterministic_use()
                self._emit(
                    "recursion_daemon.deterministic_path_used",
                    {
                        "event_id": event.event_id,
                        "channel": event.channel,
                        "client_id": event.client_id,
                    },
                )
                result = self._dispatch_deterministic_event(event)
            else:
                if route == "llm_required":
                    self._record_llm_invocation()
                    self._emit(
                        "recursion_daemon.llm_invoked",
                        {
                            "event_id": event.event_id,
                            "channel": event.channel,
                            "client_id": event.client_id,
                        },
                    )
                result = cast(DispatchHandler, handler)(event)

            with self._lock:
                self._stats["dispatched"] += 1

            elapsed_ns = time.perf_counter_ns() - t0
            self._record_latency("dispatch_sync", elapsed_ns)
            self._record_latency(route, elapsed_ns)

            session_id = ""
            if event.session is not None:
                session_id = event.session.session_id

            recursion_result = RecursionResult(
                session_id=session_id,
                route=route,
                content=str(result.get("response", "")),
                metrics={
                    "elapsed_ns": elapsed_ns,
                    "elapsed_ms": round(elapsed_ns / 1_000_000, 3),
                    "event_id": event.event_id,
                    "channel": event.channel,
                },
            )
            self._last_dispatch_result = recursion_result

            result["_recursion_result"] = {
                "session_id": recursion_result.session_id,
                "route": recursion_result.route,
                "elapsed_ms": round(elapsed_ns / 1_000_000, 3),
            }

            self._emit(
                "recursion_daemon.event_dispatched",
                {
                    "event_id": event.event_id,
                    "channel": event.channel,
                    "client_id": event.client_id,
                    "route": route,
                    "result_status": result.get("status", "unknown"),
                    "elapsed_ms": round(elapsed_ns / 1_000_000, 3),
                },
            )
            return result
        finally:
            self._leave_dispatch()

    def _build_session_state(self, session: Any | None) -> DaemonSessionState | None:
        if session is None:
            return None

        session_id = str(getattr(session, "session_id", "") or "")
        client_id = str(getattr(session, "client_id", "") or "")
        originating_channel = str(getattr(session, "originating_channel", "") or "")
        metadata = dict(getattr(session, "metadata", {}) or {})
        delivery_context = dict(getattr(session, "delivery_context", {}) or {})

        # Active channels: primary + delivery + metadata hints
        active_set: set[str] = set()
        primary = (originating_channel or client_id).partition(":")[0].strip().lower()
        if primary:
            active_set.add(primary)
        delivery_channel = str(delivery_context.get("channel") or "").strip().lower()
        if delivery_channel:
            active_set.add(delivery_channel)
        raw_active = metadata.get("_active_channels")
        if isinstance(raw_active, list):
            for item in cast(list[object], raw_active):
                ch = str(item).strip().lower()
                if ch:
                    active_set.add(ch)

        # Channel context: reuse metadata if available
        channel_context: dict[str, Any] = {}
        raw_cc = metadata.get("_channel_context")
        if isinstance(raw_cc, dict):
            channel_context.update(cast(dict[str, Any], raw_cc))
        channel_context.setdefault("channel", primary)
        channel_context.setdefault(
            "transport",
            str(delivery_context.get("transport") or primary or "runtime").strip().lower(),
        )

        # Agent state (from metadata hint) and LLM policy (from runtime policy)
        agent_state: dict[str, Any] = {}
        raw_as = metadata.get("_agent_state")
        if isinstance(raw_as, dict):
            agent_state.update(cast(dict[str, Any], raw_as))

        llm_policy: dict[str, Any] = {}
        raw_lp = metadata.get("_llm_policy")
        if isinstance(raw_lp, dict):
            llm_policy.update(cast(dict[str, Any], raw_lp))
        rlm_session = getattr(session, "rlm_instance", None)
        rlm_core = getattr(rlm_session, "_rlm", None) if rlm_session else None
        policy = getattr(rlm_core, "_runtime_execution_policy", None) if rlm_core else None
        if policy is not None:
            llm_policy["task_class"] = getattr(policy, "task_class", "default")

        return DaemonSessionState(
            session_id=session_id,
            client_id=client_id,
            user_id=str(getattr(session, "user_id", "") or ""),
            status=str(getattr(session, "status", "") or ""),
            originating_channel=originating_channel,
            active_channels=tuple(sorted(active_set)),
            last_activity_at=str(getattr(session, "last_active", "") or ""),
            channel_context=channel_context,
            agent_state=agent_state,
            llm_policy=llm_policy,
            delivery_context=delivery_context,
            metadata=metadata,
        )

    def _infer_priority(self, *, channel: str, metadata: dict[str, Any]) -> EventPriority:
        if metadata.get("priority") in {"low", "normal", "high", "urgent"}:
            return cast(EventPriority, metadata["priority"])
        if channel == "iot" and bool(metadata.get("anomaly")):
            return "high"
        return "normal"

    def _dispatch_deterministic_event(self, event: ChannelEvent) -> dict[str, Any]:
        text = event.text.strip().lower()
        if text in {"status", "/status"}:
            return {
                "status": "completed",
                "response": self._format_status_response(),
            }
        if text in {"help", "/help"}:
            return {
                "status": "completed",
                "response": (
                    "Comandos deterministicos disponiveis: "
                    "/status, /help, /ping, /channels, /sessions, /history, /maintenance, /reconnect, /sync."
                ),
            }
        if text == "maintenance" or text.startswith("/maintenance"):
            return self._handle_maintenance(event)
        if text in {"ping", "/ping"}:
            return {"status": "completed", "response": "pong"}
        if text in {"/channels"}:
            return {"status": "completed", "response": self._format_channels_response()}
        if text in {"/sessions"}:
            return {"status": "completed", "response": self._format_sessions_response()}
        if text in {"/history"}:
            return {"status": "completed", "response": self._format_history_response(event)}
        if event.message_type == "cross_channel_forward" or event.metadata.get("forward_to"):
            return self._handle_cross_channel_forward(event)
        if event.message_type in {"ack", "sync", "reconnect", "channel_control"}:
            return self._handle_channel_control(event)
        if text in {"/reconnect", "/sync"}:
            return self._handle_channel_control(event)
        if event.channel == "iot":
            return {"status": "completed", "response": ""}
        return {"status": "completed", "response": "ok"}

    def _format_status_response(self) -> str:
        snapshot = self.snapshot()
        attached = ", ".join(
            f"{channel}:{count}" for channel, count in sorted(snapshot["attached_channels"].items())
        ) or "none"
        return (
            f"daemon={snapshot['name']} running={snapshot['running']} ready={snapshot['ready']} "
            f"draining={snapshot['draining']} inflight={snapshot['inflight_dispatches']} channels={attached} "
            f"backlog={snapshot['outbox'].get('backlog', 0)} active_sessions={snapshot.get('active_sessions', 0)}"
        )

    def _format_channels_response(self) -> str:
        with self._lock:
            if not self._attached_channels:
                return "channels: none"
            lines = ["channels:"]
            for channel, client_ids in sorted(self._attached_channels.items()):
                lines.append(f"  {channel}: {len(client_ids)} client(s) [{', '.join(sorted(client_ids))}]")
            return "\n".join(lines)

    def _format_sessions_response(self) -> str:
        with self._lock:
            total_clients = sum(len(ids) for ids in self._attached_channels.values())
            return f"sessions: {total_clients} active client(s) across {len(self._attached_channels)} channel(s)"

    def _format_history_response(self, event: ChannelEvent) -> str:
        snapshot = self.snapshot()
        stats = snapshot.get("stats", {})
        return (
            f"history: received={stats.get('received', 0)} "
            f"dispatched={stats.get('dispatched', 0)} "
            f"deterministic={stats.get('deterministic_used', 0)} "
            f"llm={stats.get('llm_invoked', 0)} "
            f"task_agent={stats.get('task_agent_invoked', 0)}"
        )

    def _handle_cross_channel_forward(self, event: ChannelEvent) -> dict[str, Any]:
        target = str(
            event.metadata.get("forward_to")
            or event.metadata.get("cross_channel_target")
            or ""
        ).strip()
        if not target:
            return {"status": "completed", "response": "forward: no target specified"}
        self._emit(
            "recursion_daemon.cross_channel_forward",
            {
                "event_id": event.event_id,
                "source_channel": event.channel,
                "target_channel": target,
                "client_id": event.client_id,
            },
        )
        return {
            "status": "completed",
            "response": f"forward: {event.channel} -> {target}",
            "forward_target": target,
            "source_event_id": event.event_id,
        }

    def _handle_channel_control(self, event: ChannelEvent) -> dict[str, Any]:
        control_type = event.message_type if event.message_type in {
            "ack", "sync", "reconnect", "channel_control",
        } else event.text.strip().lower().lstrip("/")
        self._emit(
            "recursion_daemon.channel_control",
            {
                "event_id": event.event_id,
                "channel": event.channel,
                "client_id": event.client_id,
                "control_type": control_type,
            },
        )
        return {"status": "completed", "response": f"control: {control_type} acknowledged"}

    def _handle_maintenance(self, event: ChannelEvent) -> dict[str, Any]:
        idle_timeout_s = 900.0
        parts = event.text.strip().split(maxsplit=1)
        if len(parts) == 2:
            try:
                idle_timeout_s = float(parts[1])
            except ValueError:
                return {
                    "status": "completed",
                    "response": "maintenance: invalid idle timeout",
                }
        return self.run_maintenance(idle_timeout_s=idle_timeout_s)

    def _normalize_attachments(self, value: Any) -> tuple[dict[str, Any], ...]:
        normalized: list[dict[str, Any]] = []
        if isinstance(value, list):
            for item in cast(list[object], value):
                if isinstance(item, dict):
                    normalized.append(cast(dict[str, Any], item))
        elif isinstance(value, tuple):
            for item in cast(tuple[object, ...], value):
                if isinstance(item, dict):
                    normalized.append(cast(dict[str, Any], item))
        return tuple(normalized)

    def _record_received(self, route: DispatchClass) -> None:
        with self._lock:
            self._stats["received"] += 1
            self._stats[route] += 1

    def _record_llm_invocation(self) -> None:
        with self._lock:
            self._stats["llm_invoked"] += 1

    def _record_deterministic_use(self) -> None:
        with self._lock:
            self._stats["deterministic_used"] += 1

    def _record_task_agent_invoked(self, route: TaskDispatchRoute) -> None:
        if route == "spawn_child_rlm":
            return
        with self._lock:
            self._stats["task_agent_invoked"] += 1

    def _list_active_sessions(self) -> list[Any]:
        session_manager = self._session_manager
        if session_manager is None:
            return []
        list_active_sessions = getattr(session_manager, "list_active_sessions", None)
        if callable(list_active_sessions):
            active_sessions = list_active_sessions()
            if isinstance(active_sessions, (list, tuple)):
                session_items = cast(list[Any] | tuple[Any, ...], active_sessions)
                return [session for session in session_items]
            return []
        raw_active_sessions = getattr(session_manager, "_active_sessions", None)
        if isinstance(raw_active_sessions, dict):
            active_session_map = cast(dict[str, Any], raw_active_sessions)
            return [session for session in active_session_map.values()]
        return []

    def _session_channel_map(self, session: Any) -> dict[str, set[str]]:
        channel_map: dict[str, set[str]] = {}
        session_id = str(getattr(session, "session_id", "") or "")
        client_id = str(getattr(session, "client_id", "") or "")
        primary_channel = str(
            getattr(session, "originating_channel", "") or client_id.partition(":")[0] or ""
        ).partition(":")[0].strip().lower()
        if primary_channel:
            channel_map.setdefault(primary_channel, set()).add(client_id or session_id or primary_channel)

        metadata = dict(getattr(session, "metadata", {}) or {})
        raw_active_channels = metadata.get("_active_channels")
        if isinstance(raw_active_channels, list):
            for item in cast(list[object], raw_active_channels):
                channel = str(item or "").strip().lower()
                if not channel:
                    continue
                if channel == primary_channel:
                    channel_map.setdefault(channel, set()).add(client_id or session_id or channel)
                    continue
                channel_map.setdefault(channel, set()).add(f"{session_id}:{channel}" if session_id else channel)
        return channel_map

    def _outbox_snapshot(self) -> dict[str, Any]:
        raw_stats: dict[str, Any] = {}
        stats_fn = getattr(self._outbox, "stats", None)
        if callable(stats_fn):
            raw_result = stats_fn()
            if isinstance(raw_result, dict):
                raw_stats = cast(dict[str, Any], raw_result)
        snapshot = {
            "pending": int(raw_stats.get("pending", 0) or 0),
            "delivering": int(raw_stats.get("delivering", 0) or 0),
            "delivered": int(raw_stats.get("delivered", 0) or 0),
            "failed": int(raw_stats.get("failed", 0) or 0),
            "dlq": int(raw_stats.get("dlq", 0) or 0),
        }
        snapshot["backlog"] = snapshot["pending"] + snapshot["delivering"]
        worker_alive = False
        is_alive = getattr(self._delivery_worker, "is_alive", None)
        if callable(is_alive):
            worker_alive = bool(is_alive())
        snapshot["worker_alive"] = worker_alive
        return snapshot

    def _channel_runtime_snapshot(self) -> dict[str, Any]:
        summary_fn = getattr(self._channel_status_registry, "summary", None)
        if callable(summary_fn):
            raw_summary = summary_fn()
            if isinstance(raw_summary, dict):
                summary_payload = cast(dict[str, Any], raw_summary)
                raw_channels = summary_payload.get("channels")
                registered_channels = (
                    sorted(str(key) for key in cast(dict[str, Any], raw_channels).keys())
                    if isinstance(raw_channels, dict)
                    else []
                )
                return {
                    "total": int(summary_payload.get("total", 0) or 0),
                    "running": int(summary_payload.get("running", 0) or 0),
                    "healthy": int(summary_payload.get("healthy", 0) or 0),
                    "registered_channels": registered_channels,
                }
        list_channels = getattr(self._channel_status_registry, "list_channels", None)
        if callable(list_channels):
            raw_channels = list_channels()
            registered_channels = (
                [
                    str(item)
                    for item in cast(list[Any] | tuple[Any, ...] | set[Any], raw_channels)
                    if str(item).strip()
                ]
                if isinstance(raw_channels, (list, tuple, set))
                else []
            )
            return {
                "total": len(registered_channels),
                "running": len(registered_channels),
                "healthy": len(registered_channels),
                "registered_channels": sorted(registered_channels),
            }
        return {
            "total": 0,
            "running": 0,
            "healthy": 0,
            "registered_channels": [],
        }

    _LATENCY_MAX_SAMPLES = 1000

    def _record_latency(self, key: str, elapsed_ns: int) -> None:
        with self._lock:
            samples = self._latency_ns.get(key)
            if samples is None:
                samples = []
                self._latency_ns[key] = samples
            samples.append(elapsed_ns)
            if len(samples) > self._LATENCY_MAX_SAMPLES:
                del samples[: len(samples) - self._LATENCY_MAX_SAMPLES]

    def _enter_dispatch(self) -> None:
        with self._lock:
            if not self._running:
                raise RuntimeError("RecursionDaemon is not running.")
            if self._draining:
                raise RuntimeError("RecursionDaemon is draining.")
            self._inflight_dispatches += 1

    def _leave_dispatch(self) -> None:
        with self._lock:
            self._inflight_dispatches = max(0, self._inflight_dispatches - 1)
            self._dispatch_condition.notify_all()

    def dispatch_scheduled_sync(
        self,
        *,
        client_id: str,
        prompt: str,
        job_name: str = "",
        fallback: DispatchHandler | None = None,
    ) -> dict[str, Any]:
        event = ChannelEvent(
            event_id=str(uuid.uuid4()),
            timestamp=time.time(),
            channel="scheduler",
            client_id=client_id,
            session=None,
            message_type="scheduled",
            text=prompt,
            metadata={"job_name": job_name, "source": "scheduler"},
            priority="low",
        )
        self.attach_channel(channel="scheduler", client_id=client_id)
        self._emit(
            "recursion_daemon.scheduled_dispatch",
            {
                "event_id": event.event_id,
                "client_id": client_id,
                "job_name": job_name,
            },
        )
        return self.dispatch_sync(event, fallback=fallback)

    def _emit(self, event_name: str, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(event_name, payload)
        except Exception:
            pass


__all__ = ["RecursionDaemon"]
