"""Testes do dual-mode TUI (live/local) sem LLM real nem rede."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console


# ---------------------------------------------------------------------------
# Fakes leves — zero network, zero LLM
# ---------------------------------------------------------------------------

class _FakeSessionManager:
    def __init__(self, session) -> None:
        self.session = session
        self.events: list[dict] = []

    def get_or_create(self, client_id: str):
        return self.session

    def get_session(self, session_id: str):
        if session_id == self.session.session_id:
            return self.session
        return None

    def session_to_dict(self, session):
        return {
            "session_id": session.session_id,
            "client_id": session.client_id,
            "status": session.status,
            "created_at": "2026-01-01T00:00:00+00:00",
            "last_active": "2026-01-01T00:00:00+00:00",
            "total_completions": 0,
            "total_tokens_used": 0,
            "last_error": "",
            "metadata": getattr(session, "metadata", {}),
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
        pass

    def transition_status(self, session, new_status: str, **kw) -> bool:
        session.status = new_status
        return True


class _FakeLiveAPI:
    """Substitui LiveWorkbenchAPI — sem HTTP real."""

    def __init__(self, *, probe_result: bool = True, session_id: str = "live-sess-1"):
        self._probe_result = probe_result
        self._session_id = session_id
        self.prompts: list[dict] = []
        self.commands: list[dict] = []
        self._activity: dict[str, Any] = {
            "session": {"session_id": session_id, "client_id": "tui:test", "status": "idle"},
            "event_log": [],
            "runtime": None,
        }

    @property
    def base_url(self) -> str:
        return "http://fake:9999"

    def probe(self, *, timeout: int = 3) -> bool:
        return self._probe_result

    def ensure_session(self, client_id: str):
        from rlm.cli.tui.live_api import LiveSessionInfo
        return LiveSessionInfo(
            session_id=self._session_id,
            client_id=client_id,
            status="idle",
            state_dir="/tmp/fake",
        )

    def fetch_activity(self, session_id: str) -> dict[str, Any]:
        return dict(self._activity)

    def dispatch_prompt(self, session_id: str, client_id: str, text: str) -> dict[str, Any]:
        self.prompts.append({"session_id": session_id, "client_id": client_id, "text": text})
        return {"status": "accepted"}

    def apply_command(self, session_id: str, *, client_id: str, command_type: str, payload: dict, branch_id: int | None) -> dict[str, Any]:
        self.commands.append({"command_type": command_type, "payload": payload, "branch_id": branch_id})
        return {"command": {"command_type": command_type, "command_id": 42}}


def _make_dummy_env():
    return SimpleNamespace(
        get_runtime_state_snapshot=lambda: {
            "tasks": {"current": {"title": "-", "status": "-", "note": ""}, "items": []},
            "attachments": {"items": []},
            "timeline": {"entries": []},
            "recursive_session": {"messages": [], "commands": [], "events": []},
            "coordination": {"events": [], "branch_tasks": [], "latest_parallel_summary": {}},
            "controls": {"paused": False, "pause_reason": "", "focused_branch_id": None,
                         "branch_priorities": {}, "last_checkpoint_path": None, "last_operator_note": ""},
        },
        queue_recursive_command=lambda *a, **kw: {"command_id": 1, "command_type": "noop", "payload": {}, "status": "queued"},
        update_recursive_command=lambda *a, **kw: {},
        set_runtime_paused=lambda *a, **kw: {},
        set_runtime_focus=lambda *a, **kw: {},
        record_recursive_message=lambda *a, **kw: {},
    )


def _make_local_session():
    env = _make_dummy_env()
    return SimpleNamespace(
        session_id="local-test-1",
        client_id="tui:test",
        status="idle",
        state_dir="./rlm_states/local-test-1",
        metadata={},
        last_error="",
        rlm_instance=SimpleNamespace(
            _persistent_env=env,
            _record_recursive_message=env.record_recursive_message,
            _rlm=SimpleNamespace(backend_kwargs={"model_name": "mock-model"}),
            save_state=lambda path: path,
        ),
    )


# =========================================================================
# 1) TuiAdapter — cross-channel delivery sem rede
# =========================================================================

class TestTuiAdapter:
    def test_send_message_logs_event(self):
        from rlm.gateway.operator_bridge import TuiAdapter

        session = _make_local_session()
        sm = _FakeSessionManager(session)
        adapter = TuiAdapter(sm)

        ok = adapter.send_message("default", "Olá do Telegram!")
        assert ok is True
        assert len(sm.events) == 1
        ev = sm.events[0]
        assert ev["event_type"] == "tui_response_delivered"
        assert ev["payload"]["response_preview"] == "Olá do Telegram!"
        assert ev["payload"]["delivery_source"] == "message_bus"
        assert ev["payload"]["target_channel"] == "tui"

    def test_send_message_truncates_at_500(self):
        from rlm.gateway.operator_bridge import TuiAdapter

        session = _make_local_session()
        sm = _FakeSessionManager(session)
        adapter = TuiAdapter(sm)

        big_text = "X" * 1000
        adapter.send_message("default", big_text)
        assert len(sm.events[0]["payload"]["response_preview"]) == 500

    def test_send_media_converts_to_text(self):
        from rlm.gateway.operator_bridge import TuiAdapter

        session = _make_local_session()
        sm = _FakeSessionManager(session)
        adapter = TuiAdapter(sm)

        ok = adapter.send_media("user1", "/imgs/foto.png", caption="screenshot")
        assert ok is True
        preview = sm.events[0]["payload"]["response_preview"]
        assert "[Midia: /imgs/foto.png]" in preview
        assert "screenshot" in preview

    def test_send_message_returns_false_on_error(self):
        from rlm.gateway.operator_bridge import TuiAdapter

        sm = MagicMock()
        sm.get_or_create.side_effect = RuntimeError("db down")
        adapter = TuiAdapter(sm)

        ok = adapter.send_message("user1", "oi")
        assert ok is False


# =========================================================================
# 2) LiveWorkbenchAPI — probe e request sem rede
# =========================================================================

class TestLiveWorkbenchAPI:
    def _make_api(self, *, host: str = "http://127.0.0.1:9999"):
        ctx = MagicMock()
        ctx.env = {"RLM_INTERNAL_HOST": host, "RLM_INTERNAL_TOKEN": "test-tok-123"}
        ctx.api_base_url.return_value = host
        from rlm.cli.tui.live_api import LiveWorkbenchAPI
        return LiveWorkbenchAPI(ctx)

    def test_probe_returns_false_when_unreachable(self):
        api = self._make_api(host="http://127.0.0.1:1")  # porta impossível
        assert api.probe(timeout=1) is False

    def test_probe_returns_true_on_200(self):
        api = self._make_api()
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("rlm.cli.tui.live_api.urequest.urlopen", return_value=fake_resp):
            assert api.probe() is True

    def test_ensure_session_parses_response(self):
        api = self._make_api()
        payload = json.dumps({
            "session_id": "s-123",
            "client_id": "tui:demo",
            "status": "idle",
            "state_dir": "/tmp/s",
            "metadata": {"key": "val"},
        }).encode()
        fake_resp = MagicMock()
        fake_resp.read.return_value = payload
        fake_resp.status = 200
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("rlm.cli.tui.live_api.urequest.urlopen", return_value=fake_resp):
            info = api.ensure_session("tui:demo")

        assert info.session_id == "s-123"
        assert info.client_id == "tui:demo"
        assert info.metadata == {"key": "val"}

    def test_request_json_raises_on_http_error(self):
        from urllib.error import HTTPError
        from rlm.cli.tui.live_api import LiveWorkbenchError

        api = self._make_api()
        exc = HTTPError("http://x", 500, "err", {}, None)
        exc.read = lambda: b"internal server error"

        with patch("rlm.cli.tui.live_api.urequest.urlopen", side_effect=exc):
            with pytest.raises(LiveWorkbenchError, match="HTTP 500"):
                api.fetch_activity("s-1")

    def test_request_json_raises_on_url_error(self):
        from urllib.error import URLError
        from rlm.cli.tui.live_api import LiveWorkbenchError

        api = self._make_api()
        with patch("rlm.cli.tui.live_api.urequest.urlopen", side_effect=URLError("refused")):
            with pytest.raises(LiveWorkbenchError, match="indisponivel"):
                api.dispatch_prompt("s-1", "tui:x", "oi")

    def test_headers_include_token(self):
        api = self._make_api()
        assert api._headers.get("X-RLM-Token") == "test-tok-123"
        assert api._headers.get("Content-Type") == "application/json"

    def test_headers_omit_token_when_empty(self):
        ctx = MagicMock()
        ctx.env = {"RLM_INTERNAL_HOST": "http://x"}
        ctx.api_base_url.return_value = "http://x"
        from rlm.cli.tui.live_api import LiveWorkbenchAPI
        api = LiveWorkbenchAPI(ctx)
        assert "X-RLM-Token" not in api._headers


# =========================================================================
# 3) RuntimeWorkbench — dual-mode (live vs local)
# =========================================================================

class TestRuntimeWorkbenchLiveMode:
    def test_live_mode_sets_is_live(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        console = Console(record=True, width=120)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        assert wb._is_live is True
        assert wb.session.session_id == "live-sess-1"

    def test_live_mode_once_renders(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        console = Console(record=True, width=120)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        result = wb.run(once=True)
        rendered = console.export_text()

        assert result == 0
        assert "Arkhe TUI Workbench" in rendered
        assert "Modo: live" in rendered

    def test_live_dispatch_calls_api(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        # Make watch_until_idle a no-op (status already idle)
        console = Console(record=True, width=120)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        wb._dispatch_prompt("Olá mundo")

        assert len(fake_api.prompts) == 1
        assert fake_api.prompts[0]["text"] == "Olá mundo"
        assert fake_api.prompts[0]["session_id"] == "live-sess-1"

    def test_live_operator_command_calls_api(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        console = Console(record=True, width=120)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        should_exit = wb._handle_operator_command("/pause motivo teste")
        assert should_exit is False
        assert len(fake_api.commands) == 1
        assert fake_api.commands[0]["command_type"] == "pause_runtime"
        assert "motivo teste" in fake_api.commands[0]["payload"]["reason"]

    def test_live_fetch_activity_returns_dict(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        console = Console(record=True, width=120)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        activity = wb._fetch_activity()
        assert "session" in activity
        assert "event_log" in activity


class TestRuntimeWorkbenchLocalMode:
    def test_local_mode_sets_is_live_false(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench
        from rlm.cli.tui.runtime_factory import WorkbenchRuntime

        session = _make_local_session()
        sm = _FakeSessionManager(session)
        runtime = WorkbenchRuntime(session_manager=sm, supervisor=MagicMock())
        console = Console(record=True, width=120)
        wb = RuntimeWorkbench(runtime, client_id="tui:test", console=console)

        assert wb._is_live is False
        assert wb.session.session_id == "local-test-1"

    def test_local_once_renders_local_mode(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench
        from rlm.cli.tui.runtime_factory import WorkbenchRuntime

        session = _make_local_session()
        sm = _FakeSessionManager(session)
        runtime = WorkbenchRuntime(session_manager=sm, supervisor=MagicMock())
        console = Console(record=True, width=120)
        wb = RuntimeWorkbench(runtime, client_id="tui:test", console=console)

        result = wb.run(once=True)
        rendered = console.export_text()

        assert result == 0
        assert "Modo: local" in rendered

    def test_local_fetch_activity_uses_build_activity_payload(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench
        from rlm.cli.tui.runtime_factory import WorkbenchRuntime

        session = _make_local_session()
        sm = _FakeSessionManager(session)
        runtime = WorkbenchRuntime(session_manager=sm, supervisor=MagicMock())
        console = Console(record=True, width=120)
        wb = RuntimeWorkbench(runtime, client_id="tui:test", console=console)

        activity = wb._fetch_activity()
        assert "session" in activity
        assert "event_log" in activity


# =========================================================================
# 4) run_workbench — probe → live, fallback → local
# =========================================================================

class TestRunWorkbenchEntryPoint:
    def test_live_path_when_probe_succeeds(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI(probe_result=True)
        calls: list[str] = []

        def fake_run(self_wb, *, once=False):
            calls.append("live" if self_wb._is_live else "local")
            return 0

        ctx = MagicMock()
        ctx.env = {"RLM_INTERNAL_HOST": "http://fake:9999", "RLM_INTERNAL_TOKEN": "tok"}
        ctx.api_base_url.return_value = "http://fake:9999"

        with patch("rlm.cli.tui.live_api.LiveWorkbenchAPI", return_value=fake_api), \
             patch.object(RuntimeWorkbench, "run", fake_run):
            from rlm.cli.commands.workbench import run_workbench
            result = run_workbench(ctx, client_id="tui:test", refresh_interval=0.75, once=True)

        assert result == 0
        assert calls == ["live"]

    def test_fallback_local_when_probe_fails(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI(probe_result=False)
        calls: list[str] = []

        def fake_run(self_wb, *, once=False):
            calls.append("live" if self_wb._is_live else "local")
            return 0

        ctx = MagicMock()
        ctx.env = {"RLM_INTERNAL_HOST": "http://fake:9999"}
        ctx.api_base_url.return_value = "http://fake:9999"

        mock_runtime = MagicMock()
        mock_runtime.session_manager = _FakeSessionManager(_make_local_session())

        with patch("rlm.cli.tui.live_api.LiveWorkbenchAPI", return_value=fake_api), \
             patch("rlm.cli.commands.workbench.build_local_workbench_runtime", return_value=mock_runtime), \
             patch.object(RuntimeWorkbench, "run", fake_run):
            from rlm.cli.commands.workbench import run_workbench
            result = run_workbench(ctx, client_id="tui:test", refresh_interval=0.75, once=True)

        assert result == 0
        assert calls == ["local"]

    def test_fallback_local_when_probe_raises(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        calls: list[str] = []

        def fake_run(self_wb, *, once=False):
            calls.append("live" if self_wb._is_live else "local")
            return 0

        ctx = MagicMock()
        ctx.env = {}
        ctx.api_base_url.return_value = "http://fake:9999"

        mock_api_cls = MagicMock(side_effect=RuntimeError("import failed"))
        mock_runtime = MagicMock()
        mock_runtime.session_manager = _FakeSessionManager(_make_local_session())

        with patch("rlm.cli.tui.live_api.LiveWorkbenchAPI", mock_api_cls), \
             patch("rlm.cli.commands.workbench.build_local_workbench_runtime", return_value=mock_runtime), \
             patch.object(RuntimeWorkbench, "run", fake_run):
            from rlm.cli.commands.workbench import run_workbench
            result = run_workbench(ctx, client_id="tui:test", refresh_interval=0.75, once=True)

        assert result == 0
        assert calls == ["local"]


# =========================================================================
# 5) LiveSession dataclass — proxy behavior
# =========================================================================

class TestLiveSession:
    def test_live_session_has_no_rlm_instance(self):
        from rlm.cli.commands.workbench import LiveSession
        ls = LiveSession(session_id="s1", client_id="tui:x")
        assert ls.rlm_instance is None
        assert ls.status == "idle"
        assert ls.metadata == {}


# =========================================================================
# 6) Translate operator commands
# =========================================================================

class TestTranslateOperatorCommands:
    def _make_workbench(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench
        from rlm.cli.tui.runtime_factory import WorkbenchRuntime

        session = _make_local_session()
        sm = _FakeSessionManager(session)
        runtime = WorkbenchRuntime(session_manager=sm, supervisor=MagicMock())
        return RuntimeWorkbench(runtime, client_id="tui:test", console=Console(record=True, width=120))

    def test_pause(self):
        wb = self._make_workbench()
        cmd_type, payload, branch = wb._translate_operator_command(["/pause", "motivo"])
        assert cmd_type == "pause_runtime"
        assert "motivo" in payload["reason"]
        assert branch is None

    def test_focus_requires_branch_id(self):
        wb = self._make_workbench()
        with pytest.raises(ValueError, match="branch_id"):
            wb._translate_operator_command(["/focus"])

    def test_winner(self):
        wb = self._make_workbench()
        cmd_type, payload, branch = wb._translate_operator_command(["/winner", "3"])
        assert cmd_type == "fix_winner_branch"
        assert branch == 3

    def test_priority_requires_three_args(self):
        wb = self._make_workbench()
        with pytest.raises(ValueError, match="branch_id"):
            wb._translate_operator_command(["/priority", "1"])

    def test_note_requires_text(self):
        wb = self._make_workbench()
        with pytest.raises(ValueError, match="texto"):
            wb._translate_operator_command(["/note"])

    def test_unknown_command_raises(self):
        wb = self._make_workbench()
        with pytest.raises(ValueError, match="desconhecido"):
            wb._translate_operator_command(["/naoexiste"])

    def test_help_does_not_exit(self):
        wb = self._make_workbench()
        should_exit = wb._handle_operator_command("/help")
        assert should_exit is False
        assert "Comandos:" in wb.last_notice

    def test_quit_exits(self):
        wb = self._make_workbench()
        should_exit = wb._handle_operator_command("/quit")
        assert should_exit is True


# =========================================================================
# 7) __init__.py lazy import — verifica que não há ciclo
# =========================================================================

class TestLazyImport:
    def test_import_run_workbench_from_tui_package(self):
        from rlm.cli.tui import run_workbench
        assert callable(run_workbench)

    def test_import_runtime_workbench_from_tui_package(self):
        from rlm.cli.tui import RuntimeWorkbench
        assert RuntimeWorkbench is not None

    def test_import_workbench_runtime_is_eager(self):
        from rlm.cli.tui import WorkbenchRuntime
        assert WorkbenchRuntime is not None

    def test_unknown_attr_raises(self):
        with pytest.raises(AttributeError, match="no attribute"):
            from rlm.cli import tui
            tui.nao_existe  # noqa: B018
