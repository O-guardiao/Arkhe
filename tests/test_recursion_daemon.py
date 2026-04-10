from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from rlm.daemon import DaemonMemoryAccess, LLMGate, RecursionDaemon, WarmRuntimePool
from rlm.daemon.channel_subagents import WebChatSubAgent
from rlm.daemon.contracts import ChannelEvent
from rlm.daemon.contracts import DaemonTaskRequest
from rlm.server.runtime_pipeline import dispatch_runtime_prompt_sync


def _fake_session() -> SimpleNamespace:
    return SimpleNamespace(
        session_id="sess-1",
        client_id="telegram:42",
        user_id="user-1",
        status="idle",
        last_active="2026-04-10T00:00:00+00:00",
        state_dir="./states/sess-1",
        total_completions=3,
        total_tokens_used=144,
        originating_channel="telegram:42",
        delivery_context={"channel": "telegram", "client_id": "telegram:42", "replyable": True},
        metadata={
            "scope": "test",
            "_active_channels": ["telegram"],
            "_context_refs": [{"kind": "memory", "path": "./states/sess-1/memory.db"}],
            "_channel_context": {
                "channel": "telegram",
                "transport": "telegram",
                "actor": "demet",
                "origin_session_id": "sess-1",
                "source_name": "telegram_gateway",
            },
            "_agent_state": {"branch": "root"},
            "_llm_policy": {"task_class": "default"},
        },
        rlm_instance=SimpleNamespace(
            _rlm=SimpleNamespace(
                _runtime_execution_policy=SimpleNamespace(
                    task_class="simple_inspect",
                    allow_recursion=False,
                    allow_role_orchestrator=False,
                    max_iterations_override=3,
                    root_model_override="gpt-4o-mini",
                    note="simple local verification path",
                )
            )
        ),
    )


def test_recursion_daemon_build_event_infers_channel_and_session() -> None:
    daemon = RecursionDaemon()
    event = daemon.build_event(
        client_id="telegram:42",
        payload={"text": "ola", "from_user": "demet"},
        session=_fake_session(),
    )

    assert event.channel == "telegram"
    assert event.text == "ola"
    assert event.user_id == "demet"
    assert event.session is not None
    assert event.session.session_id == "sess-1"
    assert event.session.active_channels == ("telegram",)
    assert event.session.last_activity_at == "2026-04-10T00:00:00+00:00"
    assert event.session.channel_context["transport"] == "telegram"
    assert event.session.channel_context["actor"] == "demet"
    assert event.session.channel_context["origin_session_id"] == "sess-1"
    assert event.session.agent_state["branch"] == "root"
    assert event.session.llm_policy["task_class"] == "simple_inspect"


def test_llm_gate_classifies_basic_routes() -> None:
    gate = LLMGate()
    daemon = RecursionDaemon(llm_gate=gate)

    status_event = daemon.build_event(
        client_id="tui:default",
        payload={"text": "/status"},
    )
    assert gate.classify_event(status_event) == "deterministic"

    chat_event = daemon.build_event(
        client_id="telegram:42",
        payload={"text": "me explique esse erro"},
    )
    assert gate.classify_event(chat_event) == "llm_required"

    iot_event = daemon.build_event(
        client_id="iot:sensor-1",
        payload={"message": "temp=98", "channel_meta": {"anomaly": True}},
    )
    assert gate.classify_event(iot_event) == "task_agent_required"


def test_get_channel_subagent_normalizes_webhook_to_api() -> None:
    daemon = RecursionDaemon()

    agent = daemon.get_channel_subagent(client_id="request-42", source_name="webhook")

    assert isinstance(agent, WebChatSubAgent)
    assert agent.channel == "api"
    assert agent.agent_name == "api"


def test_warm_runtime_pool_invokes_owner_runtime() -> None:
    warmed: list[str] = []
    pool = WarmRuntimePool()
    session = SimpleNamespace(
        rlm_instance=SimpleNamespace(
            _rlm=SimpleNamespace(
                ensure_warm_runtime=lambda: warmed.append("warm"),
                _persistent_env=None,
                _persistent_lm_handler=None,
            )
        )
    )

    assert pool.warm_session(session) is True
    assert warmed == ["warm"]


def test_daemon_memory_access_layers_session_workspace_and_kb() -> None:
    def _search_workspace(query: str) -> list[dict[str, str]]:
        return [
            {
                "key": "docs/spec.md",
                "type": "knowledge",
                "preview": f"spec ligado a {query}",
                "match": "hybrid_score: 0.9",
            }
        ]

    def _build_memory_block(query: str, available_tokens: int) -> str:
        del query, available_tokens
        return "[SESSION MEMORY]\n• fato sessao"

    def _retrieve_from_kb(query: str, max_tokens: int = 1200) -> str:
        del query, max_tokens
        return "[CONHECIMENTO PERSISTENTE]\n• fato global"

    workspace_memory = SimpleNamespace(
        scope_id="workspace::repo-main",
        search=_search_workspace,
        _agent_context=SimpleNamespace(
            depth=2,
            branch_id=7,
            role="child_parallel",
            parent_session_id="sess-root",
            channel="tui:default",
        ),
    )
    rlm_session = SimpleNamespace(
        build_memory_block=_build_memory_block,
        _retrieve_from_kb=_retrieve_from_kb,
        _rlm=SimpleNamespace(_persistent_env=SimpleNamespace(_memory=workspace_memory, _originating_channel="tui:default")),
        session_id="sess-1",
    )
    session = SimpleNamespace(
        metadata={
            "_channel_context": {"channel": "tui", "actor": "cli"},
            "_active_channels": ["tui", "telegram"],
        }
    )
    access = DaemonMemoryAccess()

    prompt = access.inject_prompt(rlm_session, "como funciona a memoria?", "PROMPT", session=session)

    assert "[SESSION MEMORY]" in prompt
    assert "[MEMORIA DE WORKSPACE — workspace::repo-main]" in prompt
    assert "depth=2" in prompt
    assert "branch=7" in prompt
    assert "active=tui,telegram" in prompt
    assert "[CONHECIMENTO PERSISTENTE]" in prompt

    snapshot = access.snapshot()
    assert snapshot["recall_requests"] == 1
    assert snapshot["recall_hits"] == 1
    assert snapshot["session_blocks"] == 1
    assert snapshot["workspace_blocks"] == 1
    assert snapshot["kb_blocks"] == 1


