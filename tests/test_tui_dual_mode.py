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

    def __init__(self, *, probe_result: bool = True, session_id: str = "live-sess-1", ensure_sequence: list[str] | None = None):
        self._probe_result = probe_result
        self._session_id = session_id
        self._ensure_sequence = list(ensure_sequence or [session_id])
        self.ensure_calls: list[str] = []
        self.fetch_calls: list[str] = []
        self.fetch_errors: list[Exception] = []
        self.prompt_errors: list[Exception] = []
        self.command_errors: list[Exception] = []
        self.prompts: list[dict] = []
        self.commands: list[dict] = []
        self._activity: dict[str, Any] = {
            "session": {"session_id": session_id, "client_id": "tui:test", "status": "idle", "metadata": {}},
            "event_log": [],
            "operation_log": [],
            "runtime": None,
        }

    @property
    def base_url(self) -> str:
        return "http://fake:9999"

    def probe(self, *, timeout: int = 3) -> bool:
        return self._probe_result

    def ensure_session(self, client_id: str):
        from rlm.cli.tui.live_api import LiveSessionInfo
        self.ensure_calls.append(client_id)
        if self._ensure_sequence:
            self._session_id = self._ensure_sequence.pop(0)
        return LiveSessionInfo(
            session_id=self._session_id,
            client_id=client_id,
            status="idle",
            state_dir="/tmp/fake",
        )

    def fetch_activity(self, session_id: str) -> dict[str, Any]:
        self.fetch_calls.append(session_id)
        if self.fetch_errors:
            raise self.fetch_errors.pop(0)
        payload = dict(self._activity)
        session = dict(payload.get("session") or {})
        session.setdefault("session_id", self._session_id)
        session.setdefault("client_id", "tui:test")
        payload["session"] = session
        return payload

    def dispatch_prompt(self, session_id: str, client_id: str, text: str) -> dict[str, Any]:
        if self.prompt_errors:
            raise self.prompt_errors.pop(0)
        self.prompts.append({"session_id": session_id, "client_id": client_id, "text": text})
        return {"status": "accepted"}

    def apply_command(self, session_id: str, *, client_id: str, command_type: str, payload: dict, branch_id: int | None) -> dict[str, Any]:
        if self.command_errors:
            raise self.command_errors.pop(0)
        self.commands.append({"command_type": command_type, "payload": payload, "branch_id": branch_id})
        return {"command": {"command_type": command_type, "command_id": 42}}

    def fetch_channels_status(self) -> dict[str, Any]:
        return {"channels": {}}

    def probe_channel(self, channel_id: str) -> dict[str, Any]:
        return {"status": "ok", "channel_id": channel_id}

    def cross_channel_send(self, target_client_id: str, message: str) -> dict[str, Any]:
        return {"status": "ok", "target_client_id": target_client_id, "message": message}


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
        from rlm.server.operator_bridge import TuiAdapter

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
        from rlm.server.operator_bridge import TuiAdapter

        session = _make_local_session()
        sm = _FakeSessionManager(session)
        adapter = TuiAdapter(sm)

        big_text = "X" * 1000
        adapter.send_message("default", big_text)
        assert len(sm.events[0]["payload"]["response_preview"]) == 500

    def test_send_media_converts_to_text(self):
        from rlm.server.operator_bridge import TuiAdapter

        session = _make_local_session()
        sm = _FakeSessionManager(session)
        adapter = TuiAdapter(sm)

        ok = adapter.send_media("user1", "/imgs/foto.png", caption="screenshot")
        assert ok is True
        preview = sm.events[0]["payload"]["response_preview"]
        assert "[Midia: /imgs/foto.png]" in preview
        assert "screenshot" in preview

    def test_send_message_returns_false_on_error(self):
        from rlm.server.operator_bridge import TuiAdapter

        sm = MagicMock()
        sm.get_or_create.side_effect = RuntimeError("db down")
        adapter = TuiAdapter(sm)

        ok = adapter.send_message("user1", "oi")
        assert ok is False


# =========================================================================
# 2) LiveWorkbenchAPI — probe e request sem rede
# =========================================================================

