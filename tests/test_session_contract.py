from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from rlm.core.session import SessionManager, SessionRecord
from rlm.plugins.channel_registry import ChannelRegistry


def _patch_activation(monkeypatch) -> None:
    def _fake_activate(self, session, extra_rlm_kwargs):
        session.rlm_instance = SimpleNamespace(client_id=session.client_id)
        self._active_sessions[session.session_id] = session

    monkeypatch.setattr(SessionManager, "_activate_session", _fake_activate)


def test_get_session_uses_explicit_column_order(tmp_path, monkeypatch) -> None:
    _patch_activation(monkeypatch)
    monkeypatch.setenv("RLM_SESSION_SCOPE", "main")

    db_path = tmp_path / "sessions.db"
    state_root = tmp_path / "states"
    manager = SessionManager(db_path=str(db_path), state_root=str(state_root))

    created = manager.get_or_create("telegram:42")
    manager._active_sessions.clear()

    reloaded = SessionManager(db_path=str(db_path), state_root=str(state_root)).get_session(created.session_id)

    assert reloaded is not None
    assert reloaded.user_id == "main"
    assert reloaded.status == "idle"
    assert reloaded.metadata == {}


def test_non_replyable_origin_does_not_replace_delivery_context(tmp_path, monkeypatch) -> None:
    _patch_activation(monkeypatch)
    monkeypatch.setenv("RLM_SESSION_SCOPE", "main")
    monkeypatch.setattr(ChannelRegistry, "_adapters", {"telegram": SimpleNamespace(send_message=lambda *_: True, send_media=lambda *_: True)})

    manager = SessionManager(
        db_path=str(tmp_path / "sessions.db"),
        state_root=str(tmp_path / "states"),
    )

    session = manager.get_or_create("telegram:123")
    assert session.delivery_context["channel"] == "telegram:123"
    assert session.originating_channel == "telegram:123"

    same_session = manager.get_or_create("webchat:browser-1")

    assert same_session.session_id == session.session_id
    assert same_session.originating_channel == "webchat:browser-1"
    assert same_session.delivery_context["channel"] == "telegram:123"


def test_transition_status_writes_operation_log(tmp_path, monkeypatch) -> None:
    _patch_activation(monkeypatch)
    monkeypatch.setenv("RLM_SESSION_SCOPE", "main")

    manager = SessionManager(
        db_path=str(tmp_path / "sessions.db"),
        state_root=str(tmp_path / "states"),
    )
    session = manager.get_or_create("tui:demo")

    changed = manager.transition_status(session, "running", source="test", reason="unit")

    assert changed is True
    payload = manager.session_to_dict(session)
    assert payload["session_status"] == "running"
    assert payload["originating_channel"] == "tui:demo"

    operations = manager.get_operation_log(session.session_id, limit=10)
    assert any(
        item["operation"] == "session.status" and item["status"] == "running"
        for item in operations
    )