def test_recursion_daemon_snapshot_includes_memory_access_stats() -> None:
    def _search_workspace(query: str) -> list[dict[str, str]]:
        del query
        return [{"key": "docs/spec.md", "type": "knowledge", "preview": "spec", "match": "hybrid_score: 0.9"}]

    def _build_memory_block(query: str, available_tokens: int) -> str:
        del query, available_tokens
        return "[SESSION MEMORY]\n• fato sessao"

    def _retrieve_from_kb(query: str, max_tokens: int = 1200) -> str:
        del query, max_tokens
        return ""

    workspace_memory = SimpleNamespace(
        scope_id="workspace::repo-main",
        search=_search_workspace,
        _agent_context=SimpleNamespace(depth=1, branch_id=None, role="root", parent_session_id="", channel="tui:default"),
    )
    daemon = RecursionDaemon()
    daemon.start()
    rlm_session = SimpleNamespace(
        build_memory_block=_build_memory_block,
        _retrieve_from_kb=_retrieve_from_kb,
        _rlm=SimpleNamespace(_persistent_env=SimpleNamespace(_memory=workspace_memory, _originating_channel="tui:default")),
        session_id="sess-1",
    )

    daemon.inject_memory_prompt(rlm_session, "memoria", "PROMPT")

    snapshot = daemon.snapshot()
    assert snapshot["memory_access"]["recall_requests"] == 1
    assert snapshot["memory_access"]["recall_hits"] == 1
    assert snapshot["memory_access"]["workspace_blocks"] == 1


def test_daemon_memory_access_promotes_episodic_memory_with_scope_metadata(tmp_path: Path) -> None:
    from rlm.core.memory.memory_manager import MultiVectorMemory

    class _ImmediateThread:
        def __init__(
            self,
            *,
            target: Any,
            args: tuple[Any, ...] = (),
            kwargs: dict[str, Any] | None = None,
            daemon: bool | None = None,
            name: str | None = None,
        ) -> None:
            del daemon, name
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self) -> None:
            self._target(*self._args, **self._kwargs)

    mem = MultiVectorMemory(db_path=str(tmp_path / "episodic.db"))
    mem.client = None
    access = DaemonMemoryAccess()
    post_turn_async = MagicMock()
    workspace_memory = SimpleNamespace(
        scope_id="workspace::repo-main",
        _agent_context=SimpleNamespace(
            depth=2,
            branch_id=7,
            role="child_parallel",
            parent_session_id="sess-root",
            channel="telegram",
        ),
    )
    rlm_session = SimpleNamespace(
        _memory=mem,
        _session_id="sess-episodic",
        _post_turn_async=post_turn_async,
        _rlm=SimpleNamespace(
            _persistent_env=SimpleNamespace(
                _memory=workspace_memory,
                _originating_channel="telegram:42",
            )
        ),
    )
    session = SimpleNamespace(
        metadata={
            "_channel_context": {"channel": "telegram", "actor": "demet"},
            "_active_channels": ["telegram", "tui"],
        }
    )

    with patch("rlm.daemon.memory_access.threading.Thread", _ImmediateThread):
        access.record_post_turn(
            rlm_session,
            "como esta a branch 7?",
            "a branch 7 consolidou a memoria do daemon",
            session=session,
        )

    post_turn_async.assert_called_once_with(
        "como esta a branch 7?",
        "a branch 7 consolidou a memoria do daemon",
    )

    with sqlite3.connect(mem.db_path) as conn:
        row = conn.execute(
            "SELECT content, metadata FROM memory_chunks WHERE session_id = ? ORDER BY timestamp DESC LIMIT 1",
            ("sess-episodic",),
        ).fetchone()

    assert row is not None
    content, metadata_json = row
    assert "[EPISODIC TURN]" in content
    assert "branch=7" in content

    metadata = json.loads(metadata_json)
    assert metadata["memory_kind"] == "episodic_turn"
    assert metadata["agent_channel"] == "telegram"
    assert metadata["actor"] == "demet"
    assert metadata["active_channels"] == ["telegram", "tui"]
    assert metadata["branch_id"] == 7
    assert metadata["agent_role"] == "child_parallel"

    snapshot = access.snapshot()
    assert snapshot["post_turn_requests"] == 1
    assert snapshot["post_turn_delegated"] == 1
    assert snapshot["episodic_writes"] == 1


def test_recursion_daemon_dispatch_updates_stats() -> None:
    daemon = RecursionDaemon()
    daemon.start()
    event = daemon.build_event(client_id="telegram:42", payload={"text": "ola"})

    result = daemon.dispatch_sync(
        event,
        fallback=lambda received: {
            "status": "completed",
            "response": received.text.upper(),
        },
    )

    snapshot = daemon.snapshot()
    assert result["response"] == "OLA"
    assert snapshot["running"] is True
    assert snapshot["ready"] is True
    assert snapshot["stats"]["received"] == 1
    assert snapshot["stats"]["dispatched"] == 1
    assert snapshot["stats"]["llm_required"] == 1
    assert snapshot["stats"]["llm_invoked"] == 1