class TestLiveWorkbenchAPI:
    def _make_api(
        self,
        *,
        host: str = "http://127.0.0.1:9999",
        operator_host: str | None = None,
        internal_host: str | None = None,
    ):
        ctx = MagicMock()
        ctx.env = {"RLM_INTERNAL_TOKEN": "test-tok-123"}
        if operator_host is not None:
            ctx.env["RLM_OPERATOR_HOST"] = operator_host
        if internal_host is not None:
            ctx.env["RLM_INTERNAL_HOST"] = internal_host
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

    def test_prefers_operator_host_over_internal_host(self):
        api = self._make_api(
            host="http://127.0.0.1:5000",
            operator_host="http://127.0.0.1:6000",
            internal_host="http://bridge.internal:7777",
        )

        assert api.base_url == "http://127.0.0.1:6000"

    def test_ignores_internal_host_when_operator_host_missing(self):
        api = self._make_api(
            host="http://0.0.0.0:5000",
            internal_host="http://bridge.internal:7777",
        )

        assert api.base_url == "http://127.0.0.1:5000"

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

    def test_request_json_sanitizes_surrogates_in_body(self):
        api = self._make_api()
        captured: dict[str, bytes] = {}
        fake_resp = MagicMock()
        fake_resp.read.return_value = b'{"status": "ok"}'
        fake_resp.status = 200
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = MagicMock(return_value=False)

        def _capture(req, timeout=10):
            captured["data"] = req.data
            return fake_resp

        with patch("rlm.cli.tui.live_api.urequest.urlopen", side_effect=_capture):
            api.cross_channel_send("telegram:42\udcc3", "ok\udcc3")

        payload = json.loads(captured["data"].decode("utf-8"))
        assert "\udcc3" not in payload["target_client_id"]
        assert "\udcc3" not in payload["message"]

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

    def test_live_mode_renders_channel_context(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        fake_api._activity["session"] = {
            "session_id": "live-sess-1",
            "client_id": "tui:test",
            "status": "running",
            "last_activity_at": "2026-04-10T10:00:00+00:00",
            "metadata": {
                "_channel_context": {
                    "transport": "brain_router",
                    "source_name": "brain_router",
                    "actor": "cli",
                    "origin_session_id": "cli-session-9",
                    "session_origin": "brain_prompt",
                }
            },
        }
        console = Console(record=True, width=160)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        wb.run(once=True)
        rendered = console.export_text()

        assert "Transport: brain_router" in rendered
        assert "Source: brain_router" in rendered
        assert "Actor: cli" in rendered
        assert "Origem: cli-session-9" in rendered
        assert "OrigemSessao: brain_prompt" in rendered

    def test_live_mode_prefers_runtime_recursion_projection(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        fake_api._activity["runtime"] = {
            "recursion": {
                "controls": {
                    "paused": True,
                    "pause_reason": "operador",
                    "focused_branch_id": 3,
                    "branch_priorities": {"3": 9},
                    "last_checkpoint_path": "/tmp/ckpt.json",
                    "last_operator_note": "seguir branch 3",
                },
                "summary": {"winner_branch_id": 7},
                "branches": [
                    {
                        "branch_id": 3,
                        "title": "diagnosticar",
                        "mode": "parallel",
                        "status": "running",
                        "operator_priority": 9,
                        "metadata": {"role": "worker"},
                    },
                    {
                        "branch_id": 7,
                        "title": "responder",
                        "mode": "parallel",
                        "status": "completed",
                        "operator_fixed_winner": True,
                        "metadata": {},
                    },
                ],
                "events": [{"operation": "fanout", "payload_preview": "spawned"}],
            },
            "tasks": {"current": {"title": "macro", "status": "running", "note": "aguardando"}},
            "recursive_session": {"messages": [], "events": []},
            "timeline": {"entries": []},
        }
        console = Console(record=True, width=180)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        wb.run(once=True)
        rendered = console.export_text()

        assert "Focus: 3" in rendered
        assert "Winner: 7" in rendered
        assert "diagnosticar" in rendered
        assert "focus" in rendered
        assert "prio=9" in rendered
        assert "Pause reason: operador" in rendered

    def test_live_mode_renders_operation_log_entries(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        fake_api._activity["operation_log"] = [
            {
                "operation": "session.status",
                "status": "running",
                "source": "supervisor",
                "payload": {"reason": "unit"},
            },
            {
                "operation": "dispatch.prompt",
                "status": "completed",
                "source": "daemon",
                "payload": {"response_preview": "ok"},
            },
        ]
        fake_api._activity["runtime"] = {
            "recursion": {"controls": {}, "summary": {}, "branches": [], "events": []},
            "recursive_session": {"messages": [], "events": []},
            "timeline": {"entries": []},
        }
        console = Console(record=True, width=180)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        wb.run(once=True)
        rendered = console.export_text()

        assert "Ultima operacao" in rendered
        assert "dispatch.prompt/completed" in rendered
        assert "ok" in rendered

    def test_live_mode_renders_daemon_status(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        fake_api._activity["runtime"] = {
            "daemon": {
                "name": "main",
                "running": True,
                "ready": True,
                "draining": False,
                "inflight_dispatches": 2,
                "active_sessions": 2,
                "stats": {"llm_invoked": 4, "deterministic_used": 3, "task_agent_invoked": 1},
                "warm_runtime": {"requests": 7, "warmed": 2, "already_warm": 5, "failed": 0},
                "outbox": {"pending": 3, "delivering": 1, "delivered": 5, "failed": 0, "dlq": 1, "backlog": 4, "worker_alive": True},
                "channel_runtime": {"total": 3, "running": 2, "healthy": 2, "registered_channels": ["telegram", "tui", "webchat"]},
                "memory_access": {
                    "recall_requests": 8,
                    "recall_hits": 5,
                    "session_blocks": 4,
                    "workspace_blocks": 3,
                    "kb_blocks": 2,
                    "post_turn_requests": 6,
                    "post_turn_delegated": 6,
                    "episodic_writes": 4,
                    "last_scope": {
                        "channel": "tui",
                        "actor": "cli",
                        "active_channels": ["tui", "telegram"],
                        "workspace_scope": "workspace::repo-main",
                        "agent_depth": 2,
                        "branch_id": 7,
                        "agent_role": "child_parallel",
                        "parent_session_id": "sess-root",
                    },
                },
            },
            "recursion": {"controls": {}, "summary": {}, "branches": [], "events": []},
            "recursive_session": {"messages": [], "events": []},
            "timeline": {"entries": []},
        }
        console = Console(record=True, width=220)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        wb.run(once=True)
        rendered = console.export_text()

        assert "Daemon: ready" in rendered
        assert "Inflight: 2" in rendered
        assert "LLM: 4" in rendered
        assert "Memory: recall=8" in rendered
        assert "episodic=4" in rendered
        assert "Warm runtime: req=7" in rendered
        assert "Flow: sessions=2" in rendered
        assert "backlog=4" in rendered
        assert "Memory scope: channel=tui" in rendered
        assert "branch=7" in rendered

    def test_live_mode_sanitizes_surrogates_in_activity_and_channels(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        fake_api._activity["session"] = {
            "session_id": "live-sess-1",
            "client_id": "tui:test",
            "status": "idle",
            "metadata": {"last_operator_response": "ok\udcc3"},
        }
        fake_api.fetch_channels_status = lambda: {
            "channels": {
                "telegram": {
                    "channel_id": "telegram",
                    "account_id": "default",
                    "configured": True,
                    "running": True,
                    "healthy": True,
                    "identity": {"display_name": "bot\udcc3"},
                }
            }
        }
        console = Console(record=True, width=180)
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=console)

        wb.run(once=True)
        rendered = console.export_text()

        assert "Resposta mais recente:" in rendered
        assert "Canais" in rendered
        assert "ok?" in rendered
        assert "\udcc3" not in rendered

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

    def test_live_fetch_activity_updates_live_session_snapshot(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI()
        fake_api._activity["session"] = {
            "session_id": "live-sess-1",
            "client_id": "tui:test",
            "status": "running",
            "last_activity_at": "2026-04-10T12:00:00+00:00",
            "metadata": {"_channel_context": {"transport": "brain_router", "actor": "cli"}},
        }
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=Console(record=True, width=120))

        activity = wb._fetch_activity()

        assert activity["session"]["status"] == "running"
        assert wb.session.status == "running"
        assert wb.session.last_activity_at == "2026-04-10T12:00:00+00:00"
        assert wb.session.metadata["_channel_context"]["actor"] == "cli"

    def test_live_fetch_activity_reattaches_when_session_is_missing(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI(ensure_sequence=["live-sess-1", "live-sess-2"])
        fake_api.fetch_errors.append(RuntimeError("HTTP 404 em /operator/session/live-sess-1/activity: Session not found"))
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=Console(record=True, width=120))

        activity = wb._fetch_activity()

        assert wb.session.session_id == "live-sess-2"
        assert fake_api.ensure_calls == ["tui:test", "tui:test"]
        assert fake_api.fetch_calls == ["live-sess-1", "live-sess-2"]
        assert activity["session"]["session_id"] == "live-sess-2"

    def test_live_dispatch_retries_after_reattach(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI(ensure_sequence=["live-sess-1", "live-sess-2"])
        fake_api.prompt_errors.append(RuntimeError("HTTP 404 em /operator/session/live-sess-1/message: Session not found"))
        wb = RuntimeWorkbench(None, client_id="tui:test", live_api=fake_api, console=Console(record=True, width=120))

        with patch.object(RuntimeWorkbench, "watch_until_idle", return_value=None):
            wb._dispatch_prompt("Olá mundo")

        assert wb.session.session_id == "live-sess-2"
        assert wb.session.status == "running"
        assert fake_api.ensure_calls == ["tui:test", "tui:test"]
        assert fake_api.prompts[-1]["session_id"] == "live-sess-2"


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
        ctx.env = {"RLM_OPERATOR_HOST": "http://fake:9999", "RLM_INTERNAL_TOKEN": "tok"}
        ctx.api_base_url.return_value = "http://fake:9999"

        with patch("rlm.cli.tui.live_api.LiveWorkbenchAPI", return_value=fake_api), \
             patch.object(RuntimeWorkbench, "run", fake_run):
            from rlm.cli.commands.workbench import run_workbench
            result = run_workbench(ctx, client_id="tui:test", refresh_interval=0.75, once=True)

        assert result == 0
        assert calls == ["live"]

    def test_autostarts_local_service_and_enters_live_mode_when_probe_fails(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI(probe_result=False)
        fake_api.probe = MagicMock(side_effect=[False, True])
        calls: list[str] = []

        def fake_run(self_wb, *, once=False):
            calls.append("live" if self_wb._is_live else "local")
            return 0

        ctx = MagicMock()
        ctx.env = {"RLM_OPERATOR_HOST": "http://127.0.0.1:5000"}
        ctx.api_base_url.return_value = "http://127.0.0.1:5000"

        with patch("rlm.cli.tui.live_api.LiveWorkbenchAPI", return_value=fake_api), \
             patch("rlm.cli.service.start_services", return_value=0) as start_services, \
             patch("rlm.cli.commands.workbench.build_local_workbench_runtime") as build_local_runtime, \
             patch.object(RuntimeWorkbench, "run", fake_run):
            from rlm.cli.commands.workbench import run_workbench
            result = run_workbench(ctx, client_id="tui:test", refresh_interval=0.75, once=True)

        assert result == 0
        assert calls == ["live"]
        start_services.assert_called_once_with(foreground=False, context=ctx)
        build_local_runtime.assert_not_called()

    def test_autostart_local_service_reuses_live_target_port(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI(probe_result=False)
        fake_api.probe = MagicMock(side_effect=[False, True])
        calls: list[str] = []

        def fake_run(self_wb, *, once=False):
            calls.append("live" if self_wb._is_live else "local")
            return 0

        def fake_start_services(*, foreground=False, context=None):
            assert foreground is False
            assert context is ctx
            assert context.env["RLM_API_HOST"] == "127.0.0.1"
            assert context.env["RLM_API_PORT"] == "5000"
            assert context.env["RLM_OPERATOR_HOST"] == "http://127.0.0.1:5000"
            return 0

        ctx = MagicMock()
        ctx.env = {
            "RLM_OPERATOR_HOST": "http://127.0.0.1:5000",
            "RLM_API_HOST": "127.0.0.1",
            "RLM_API_PORT": "1",
        }
        ctx.api_base_url.return_value = "http://127.0.0.1:1"

        with patch("rlm.cli.tui.live_api.LiveWorkbenchAPI", return_value=fake_api), \
             patch("rlm.cli.service.start_services", side_effect=fake_start_services) as start_services, \
             patch("rlm.cli.commands.workbench.build_local_workbench_runtime") as build_local_runtime, \
             patch.object(RuntimeWorkbench, "run", fake_run):
            from rlm.cli.commands.workbench import run_workbench
            result = run_workbench(ctx, client_id="tui:test", refresh_interval=0.75, once=True)

        assert result == 0
        assert calls == ["live"]
        assert start_services.call_count == 1
        build_local_runtime.assert_not_called()

    def test_local_live_target_does_not_fallback_to_ephemeral_runtime_when_autostart_fails(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI(probe_result=False)
        fake_api.probe = MagicMock(return_value=False)
        calls: list[str] = []

        def fake_run(self_wb, *, once=False):
            calls.append("live" if self_wb._is_live else "local")
            return 0

        ctx = MagicMock()
        ctx.env = {"RLM_OPERATOR_HOST": "http://127.0.0.1:5000"}
        ctx.api_base_url.return_value = "http://127.0.0.1:5000"

        with patch("rlm.cli.tui.live_api.LiveWorkbenchAPI", return_value=fake_api), \
             patch("rlm.cli.service.start_services", return_value=1) as start_services, \
             patch("rlm.cli.commands.workbench.build_local_workbench_runtime") as build_local_runtime, \
             patch.object(RuntimeWorkbench, "run", fake_run):
            from rlm.cli.commands.workbench import run_workbench
            result = run_workbench(ctx, client_id="tui:test", refresh_interval=0.75, once=True)

        assert result == 1
        assert calls == []
        start_services.assert_called_once_with(foreground=False, context=ctx)
        build_local_runtime.assert_not_called()

    def test_fallback_local_when_probe_fails(self):
        from rlm.cli.commands.workbench import RuntimeWorkbench

        fake_api = _FakeLiveAPI(probe_result=False)
        calls: list[str] = []

        def fake_run(self_wb, *, once=False):
            calls.append("live" if self_wb._is_live else "local")
            return 0

        ctx = MagicMock()
        ctx.env = {"RLM_OPERATOR_HOST": "http://fake:9999"}
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
