from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from rich.console import Console


class _DummySessionManager:
    def __init__(self, session) -> None:
        self.session = session
        self.events: list[dict] = []
        self.updated: list[dict] = []

    def get_or_create(self, client_id: str):
        return self.session

    def session_to_dict(self, session):
        return {
            "session_id": session.session_id,
            "client_id": session.client_id,
            "status": session.status,
            "created_at": "2026-03-20T00:00:00+00:00",
            "last_active": "2026-03-20T00:00:00+00:00",
            "total_completions": 0,
            "total_tokens_used": 0,
            "last_error": "",
            "metadata": session.metadata,
            "has_rlm_instance": True,
        }

    def get_events(self, session_id: str, limit: int = 40):
        items = list(self.events)
        if limit > 0:
            items = items[-limit:]
        return list(reversed(items))

    def log_event(self, session_id: str, event_type: str, payload: dict | None = None):
        self.events.append({"event_type": event_type, "payload": payload or {}})

    def update_session(self, session) -> None:
        self.updated.append(dict(session.metadata))


class _DummyEnv:
    def __init__(self) -> None:
        self.snapshot = {
            "tasks": {"current": {"title": "Analisar sessão", "status": "in-progress", "note": "runtime ativo"}, "items": []},
            "attachments": {"items": []},
            "timeline": {"entries": [{"event_type": "fanout", "summary": "branches emitidos"}]},
            "recursive_session": {
                "messages": [
                    {"role": "user", "content": "oi"},
                    {"role": "assistant", "content": "olá"},
                ],
                "commands": [],
                "events": [{"event_type": "assistant_message_emitted", "payload": {"role": "assistant"}}],
            },
            "coordination": {
                "events": [{"operation": "consensus", "payload_preview": "branch 2 venceu"}],
                "branch_tasks": [
                    {"branch_id": 2, "title": "Branch 2", "mode": "implement", "status": "completed", "metadata": {"role": "implementation"}},
                ],
                "latest_parallel_summary": {"winner_branch_id": 2},
            },
            "controls": {"paused": False, "pause_reason": "", "focused_branch_id": 2, "branch_priorities": {"2": 9}, "last_checkpoint_path": None, "last_operator_note": "seguir"},
        }
        self.recorded: list[tuple[str, str]] = []

    def get_runtime_state_snapshot(self):
        return self.snapshot

    def queue_recursive_command(self, command_type: str, *, payload=None, status="queued", branch_id=None):
        entry = {"command_id": 1, "command_type": command_type, "payload": payload or {}, "status": status, "branch_id": branch_id}
        self.snapshot["recursive_session"]["commands"].append(entry)
        return entry

    def update_recursive_command(self, command_id: int, *, status: str, outcome=None):
        self.snapshot["recursive_session"]["commands"][-1]["status"] = status
        self.snapshot["recursive_session"]["commands"][-1]["outcome"] = outcome or {}
        return self.snapshot["recursive_session"]["commands"][-1]

    def set_runtime_focus(self, branch_id: int, *, fixed: bool = False, reason: str = "", origin: str = "operator"):
        self.snapshot["controls"]["focused_branch_id"] = branch_id
        if fixed:
            self.snapshot["coordination"]["latest_parallel_summary"]["winner_branch_id"] = branch_id
        return self.snapshot["controls"]

    def record_recursive_message(self, role: str, content: str, metadata=None):
        self.recorded.append((role, content))
        self.snapshot["recursive_session"]["messages"].append({"role": role, "content": content})
        return self.snapshot["recursive_session"]["messages"][-1]


def _make_session():
    env = _DummyEnv()
    return SimpleNamespace(
        session_id="runtime-tui-demo",
        client_id="tui:demo",
        status="idle",
        state_dir="./rlm_states/runtime-tui-demo",
        metadata={},
        last_error="",
        rlm_instance=SimpleNamespace(
            _persistent_env=env,
            _record_recursive_message=env.record_recursive_message,
            _rlm=SimpleNamespace(backend_kwargs={"model_name": "gpt-4o-mini"}),
            save_state=lambda path: path,
        ),
    )