def test_recursion_daemon_emits_ready_and_shutdown_events() -> None:
    emitted: list[str] = []

    def _emit(name: str, payload: dict[str, Any]) -> None:
        emitted.append(name)

    daemon = RecursionDaemon(event_bus=SimpleNamespace(emit=_emit))

    daemon.start()
    daemon.attach_channel(channel="tui", client_id="tui:default", session_id="sess-1")
    daemon.stop(timeout_s=0.0)

    assert "recursion_daemon.started" in emitted
    assert "recursion_daemon.ready" in emitted
    assert "recursion_daemon.channel_attached" in emitted
    assert "recursion_daemon.shutdown_drain_started" in emitted
    assert "recursion_daemon.channel_detached" in emitted
    assert "recursion_daemon.shutdown_complete" in emitted


def test_dispatch_sync_uses_internal_deterministic_status_route() -> None:
    daemon = RecursionDaemon()
    daemon.start()
    called: list[bool] = []

    result = daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/status"}),
        fallback=lambda received: called.append(True) or {"status": "completed", "response": received.text},
    )

    snapshot = daemon.snapshot()
    assert called == []
    assert result["status"] == "completed"
    assert "ready=True" in result["response"]
    assert snapshot["stats"]["deterministic_used"] == 1


def test_telegram_subagent_build_event_enriches_channel_metadata() -> None:
    daemon = RecursionDaemon()
    session = _fake_session()
    agent = daemon.get_channel_subagent(client_id="telegram:42", source_name="webhook")

    event = agent.build_event(
        client_id="telegram:42",
        payload={
            "text": "foto recebida",
            "chat_id": 42,
            "message_id": 99,
            "update_id": 321,
            "message_thread_id": 777,
            "from_user": "demet",
            "type": "image",
        },
        session=session,
    )

    assert event.channel == "telegram"
    assert event.message_type == "image"
    assert event.thread_id == "777"
    assert event.metadata["chat_id"] == "42"
    assert event.metadata["message_id"] == "99"
    assert event.metadata["update_id"] == "321"
    assert event.metadata["thread_id"] == "777"
    assert event.metadata["username"] == "demet"
    assert event.metadata["replyable"] is True
    assert event.metadata["source_name"] == "webhook"


def test_webchat_subagent_build_event_promotes_api_request_metadata() -> None:
    daemon = RecursionDaemon()
    session = SimpleNamespace(session_id="sess-web", metadata={})
    agent = daemon.get_channel_subagent(client_id="request-42", source_name="webhook")

    event = agent.build_event(
        client_id="request-42",
        payload={
            "text": "ola web",
            "session_id": "web-session",
            "request_id": "req-9",
        },
        session=session,
    )

    assert event.channel == "api"
    assert event.message_type == "text"
    assert event.thread_id == "web-session"
    assert event.metadata["session_id"] == "web-session"
    assert event.metadata["request_id"] == "req-9"
    assert event.metadata["client_key"] == "request-42"
    assert event.metadata["replyable"] is True
    assert event.metadata["ingress_source"] == "webhook"


def test_iot_subagent_promotes_payload_anomaly_for_gate() -> None:
    daemon = RecursionDaemon(llm_gate=LLMGate())
    agent = daemon.get_channel_subagent(client_id="iot:sensor-1", source_name="webhook")

    event = agent.build_event(
        client_id="iot:sensor-1",
        payload={"message": "temp=101", "anomaly": True},
    )

    assert event.message_type == "event"
    assert event.metadata["anomaly"] is True
    assert daemon.classify_event(event) == "task_agent_required"


def test_warm_session_emits_session_resumed_event() -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    def _emit(name: str, payload: dict[str, object]) -> None:
        emitted.append((name, payload))

    daemon = RecursionDaemon(event_bus=SimpleNamespace(emit=_emit))
    session = SimpleNamespace(
        session_id="sess-1",
        client_id="tui:default",
        rlm_instance=SimpleNamespace(
            _rlm=SimpleNamespace(
                ensure_warm_runtime=lambda: None,
                _persistent_env=None,
                _persistent_lm_handler=None,
            )
        ),
    )

    assert daemon.warm_session(session) is True
    assert any(name == "recursion_daemon.session_resumed" for name, _payload in emitted)


def test_dispatch_task_sync_uses_internal_text_worker_route() -> None:
    calls: dict[str, object] = {}

    class FakeClient:
        model_name = "gpt-4o-mini"

        def completion(self, prompt: str) -> str:
            calls["prompt"] = prompt
            return "worker-response"

    class FakeHandler:
        def get_client(self, model_name: str | None, *, depth: int):
            calls["model_name"] = model_name
            calls["depth"] = depth
            return FakeClient()

    owner = SimpleNamespace(
        depth=0,
        backend_kwargs={"model_name": "gpt-4o-mini"},
        ensure_warm_runtime=lambda: (FakeHandler(), object()),
    )
    daemon = RecursionDaemon()
    daemon.start()
    request_metadata: dict[str, Any] = {"text_only": True, "return_artifacts": False}

    result = daemon.dispatch_task_sync(
        owner,
        DaemonTaskRequest(
            session_id="sess-1",
            client_id="tui:default",
            task="resuma a resposta",
            context="contexto textual",
            model_role="worker",
            interaction_mode="text",
            metadata=request_metadata,
        ),
    )

    assert result.route == "internal_text_worker"
    assert result.response == "worker-response"
    assert calls["model_name"] == "gpt-4o-mini"
    assert calls["depth"] == 1
    prompt = cast(str, calls["prompt"])
    assert "contexto textual" in prompt
    assert "resuma a resposta" in prompt


