from __future__ import annotations

from types import SimpleNamespace

from rlm.core.session import SessionManager
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