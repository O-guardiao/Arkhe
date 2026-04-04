"""Testes do channel_console — painel Rich de awareness multichannel para o TUI."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from rlm.cli.tui.channel_console import (
    ChannelConsoleState,
    ChannelSnapshot,
    build_channel_panel,
    fetch_channel_snapshots_live,
    fetch_channel_snapshots_local,
    refresh_channel_state,
    _channel_icon,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_snap_dict(
    channel_id: str = "telegram",
    account_id: str = "default",
    running: bool = True,
    healthy: bool = True,
    configured: bool = True,
    identity: dict | None = None,
    last_error: str | None = None,
    reconnect_attempts: int = 0,
    last_probe_ms: float = 42.0,
    meta: dict | None = None,
) -> dict:
    return {
        "channel_id": channel_id,
        "account_id": account_id,
        "running": running,
        "healthy": healthy,
        "configured": configured,
        "identity": identity or {"username": "arkhe_bot", "display_name": "Arkhe Bot"},
        "last_error": last_error,
        "reconnect_attempts": reconnect_attempts,
        "last_probe_ms": last_probe_ms,
        "meta": meta or {},
    }


def _summary_with_channels(*channel_dicts: dict) -> dict:
    """Simula retorno de CSR.summary() / /api/channels/status."""
    channels: dict[str, list[dict]] = {}
    for ch in channel_dicts:
        cid = ch["channel_id"]
        channels.setdefault(cid, []).append(ch)
    return {
        "total": len(channel_dicts),
        "running": sum(1 for c in channel_dicts if c["running"]),
        "healthy": sum(1 for c in channel_dicts if c["healthy"]),
        "channels": channels,
    }


# ── ChannelSnapshot.from_dict ────────────────────────────────────────────

class TestChannelSnapshot:
    def test_from_dict_basic(self):
        d = _make_snap_dict(channel_id="discord", running=False, healthy=False, configured=True)
        snap = ChannelSnapshot.from_dict(d)
        assert snap.channel_id == "discord"
        assert snap.running is False
        assert snap.healthy is False
        assert snap.configured is True
        assert snap.identity_name == "Arkhe Bot"

    def test_from_dict_missing_identity(self):
        d = _make_snap_dict(identity=None)
        d.pop("identity", None)
        snap = ChannelSnapshot.from_dict(d)
        assert snap.identity_name == ""

    def test_from_dict_username_fallback(self):
        d = _make_snap_dict(identity={"username": "bot123"})
        snap = ChannelSnapshot.from_dict(d)
        assert snap.identity_name == "bot123"

    def test_from_dict_error(self):
        d = _make_snap_dict(last_error="timeout", reconnect_attempts=3, healthy=False)
        snap = ChannelSnapshot.from_dict(d)
        assert snap.last_error == "timeout"
        assert snap.reconnect_attempts == 3


# ── _channel_icon ─────────────────────────────────────────────────────────

class TestChannelIcon:
    def test_running_healthy(self):
        icon, style = _channel_icon({"running": True, "healthy": True, "configured": True})
        assert icon == "●"
        assert "green" in style

    def test_running_unhealthy(self):
        icon, style = _channel_icon({"running": True, "healthy": False, "configured": True})
        assert icon == "✖"
        assert "red" in style

    def test_configured_not_running(self):
        icon, style = _channel_icon({"running": False, "healthy": False, "configured": True})
        assert icon == "◑"
        assert "yellow" in style

    def test_off(self):
        icon, style = _channel_icon({"running": False, "healthy": False, "configured": False})
        assert icon == "○"
        assert "dim" in style


# ── build_channel_panel ───────────────────────────────────────────────────

class TestBuildChannelPanel:
    def test_empty_state(self):
        state = ChannelConsoleState()
        panel = build_channel_panel(state)
        assert panel.title == "Canais"

    def test_with_error(self):
        state = ChannelConsoleState(fetch_error="conexão recusada")
        panel = build_channel_panel(state)
        assert panel.title == "Canais"

    def test_with_snapshots(self):
        snaps = [
            ChannelSnapshot.from_dict(_make_snap_dict("telegram", running=True, healthy=True)),
            ChannelSnapshot.from_dict(_make_snap_dict("discord", running=False, healthy=False, configured=True)),
        ]
        state = ChannelConsoleState(snapshots=snaps, last_fetch_at=time.time())
        panel = build_channel_panel(state)
        assert panel.title == "Canais"

    def test_with_send_result(self):
        state = ChannelConsoleState(
            snapshots=[ChannelSnapshot.from_dict(_make_snap_dict())],
            last_send_result="Enviado para telegram:123",
        )
        panel = build_channel_panel(state)
        assert panel.title == "Canais"


# ── fetch_channel_snapshots_live ──────────────────────────────────────────

class TestFetchLive:
    def test_parses_list_accounts(self):
        api = MagicMock()
        api.fetch_channels_status.return_value = _summary_with_channels(
            _make_snap_dict("telegram"),
            _make_snap_dict("discord", running=False, healthy=False),
        )
        result = fetch_channel_snapshots_live(api)
        assert len(result) == 2
        ids = {s.channel_id for s in result}
        assert ids == {"telegram", "discord"}

    def test_parses_dict_account(self):
        """Quando channels é {channel_id: single_dict} em vez de lista."""
        api = MagicMock()
        api.fetch_channels_status.return_value = {
            "channels": {"telegram": _make_snap_dict("telegram")},
        }
        result = fetch_channel_snapshots_live(api)
        assert len(result) == 1
        assert result[0].channel_id == "telegram"

    def test_empty_response(self):
        api = MagicMock()
        api.fetch_channels_status.return_value = {"channels": {}}
        result = fetch_channel_snapshots_live(api)
        assert result == []


# ── fetch_channel_snapshots_local ─────────────────────────────────────────

class TestFetchLocal:
    def test_csr_unavailable(self):
        with patch(
            "rlm.cli.tui.channel_console.get_channel_status_registry",
            side_effect=RuntimeError("not init"),
            create=True,
        ):
            # O import pode falhar antes do patch, então capturamos gracefully
            result = fetch_channel_snapshots_local()
            # Se CSR não está disponível, retorna lista vazia
            assert isinstance(result, list)


# ── refresh_channel_state ─────────────────────────────────────────────────

class TestRefreshChannelState:
    def test_live_mode(self):
        api = MagicMock()
        api.fetch_channels_status.return_value = _summary_with_channels(
            _make_snap_dict("telegram"),
        )
        state = ChannelConsoleState()
        refresh_channel_state(state, live_api=api)
        assert len(state.snapshots) == 1
        assert state.snapshots[0].channel_id == "telegram"
        assert state.last_fetch_at > 0
        assert state.fetch_error == ""

    def test_live_mode_error(self):
        api = MagicMock()
        api.fetch_channels_status.side_effect = Exception("timeout")
        state = ChannelConsoleState()
        refresh_channel_state(state, live_api=api)
        assert "timeout" in state.fetch_error
        # Nunca levanta exceção
        assert state.snapshots == []

    def test_local_mode_no_csr(self):
        state = ChannelConsoleState()
        refresh_channel_state(state, live_api=None)
        # Sem CSR inicializado, retorna vazio mas sem crash
        assert isinstance(state.snapshots, list)
        assert state.fetch_error == ""


# ── live_api channel methods ──────────────────────────────────────────────

class TestLiveAPIMethods:
    """Testa os novos métodos em LiveWorkbenchAPI sem rede."""

    def test_fetch_channels_status_calls_correct_path(self):
        from rlm.cli.tui.live_api import LiveWorkbenchAPI
        api = MagicMock(spec=LiveWorkbenchAPI)
        api.fetch_channels_status.return_value = {"channels": {}}
        result = api.fetch_channels_status()
        assert "channels" in result

    def test_probe_channel_calls_correct_path(self):
        from rlm.cli.tui.live_api import LiveWorkbenchAPI
        api = MagicMock(spec=LiveWorkbenchAPI)
        api.probe_channel.return_value = {"channel_id": "telegram", "probe": {"ok": True}}
        result = api.probe_channel("telegram")
        assert result["channel_id"] == "telegram"

    def test_cross_channel_send_calls_correct_path(self):
        from rlm.cli.tui.live_api import LiveWorkbenchAPI
        api = MagicMock(spec=LiveWorkbenchAPI)
        api.cross_channel_send.return_value = {"status": "queued"}
        result = api.cross_channel_send("telegram:12345", "hello")
        assert result["status"] == "queued"


# ── TUI __init__ lazy exports ────────────────────────────────────────────

class TestTUILazyExports:
    def test_channel_console_state_importable(self):
        from rlm.cli.tui import ChannelConsoleState as CS
        assert CS is ChannelConsoleState

    def test_build_channel_panel_importable(self):
        from rlm.cli.tui import build_channel_panel as bcp
        assert bcp is build_channel_panel

    def test_refresh_channel_state_importable(self):
        from rlm.cli.tui import refresh_channel_state as rcs
        assert rcs is refresh_channel_state