def test_dispatch_task_sync_uses_internal_planner_route() -> None:
    calls: dict[str, object] = {}

    class FakeClient:
        model_name = "gpt-4o-mini"

        def completion(self, prompt: str) -> str:
            calls["prompt"] = prompt
            return "planner-response"

    class FakeHandler:
        def get_client(self, model_name: str | None, *, depth: int):
            calls["model_name"] = model_name
            calls["depth"] = depth
            return FakeClient()

    owner = SimpleNamespace(
        depth=1,
        backend_kwargs={"model_name": "gpt-4o-mini"},
        ensure_warm_runtime=lambda: (FakeHandler(), object()),
    )
    daemon = RecursionDaemon()
    daemon.start()

    result = daemon.dispatch_task_sync(
        owner,
        DaemonTaskRequest(
            session_id="sess-2",
            client_id="tui:default",
            task="planeje a próxima etapa",
            context="resuma o estado atual antes de decidir",
            model_role="planner",
            interaction_mode="text",
            metadata={"text_only": True, "return_artifacts": False},
        ),
    )

    assert result.route == "internal_planner"
    assert result.response == "planner-response"
    assert calls["model_name"] == "gpt-4o-mini"
    assert calls["depth"] == 2
    prompt = cast(str, calls["prompt"])
    assert "resuma o estado atual" in prompt
    assert "planeje a próxima etapa" in prompt


def test_dispatch_task_sync_uses_internal_text_worker_route_for_simple_inspect() -> None:
    calls: dict[str, object] = {}

    class FakeClient:
        model_name = "gpt-4o-mini"

        def completion(self, prompt: str) -> str:
            calls["prompt"] = prompt
            return "inspect-response"

    class FakeHandler:
        def get_client(self, model_name: str | None, *, depth: int):
            calls["model_name"] = model_name
            calls["depth"] = depth
            return FakeClient()

    owner = SimpleNamespace(
        depth=0,
        backend_kwargs={"model_name": "gpt-4o-mini"},
        ensure_warm_runtime=lambda: (FakeHandler(), object()),
    )
    daemon = RecursionDaemon()
    daemon.start()
    request_metadata: dict[str, Any] = {"text_only": True, "return_artifacts": False}

    result = daemon.dispatch_task_sync(
        owner,
        DaemonTaskRequest(
            session_id="sess-3",
            client_id="tui:default",
            task="inspecione a resposta e resuma o risco",
            context="payload textual curto",
            model_role="simple_inspect",
            interaction_mode="text",
            metadata=request_metadata,
        ),
    )

    assert result.route == "internal_text_worker"
    assert result.response == "inspect-response"
    assert calls["model_name"] == "gpt-4o-mini"
    assert calls["depth"] == 1
    prompt = cast(str, calls["prompt"])
    assert "payload textual curto" in prompt
    assert "inspecione a resposta" in prompt


def test_dispatch_runtime_prompt_sync_prefers_daemon_path() -> None:
    calls: dict[str, object] = {}
    session = _fake_session()

    def _get_or_create(client_id: str) -> SimpleNamespace:
        return session

    class FakeDaemon:
        def warm_session(self, incoming_session: Any) -> None:
            calls["warm_session"] = incoming_session

        def build_event(self, *, client_id: str, payload: dict[str, Any], session: Any | None = None) -> SimpleNamespace:
            calls["build_event"] = (client_id, payload, session)
            return SimpleNamespace(payload=payload)

        def dispatch_sync(self, event: ChannelEvent | SimpleNamespace, *, fallback: Any | None = None) -> dict[str, str]:
            calls["dispatch_sync"] = event
            calls["fallback_present"] = callable(fallback)
            return {"status": "completed", "response": "via-daemon"}

    services = SimpleNamespace(
        recursion_daemon=FakeDaemon(),
        session_manager=SimpleNamespace(get_or_create=_get_or_create),
    )

    result = dispatch_runtime_prompt_sync(
        cast(Any, services),
        "tui:default",
        {"text": "hello"},
    )

    assert result["response"] == "via-daemon"
    assert calls["fallback_present"] is True
    assert calls["warm_session"] is session
    assert calls["build_event"] == ("tui:default", {"text": "hello"}, session)


def test_dispatch_runtime_prompt_sync_enriches_session_channel_context() -> None:
    session = _fake_session()
    persisted: list[dict[str, Any]] = []

    def _get_or_create(client_id: str) -> SimpleNamespace:
        return session

    class FakeDaemon:
        def warm_session(self, incoming_session: Any) -> None:
            return None

        def build_event(self, *, client_id: str, payload: dict[str, Any], session: Any | None = None) -> SimpleNamespace:
            return SimpleNamespace(payload=payload)

        def dispatch_sync(self, event: ChannelEvent | SimpleNamespace, *, fallback: Any | None = None) -> dict[str, str]:
            return {"status": "completed", "response": "ok"}

    def _update_session(current: Any) -> None:
        persisted.append(dict(current.metadata))

    services = SimpleNamespace(
        recursion_daemon=FakeDaemon(),
        session_manager=SimpleNamespace(get_or_create=_get_or_create, update_session=_update_session),
    )

    result = dispatch_runtime_prompt_sync(
        cast(Any, services),
        "cli_main",
        {
            "text": "hello",
            "metadata": {
                "transport": "brain_router",
                "actor": "cli",
                "requested_session_id": "brain-sess-9",
                "session_origin": "brain_prompt",
            },
        },
        source_name="brain_router",
    )

    assert result["response"] == "ok"
    assert session.metadata["_channel_context"]["transport"] == "brain_router"
    assert session.metadata["_channel_context"]["actor"] == "cli"
    assert session.metadata["_channel_context"]["requested_session_id"] == "brain-sess-9"
    assert session.metadata["_channel_context"]["origin_session_id"] == "brain-sess-9"
    assert session.metadata["_agent_state"]["transport"] == "brain_router"
    assert session.delivery_context["metadata"]["actor"] == "cli"
    assert persisted