def test_activate_session_warms_daemon_immediately(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RLM_SESSION_SCOPE", "main")

    class FakeRLMSession:
        def __init__(self, **_kwargs):
            self._rlm = SimpleNamespace()

    import rlm.session as rlm_session_module

    monkeypatch.setattr(rlm_session_module, "RLMSession", FakeRLMSession)

    warmed: list[str] = []
    manager = SessionManager(
        db_path=str(tmp_path / "sessions.db"),
        state_root=str(tmp_path / "states"),
        recursion_daemon=SimpleNamespace(
            warm_session=lambda session: warmed.append(session.session_id),
        ),
    )

    state_dir = tmp_path / "states" / "sess-1"
    state_dir.mkdir(parents=True, exist_ok=True)
    session = SessionRecord(
        session_id="sess-1",
        client_id="tui:demo",
        user_id="main",
        created_at="2026-04-10T00:00:00+00:00",
        last_active="2026-04-10T00:00:00+00:00",
        state_dir=str(state_dir),
    )

    manager._activate_session(session, {})

    assert session.rlm_instance is not None
    assert warmed == ["sess-1"]


def test_prune_idle_sessions_closes_stale_in_memory_session(tmp_path, monkeypatch) -> None:
    _patch_activation(monkeypatch)
    monkeypatch.setenv("RLM_SESSION_SCOPE", "main")

    manager = SessionManager(
        db_path=str(tmp_path / "sessions.db"),
        state_root=str(tmp_path / "states"),
    )
    session = manager.get_or_create("tui:demo")
    session.last_active = "2026-04-10T00:00:00+00:00"
    session.rlm_instance = SimpleNamespace(save_state=lambda *_args, **_kwargs: None, close=lambda: None)

    pruned = manager.prune_idle_sessions(
        idle_timeout_s=60.0,
        now_ts=datetime(2026, 4, 10, 0, 5, 0, tzinfo=timezone.utc).timestamp(),
    )

    assert pruned == [session.session_id]
    assert session.session_id not in manager._active_sessions


def test_prune_idle_sessions_ignores_recent_session(tmp_path, monkeypatch) -> None:
    _patch_activation(monkeypatch)
    monkeypatch.setenv("RLM_SESSION_SCOPE", "main")

    manager = SessionManager(
        db_path=str(tmp_path / "sessions.db"),
        state_root=str(tmp_path / "states"),
    )
    session = manager.get_or_create("tui:demo")
    session.last_active = "2026-04-10T00:04:30+00:00"
    session.rlm_instance = SimpleNamespace(save_state=lambda *_args, **_kwargs: None, close=lambda: None)

    pruned = manager.prune_idle_sessions(
        idle_timeout_s=60.0,
        now_ts=datetime(2026, 4, 10, 0, 5, 0, tzinfo=timezone.utc).timestamp(),
    )

    assert pruned == []
    assert session.session_id in manager._active_sessions


def test_prune_idle_sessions_preserves_live_daemon_session(tmp_path, monkeypatch) -> None:
    _patch_activation(monkeypatch)
    monkeypatch.setenv("RLM_SESSION_SCOPE", "main")

    daemon = SimpleNamespace(warm_session=lambda session: None)
    manager = SessionManager(
        db_path=str(tmp_path / "sessions.db"),
        state_root=str(tmp_path / "states"),
        recursion_daemon=daemon,
    )
    session = manager.get_or_create("tui:demo")
    session.last_active = "2026-04-10T00:00:00+00:00"
    session.rlm_instance = SimpleNamespace(save_state=lambda *_args, **_kwargs: None, close=lambda: None)
    daemon.should_preserve_session = lambda current: current.session_id == session.session_id

    pruned = manager.prune_idle_sessions(
        idle_timeout_s=60.0,
        now_ts=datetime(2026, 4, 10, 0, 5, 0, tzinfo=timezone.utc).timestamp(),
    )

    assert pruned == []
    assert session.session_id in manager._active_sessions


def test_close_session_rebootstraps_live_daemon_runtime(tmp_path, monkeypatch) -> None:
    _patch_activation(monkeypatch)
    monkeypatch.setenv("RLM_SESSION_SCOPE", "main")

    released: list[str] = []
    bootstrapped: list[bool] = []
    daemon = SimpleNamespace(
        warm_session=lambda session: None,
        should_preserve_session=lambda session: True,
        release_session=lambda session: released.append(session.session_id) or True,
        bootstrap_session=lambda: bootstrapped.append(True),
    )
    manager = SessionManager(
        db_path=str(tmp_path / "sessions.db"),
        state_root=str(tmp_path / "states"),
        recursion_daemon=daemon,
    )
    session = manager.get_or_create("tui:demo")
    session.rlm_instance = SimpleNamespace(save_state=lambda *_args, **_kwargs: None, close=lambda: None)

    closed = manager.close_session(session.session_id)

    assert closed is True
    assert released == [session.session_id]
    assert bootstrapped == [True]


def test_summarize_inbound_payload_keeps_safe_fields_for_local_history() -> None:
    from rlm.server.api import _summarize_inbound_payload

    payload = {
        "text": "x" * 520,
        "from_user": "demet\ud800",
        "chat_id": 42,
        "extra": "ignored",
    }

    summary = _summarize_inbound_payload("telegram:42", payload)

    assert summary["client_id"] == "telegram:42"
    assert summary["channel"] == "telegram"
    assert summary["chat_id"] == 42
    assert summary["payload_size"] > 0
    assert summary["from_user"].startswith("demet")
    assert summary["text_preview"].endswith("...[truncado]")
    assert len(summary["text_preview"]) < 500