def test_apply_operator_command_updates_focus() -> None:
    from rlm.core.operator_surface import apply_operator_command

    session = _make_session()
    manager = _DummySessionManager(session)

    entry, runtime = apply_operator_command(
        manager,
        session,
        supervisor=None,
        command_type="focus_branch",
        payload={"note": "olhar branch 2"},
        branch_id=2,
        origin="tui",
    )

    assert entry["status"] == "completed"
    assert runtime["controls"]["focused_branch_id"] == 2
    assert manager.events[-1]["event_type"] == "tui_command_applied"


def test_dispatch_operator_prompt_records_response() -> None:
    from rlm.core.operator_surface import dispatch_operator_prompt

    session = _make_session()
    manager = _DummySessionManager(session)

    class _Supervisor:
        def is_running(self, session_id: str) -> bool:
            return False

        def execute_async(self, session_obj, prompt: str, on_complete=None):
            on_complete(SimpleNamespace(status="completed", response="resultado final", error_detail="", abort_reason="", execution_time=1.2))
            return session_obj.session_id

    dispatch_operator_prompt(manager, _Supervisor(), session, text="analise a sessao", origin="tui")

    assert session.metadata["last_operator_status"] == "completed"
    assert session.metadata["last_operator_response"] == "resultado final"
    assert session.rlm_instance._persistent_env.recorded[0] == ("user", "analise a sessao")
    assert session.rlm_instance._persistent_env.recorded[1] == ("assistant", "resultado final")
    assert manager.events[-1]["event_type"] == "tui_response_ready"


def test_dispatch_operator_prompt_uses_runtime_pipeline_when_available() -> None:
    from rlm.core.operator_surface import dispatch_operator_prompt

    session = _make_session()
    manager = _DummySessionManager(session)
    runtime_services = object()
    called: list[dict] = []

    def fake_dispatch(services, client_id, payload, **kwargs):
        called.append({
            "services": services,
            "client_id": client_id,
            "payload": payload,
            "kwargs": kwargs,
        })
        kwargs["on_complete"]({
            "status": "completed",
            "response": "resultado roteado",
            "execution_time": 0.4,
            "error_detail": None,
            "abort_reason": None,
        }, session)
        return {"status": "completed", "session_id": session.session_id, "response": "resultado roteado"}

    from unittest.mock import patch

    with patch("rlm.server.runtime_pipeline.dispatch_runtime_prompt_sync", side_effect=fake_dispatch):
        dispatch_operator_prompt(
            manager,
            MagicMock(is_running=lambda session_id: False),
            session,
            text="roteie via pipeline",
            origin="tui",
            runtime_services=runtime_services,
            client_id="tui:demo",
        )
        time.sleep(0.05)

    assert called[0]["services"] is runtime_services
    assert called[0]["client_id"] == "tui:demo"
    assert called[0]["payload"]["channel"] == "tui"
    assert called[0]["kwargs"]["record_conversation"] is True
    assert session.metadata["last_operator_response"] == "resultado roteado"


def test_runtime_workbench_once_renders_panels() -> None:
    from rlm.cli.tui import RuntimeWorkbench, WorkbenchRuntime

    session = _make_session()
    manager = _DummySessionManager(session)
    runtime = WorkbenchRuntime(session_manager=manager, supervisor=MagicMock())
    console = Console(record=True, width=140)
    workbench = RuntimeWorkbench(runtime, client_id="tui:demo", console=console)

    workbench.run(once=True)
    rendered = console.export_text()

    assert "Arkhe TUI Workbench" in rendered
    assert "Branch 2" in rendered
    assert "Mensagens E Timeline" in rendered


def test_polled_input_handles_backspace(monkeypatch) -> None:
    from rlm.cli.tui import RuntimeWorkbench, WorkbenchRuntime

    session = _make_session()
    manager = _DummySessionManager(session)
    runtime = WorkbenchRuntime(session_manager=manager, supervisor=MagicMock())
    workbench = RuntimeWorkbench(runtime, client_id="tui:demo", console=Console(record=True, width=120))

    keys = iter(["o", "i", "\b", "á", "\r"])
    fake_msvcrt = SimpleNamespace(
        kbhit=lambda: True,
        getwch=lambda: next(keys),
    )

    monkeypatch.setitem(__import__("sys").modules, "msvcrt", fake_msvcrt)
    line = workbench._poll_input_line()

    assert line == "oá"
    assert workbench._input_buffer == ""