def test_dispatch_runtime_prompt_sync_uses_channel_subagent_when_available() -> None:
    calls: dict[str, object] = {}
    session = _fake_session()

    def _get_or_create(client_id: str) -> SimpleNamespace:
        return session

    class FakeChannelAgent:
        def attach_session(self, incoming_session: Any) -> None:
            calls["attach_session"] = incoming_session

        def build_event(self, *, client_id: str, payload: dict[str, Any], session: Any | None = None) -> SimpleNamespace:
            calls["build_event"] = (client_id, payload, session)
            return SimpleNamespace(payload=payload)

    class FakeDaemon:
        def warm_session(self, incoming_session: Any) -> None:
            calls["warm_session"] = incoming_session

        def get_channel_subagent(self, *, client_id: str, source_name: str = "runtime") -> FakeChannelAgent:
            calls["get_channel_subagent"] = (client_id, source_name)
            return FakeChannelAgent()

        def dispatch_sync(self, event: ChannelEvent | SimpleNamespace, *, fallback: Any | None = None) -> dict[str, str]:
            calls["dispatch_sync"] = event
            calls["fallback_present"] = callable(fallback)
            return {"status": "completed", "response": "via-channel-agent"}

    services = SimpleNamespace(
        recursion_daemon=FakeDaemon(),
        session_manager=SimpleNamespace(get_or_create=_get_or_create),
    )

    result = dispatch_runtime_prompt_sync(
        cast(Any, services),
        "tui:default",
        {"text": "hello"},
        source_name="webhook",
    )

    assert result["response"] == "via-channel-agent"
    assert calls["warm_session"] is session
    assert calls["get_channel_subagent"] == ("tui:default", "webhook")
    assert calls["attach_session"] is session
    assert calls["build_event"] == ("tui:default", {"text": "hello"}, session)


# ── Slice 1: LLMGate expanded deterministic routes ──────────────────────


def test_llm_gate_classifies_cross_channel_forward_via_metadata_as_deterministic() -> None:
    gate = LLMGate()
    daemon = RecursionDaemon(llm_gate=gate)

    event = daemon.build_event(
        client_id="tui:default",
        payload={"text": "send to telegram", "channel_meta": {"forward_to": "telegram:42"}},
    )
    assert gate.classify_event(event) == "deterministic"


def test_llm_gate_classifies_cross_channel_target_metadata_as_deterministic() -> None:
    gate = LLMGate()
    event = ChannelEvent(
        event_id="e1",
        timestamp=0,
        channel="tui",
        client_id="tui:default",
        session=None,
        text="forward this",
        metadata={"cross_channel_target": "webchat"},
    )
    assert gate.classify_event(event) == "deterministic"


def test_llm_gate_classifies_ack_sync_reconnect_message_types_as_deterministic() -> None:
    gate = LLMGate()
    for msg_type in ("ack", "sync", "reconnect", "channel_control", "cross_channel_forward"):
        event = ChannelEvent(
            event_id="e1",
            timestamp=0,
            channel="telegram",
            client_id="telegram:42",
            session=None,
            text="payload",
            message_type=msg_type,
        )
        assert gate.classify_event(event) == "deterministic", f"Expected deterministic for {msg_type}"


def test_llm_gate_classifies_slash_channels_sessions_history_as_deterministic() -> None:
    gate = LLMGate()
    for cmd in ("/channels", "/sessions", "/history", "/reconnect", "/sync"):
        event = ChannelEvent(
            event_id="e1",
            timestamp=0,
            channel="tui",
            client_id="tui:default",
            session=None,
            text=cmd,
        )
        assert gate.classify_event(event) == "deterministic", f"Expected deterministic for {cmd}"


# ── Slice 1: Daemon deterministic dispatch expansion ─────────────────────


def test_dispatch_deterministic_channels_command() -> None:
    daemon = RecursionDaemon()
    daemon.start()
    daemon.attach_channel(channel="telegram", client_id="telegram:42")
    daemon.attach_channel(channel="tui", client_id="tui:default")

    def _fallback(event: ChannelEvent) -> dict[str, str]:
        del event
        return {"status": "completed", "response": "should not be called"}

    result = daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/channels"}),
        fallback=_fallback,
    )

    assert result["status"] == "completed"
    assert "telegram" in result["response"]
    assert "tui" in result["response"]


def test_dispatch_deterministic_sessions_command() -> None:
    daemon = RecursionDaemon()
    daemon.start()
    daemon.attach_channel(channel="tui", client_id="tui:default")

    def _fallback(event: ChannelEvent) -> dict[str, str]:
        del event
        return {"status": "completed", "response": "fallback"}

    result = daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/sessions"}),
        fallback=_fallback,
    )

    assert result["status"] == "completed"
    assert "1 active client" in result["response"]


def test_dispatch_deterministic_history_command() -> None:
    daemon = RecursionDaemon()
    daemon.start()
    daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/status"}),
    )

    result = daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/history"}),
    )

    assert result["status"] == "completed"
    assert "received=" in result["response"]
    assert "deterministic=" in result["response"]


def test_dispatch_deterministic_cross_channel_forward() -> None:
    emitted: list[str] = []

    def _emit(name: str, payload: dict[str, Any]) -> None:
        del payload
        emitted.append(name)

    daemon = RecursionDaemon(event_bus=SimpleNamespace(emit=_emit))
    daemon.start()

    event = ChannelEvent(
        event_id="e1",
        timestamp=0,
        channel="tui",
        client_id="tui:default",
        session=None,
        text="send this",
        message_type="cross_channel_forward",
        metadata={"forward_to": "telegram:42"},
    )

    result = daemon.dispatch_sync(event)

    assert result["status"] == "completed"
    assert "tui -> telegram:42" in result["response"]
    assert result["forward_target"] == "telegram:42"
    assert "recursion_daemon.cross_channel_forward" in emitted


def test_dispatch_deterministic_channel_control_ack() -> None:
    emitted: list[str] = []

    def _emit(name: str, payload: dict[str, Any]) -> None:
        del payload
        emitted.append(name)

    daemon = RecursionDaemon(event_bus=SimpleNamespace(emit=_emit))
    daemon.start()

    event = ChannelEvent(
        event_id="e1",
        timestamp=0,
        channel="telegram",
        client_id="telegram:42",
        session=None,
        text="",
        message_type="ack",
    )

    result = daemon.dispatch_sync(event)

    assert result["status"] == "completed"
    assert "ack acknowledged" in result["response"]
    assert "recursion_daemon.channel_control" in emitted


def test_dispatch_deterministic_slash_reconnect() -> None:
    daemon = RecursionDaemon()
    daemon.start()

    result = daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/reconnect"}),
    )

    assert result["status"] == "completed"
    assert "reconnect acknowledged" in result["response"]


def test_dispatch_deterministic_help_shows_expanded_commands() -> None:
    daemon = RecursionDaemon()
    daemon.start()

    result = daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/help"}),
    )

    assert "/channels" in result["response"]
    assert "/sessions" in result["response"]
    assert "/reconnect" in result["response"]
    assert "/sync" in result["response"]


# ── Slice 2: sub_rlm auto-divert for textual roles ──────────────────────


def test_task_agent_router_routes_evaluator_without_artifacts() -> None:
    from rlm.daemon.task_agents import TaskAgentRouter
    router = TaskAgentRouter()

    result = router.classify(DaemonTaskRequest(
        task="evaluate something",
        model_role="evaluator",
        metadata={"text_only": False, "return_artifacts": False},
    ))

    assert result == "internal_evaluator"


def test_task_agent_router_routes_text_roles_to_internal_worker() -> None:
    from rlm.daemon.task_agents import TaskAgentRouter

    router = TaskAgentRouter()
    request_metadata: dict[str, Any] = {"text_only": True, "return_artifacts": False}

    for role in ("fast", "response", "simple", "simple_inspect", "micro", "minirepl"):
        result = router.classify(
            DaemonTaskRequest(
                task="tarefa textual",
                model_role=role,
                interaction_mode="text",
                metadata=request_metadata,
            )
        )
        assert result == "internal_text_worker"


# ── Slice 3: WarmRuntimePool measurement ────────────────────────────────


def test_warm_runtime_pool_tracks_cold_and_warm_metrics() -> None:
    pool = WarmRuntimePool()

    cold_core = SimpleNamespace(
        ensure_warm_runtime=lambda: None,
        _persistent_env=None,
        _persistent_lm_handler=None,
        _warm_since_ts=time.time(),
        _last_warm_access_ts=None,
        _warm_turn_count=1,
    )
    session_cold = SimpleNamespace(
        rlm_instance=SimpleNamespace(_rlm=cold_core)
    )
    assert pool.warm_session(session_cold) is True

    snap1 = pool.snapshot()
    assert snap1["warmed"] == 1
    assert snap1["already_warm"] == 0
    assert snap1["turn_count"] == 1
    assert snap1["warm_since_ts"] is not None
    assert snap1["warm_uptime_s"] >= 0.0

    warm_core = SimpleNamespace(
        ensure_warm_runtime=lambda: None,
        _persistent_env=object(),
        _persistent_lm_handler=object(),
        _warm_since_ts=cold_core._warm_since_ts,
        _last_warm_access_ts=time.time(),
        _warm_turn_count=2,
    )
    session_warm = SimpleNamespace(
        rlm_instance=SimpleNamespace(_rlm=warm_core)
    )
    assert pool.warm_session(session_warm) is True

    snap2 = pool.snapshot()
    assert snap2["warmed"] == 1
    assert snap2["already_warm"] == 1
    assert snap2["turn_count"] == 2
    assert snap2["last_warm_ts"] is not None
    assert snap2["last_warm_ts"] >= snap1["warm_since_ts"]


def test_warm_runtime_pool_failed_session_does_not_increment_turn_count() -> None:
    pool = WarmRuntimePool()
    session = SimpleNamespace(rlm_instance=None)

    assert pool.warm_session(session) is False

    snap = pool.snapshot()
    assert snap["turn_count"] == 0
    assert snap["warmed"] == 0
    assert snap["already_warm"] == 0
    assert snap["failed"] == 1
    assert snap["warm_since_ts"] is None
    assert snap["warm_uptime_s"] == 0.0


# ── Slice 4: RecursionResult tracking + latency metrics ─────────────────


def test_dispatch_sync_enriches_result_with_recursion_result() -> None:
    daemon = RecursionDaemon()
    daemon.start()

    result = daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/status"}),
    )

    rr = result.get("_recursion_result")
    assert rr is not None
    assert rr["route"] == "deterministic"
    assert rr["elapsed_ms"] >= 0.0
    assert isinstance(rr["session_id"], str)


def test_dispatch_sync_sets_last_dispatch_result() -> None:
    from rlm.daemon.contracts import RecursionResult

    daemon = RecursionDaemon()
    daemon.start()
    assert daemon.last_dispatch_result is None

    daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/ping"}),
    )

    ldr = daemon.last_dispatch_result
    assert ldr is not None
    assert isinstance(ldr, RecursionResult)
    assert ldr.route == "deterministic"
    assert ldr.content == "pong"
    assert ldr.metrics["channel"] == "tui"
    assert ldr.metrics["elapsed_ms"] >= 0.0


def test_dispatch_sync_latency_recorded_in_snapshot() -> None:
    daemon = RecursionDaemon()
    daemon.start()

    daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/status"}),
    )
    daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/ping"}),
    )

    snap = daemon.snapshot()
    lat = snap["latency"]
    assert "dispatch_sync" in lat
    assert lat["dispatch_sync"]["count"] == 2
    assert lat["dispatch_sync"]["mean_ms"] >= 0.0
    assert lat["dispatch_sync"]["p50_ms"] >= 0.0
    assert lat["dispatch_sync"]["p95_ms"] >= 0.0
    assert lat["dispatch_sync"]["max_ms"] >= 0.0
    assert "deterministic" in lat
    assert lat["deterministic"]["count"] == 2


def test_dispatch_sync_latency_per_route_distinct() -> None:
    daemon = RecursionDaemon()
    daemon.start()

    def _fallback(event: ChannelEvent) -> dict[str, str]:
        del event
        return {"status": "completed", "response": "llm result"}

    daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/ping"}),
    )
    daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "complex query"}),
        fallback=_fallback,
    )

    snap = daemon.snapshot()
    lat = snap["latency"]
    assert "deterministic" in lat
    assert lat["deterministic"]["count"] == 1
    assert "llm_required" in lat
    assert lat["llm_required"]["count"] == 1
    assert "dispatch_sync" in lat
    assert lat["dispatch_sync"]["count"] == 2


def test_dispatch_task_sync_records_latency() -> None:
    from rlm.daemon.task_agents import TaskAgentRouter, DaemonTaskResult

    daemon = RecursionDaemon(task_router=TaskAgentRouter())
    daemon.start()

    # Mock the evaluator agent to avoid needing a full warm runtime
    fake_result = DaemonTaskResult(route="internal_evaluator", response="ok")
    daemon._evaluator_agent.run = lambda _owner, _req: fake_result  # type: ignore[assignment]

    owner = SimpleNamespace()
    request = DaemonTaskRequest(
        session_id="sess-1",
        client_id="tui:default",
        task="evaluate this",
        model_role="evaluator",
    )
    daemon.dispatch_task_sync(owner, request)

    snap = daemon.snapshot()
    lat = snap["latency"]
    assert "dispatch_task_sync" in lat
    assert lat["dispatch_task_sync"]["count"] == 1
    assert lat["dispatch_task_sync"]["mean_ms"] >= 0.0


def test_dispatch_event_dispatched_emits_elapsed_ms() -> None:
    payloads: list[dict[str, Any]] = []

    def emit(name: str, payload: dict[str, Any]) -> None:
        if name == "recursion_daemon.event_dispatched":
            payloads.append(payload)

    daemon = RecursionDaemon(event_bus=SimpleNamespace(emit=emit))
    daemon.start()

    daemon.dispatch_sync(
        daemon.build_event(client_id="tui:default", payload={"text": "/ping"}),
    )

    assert len(payloads) == 1
    assert "elapsed_ms" in payloads[0]
    assert payloads[0]["elapsed_ms"] >= 0.0


# ── Slice 5: Scheduler ↔ Daemon bridge ──────────────────────────────────


def test_attach_scheduler_stores_reference() -> None:
    daemon = RecursionDaemon()
    assert daemon.scheduler is None

    fake_scheduler = SimpleNamespace(name="test-scheduler")
    daemon.attach_scheduler(fake_scheduler)

    assert daemon.scheduler is fake_scheduler


def test_attach_scheduler_emits_event() -> None:
    emitted: list[str] = []

    def _emit(name: str, payload: dict[str, Any]) -> None:
        del payload
        emitted.append(name)

    daemon = RecursionDaemon(event_bus=SimpleNamespace(emit=_emit))

    daemon.attach_scheduler(SimpleNamespace())

    assert "recursion_daemon.scheduler_attached" in emitted


def test_snapshot_shows_scheduler_attached_flag() -> None:
    daemon = RecursionDaemon()
    daemon.start()

    snap1 = daemon.snapshot()
    assert snap1["scheduler_attached"] is False

    daemon.attach_scheduler(SimpleNamespace())

    snap2 = daemon.snapshot()
    assert snap2["scheduler_attached"] is True


def test_attach_session_manager_stores_reference() -> None:
    daemon = RecursionDaemon()
    assert daemon.session_manager is None

    fake_session_manager = SimpleNamespace(name="session-manager")
    daemon.attach_session_manager(fake_session_manager)

    assert daemon.session_manager is fake_session_manager


def test_snapshot_shows_session_manager_attached_flag() -> None:
    daemon = RecursionDaemon()
    daemon.start()

    snap1 = daemon.snapshot()
    assert snap1["session_manager_attached"] is False

    daemon.attach_session_manager(SimpleNamespace())

    snap2 = daemon.snapshot()
    assert snap2["session_manager_attached"] is True


def test_attach_session_manager_bootstraps_main_runtime_when_running() -> None:
    daemon = RecursionDaemon()
    daemon.start()
    requested_client_ids: list[str] = []
    session = SimpleNamespace(
        session_id="sess-bootstrap",
        client_id="daemon:main",
        rlm_instance=SimpleNamespace(
            _rlm=SimpleNamespace(
                ensure_warm_runtime=lambda: None,
                _persistent_env=object(),
                _persistent_lm_handler=object(),
            )
        ),
    )

    def _get_or_create(client_id: str) -> Any:
        requested_client_ids.append(client_id)
        return session

    with patch.dict("os.environ", {"RLM_SESSION_SCOPE": "main"}, clear=False):
        daemon.attach_session_manager(SimpleNamespace(get_or_create=_get_or_create))

    snap = daemon.snapshot()
    assert requested_client_ids == ["daemon:main"]
    assert snap["live_env_active"] is True
    assert snap["live_lm_handler_active"] is True
    assert snap["live_session_id"] == "sess-bootstrap"
    assert snap["live_client_id"] == "daemon:main"


def test_attach_channel_runtime_snapshot_exposes_queue_and_channel_health() -> None:
    daemon = RecursionDaemon()
    daemon.start()

    def _channel_summary() -> dict[str, Any]:
        return {
            "total": 3,
            "running": 2,
            "healthy": 2,
            "channels": {"telegram": [{}], "tui": [{}], "webchat": [{}]},
        }

    def _outbox_stats() -> dict[str, int]:
        return {"pending": 3, "delivering": 1, "delivered": 5, "failed": 0, "dlq": 1}

    daemon.attach_channel_runtime(
        channel_status_registry=SimpleNamespace(summary=_channel_summary),
        outbox=SimpleNamespace(stats=_outbox_stats),
        delivery_worker=SimpleNamespace(is_alive=lambda: True),
    )

    snap = daemon.snapshot()

    assert snap["channel_runtime"]["total"] == 3
    assert snap["channel_runtime"]["running"] == 2
    assert snap["channel_runtime"]["healthy"] == 2
    assert snap["channel_runtime"]["registered_channels"] == ["telegram", "tui", "webchat"]
    assert snap["outbox"]["pending"] == 3
    assert snap["outbox"]["backlog"] == 4
    assert snap["outbox"]["worker_alive"] is True


def test_run_maintenance_reconciles_stale_channels_against_active_sessions() -> None:
    emitted: list[tuple[str, dict[str, Any]]] = []

    def emit(name: str, payload: dict[str, Any]) -> None:
        emitted.append((name, payload))

    active_session = SimpleNamespace(
        session_id="sess-keep",
        client_id="telegram:42",
        originating_channel="telegram:42",
        metadata={"_active_channels": ["telegram", "webchat"]},
    )

    def _prune_idle_sessions(_timeout_s: float) -> list[str]:
        return []

    def _list_active_sessions() -> list[Any]:
        return [active_session]

    def _channel_summary() -> dict[str, Any]:
        return {
            "total": 2,
            "running": 2,
            "healthy": 2,
            "channels": {"telegram": [{}], "webchat": [{}]},
        }

    daemon = RecursionDaemon(event_bus=SimpleNamespace(emit=emit))
    daemon.start()
    daemon.attach_session_manager(
        SimpleNamespace(
            prune_idle_sessions=_prune_idle_sessions,
            list_active_sessions=_list_active_sessions,
        )
    )
    daemon.attach_channel_runtime(
        channel_status_registry=SimpleNamespace(summary=_channel_summary)
    )
    daemon.attach_channel(channel="slack", client_id="slack:99")

    result = daemon.run_maintenance(idle_timeout_s=60.0)
    snap = daemon.snapshot()

    assert "slack" not in snap["attached_channels"]
    assert snap["attached_channels"]["telegram"] == 1
    assert snap["attached_channels"]["webchat"] == 1
    assert result["channel_reconciliation"]["removed_channels"] == ["slack"]
    assert sorted(result["channel_reconciliation"]["added_channels"]) == ["telegram", "webchat"]
    assert any(
        name == "recursion_daemon.channel_detached" and payload.get("reason") == "maintenance_reconcile"
        for name, payload in emitted
    )
    assert any(
        name == "recursion_daemon.channel_attached" and payload.get("reason") == "maintenance_reconcile"
        for name, payload in emitted
    )


def test_dispatch_scheduled_sync_creates_event_and_dispatches() -> None:
    emitted_names: list[str] = []

    def _emit(name: str, payload: dict[str, Any]) -> None:
        del payload
        emitted_names.append(name)

    daemon = RecursionDaemon(event_bus=SimpleNamespace(emit=_emit))
    daemon.start()

    result = daemon.dispatch_scheduled_sync(
        client_id="cron:healthcheck",
        prompt="/status",
        job_name="healthcheck",
    )

    assert result["status"] == "completed"
    assert "daemon=" in result["response"]
    assert "recursion_daemon.scheduled_dispatch" in emitted_names
    assert "recursion_daemon.event_dispatched" in emitted_names

    snap = daemon.snapshot()
    assert "scheduler" in snap["attached_channels"]


def test_dispatch_scheduled_sync_llm_prompt_hits_fallback() -> None:
    fallback_calls: list[str] = []

    def fallback(event: ChannelEvent) -> dict[str, Any]:
        fallback_calls.append(event.text)
        return {"status": "completed", "response": "scheduled result"}

    daemon = RecursionDaemon()
    daemon.start()
    daemon.register_dispatch_handler(fallback)

    result = daemon.dispatch_scheduled_sync(
        client_id="cron:daily-summary",
        prompt="Generate daily summary report",
        job_name="daily-summary",
    )

    assert result["status"] == "completed"
    assert result["response"] == "scheduled result"
    assert fallback_calls == ["Generate daily summary report"]


def test_dispatch_scheduled_sync_tracks_latency() -> None:
    daemon = RecursionDaemon()
    daemon.start()

    daemon.dispatch_scheduled_sync(
        client_id="cron:test",
        prompt="/ping",
        job_name="test",
    )

    snap = daemon.snapshot()
    lat = snap["latency"]
    assert "dispatch_sync" in lat
    assert lat["dispatch_sync"]["count"] == 1


def test_dispatch_maintenance_runs_prune_idle_sessions() -> None:
    emitted_names: list[str] = []
    pruned_calls: list[float] = []

    def emit(name: str, payload: dict[str, Any]) -> None:
        del payload
        emitted_names.append(name)

    def prune_idle_sessions(timeout_s: float) -> list[str]:
        pruned_calls.append(timeout_s)
        return ["sess-1", "sess-2"]

    daemon = RecursionDaemon(event_bus=SimpleNamespace(emit=emit))
    daemon.start()
    daemon.attach_session_manager(
        SimpleNamespace(prune_idle_sessions=prune_idle_sessions)
    )

    result = daemon.dispatch_sync(
        daemon.build_event(client_id="cron:maintenance", payload={"text": "/maintenance 60"}),
    )

    assert result["status"] == "completed"
    assert result["pruned_session_ids"] == ["sess-1", "sess-2"]
    assert pruned_calls == [60.0]
    assert "recursion_daemon.maintenance_completed" in emitted_names

    snap = daemon.snapshot()
    assert snap["stats"]["maintenance_runs"] == 1
    assert snap["stats"]["sessions_pruned"] == 2


def test_run_maintenance_without_session_manager_is_skipped() -> None:
    daemon = RecursionDaemon()

    result = daemon.run_maintenance(idle_timeout_s=30.0)

    assert result["status"] == "skipped"
    assert result["pruned_session_ids"] == []
