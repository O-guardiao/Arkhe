"""
Testes — Phase 6: Camada 4 — Service Discovery (Channel Probe + Status Registry)

Cobre:
- BotIdentity / ProbeResult dataclasses
- TelegramProber: sucesso, HTTP error, retry, getWebhookInfo
- DiscordProber: sucesso, erro
- NullProber: sempre ok
- ChannelStatusRegistry: register, mark_running/stopped/error, probe, get_snapshot, summary
- ChannelStatusRegistry singleton: init/get/reset
- ChannelAccountSnapshot.to_dict() serialization
- CLI commands import

Execute:
    pytest tests/test_phase6_channel_discovery.py -v
"""
from __future__ import annotations

import json
import time
from http.client import HTTPResponse
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rlm.core.comms.channel_probe import (
    BotIdentity,
    ChannelProber,
    DiscordProber,
    NullProber,
    ProbeResult,
    TelegramProber,
)
from rlm.core.comms.channel_status import (
    ChannelAccountSnapshot,
    ChannelStatusRegistry,
    _reset_singleton,
    get_channel_status_registry,
    init_channel_status_registry,
)


# ===========================================================================
# Helpers
# ===========================================================================

class _FakeResponse:
    """Minimal file-like for urlopen mock."""
    def __init__(self, data: dict, status: int = 200):
        self._body = json.dumps(data).encode("utf-8")
        self.status = status
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass


def _make_urlopen_mock(responses: list[dict]):
    """Returns a side_effect callable that yields _FakeResponse per call."""
    call_idx = [0]
    def _side(req, timeout=None):
        idx = call_idx[0]
        call_idx[0] += 1
        if idx < len(responses):
            return _FakeResponse(responses[idx])
        return _FakeResponse({"ok": False, "description": "exhausted"})
    return _side


# ===========================================================================
# BotIdentity
# ===========================================================================

class TestBotIdentity:

    def test_frozen_defaults(self):
        bi = BotIdentity()
        assert bi.bot_id is None
        assert bi.username is None
        assert bi.display_name is None
        assert bi.extras == {}

    def test_frozen_with_values(self):
        bi = BotIdentity(bot_id=123, username="testbot", display_name="Test")
        assert bi.bot_id == 123
        assert bi.username == "testbot"
        assert bi.display_name == "Test"

    def test_immutable(self):
        bi = BotIdentity(bot_id=1)
        with pytest.raises(AttributeError):
            bi.bot_id = 2  # type: ignore[misc]


# ===========================================================================
# ProbeResult
# ===========================================================================

class TestProbeResult:

    def test_defaults(self):
        pr = ProbeResult()
        assert pr.ok is False
        assert pr.elapsed_ms == 0.0
        assert pr.error is None
        assert pr.identity is None
        assert pr.raw == {}

    def test_with_identity(self):
        bi = BotIdentity(bot_id=42, username="bot42")
        pr = ProbeResult(ok=True, elapsed_ms=123.4, identity=bi)
        assert pr.ok is True
        assert pr.identity.bot_id == 42  # type: ignore[union-attr]


# ===========================================================================
# NullProber
# ===========================================================================

class TestNullProber:

    def test_always_ok(self):
        p = NullProber("webchat")
        r = p.probe()
        assert r.ok is True
        assert r.elapsed_ms == 0.0

    def test_channel_id(self):
        p = NullProber("slack")
        assert p.channel_id == "slack"

    def test_is_channel_prober(self):
        assert isinstance(NullProber("x"), ChannelProber)


# ===========================================================================
# TelegramProber
# ===========================================================================

class TestTelegramProber:

    @patch("rlm.core.comms.channel_probe.urllib_request.urlopen")
    def test_success(self, mock_urlopen):
        """Probe succeeds: /getMe ok + /getWebhookInfo ok."""
        get_me = {"ok": True, "result": {
            "id": 999, "username": "testbot", "first_name": "Tst",
            "can_join_groups": True, "can_read_all_group_messages": False,
            "supports_inline_queries": False,
        }}
        webhook_info = {"ok": True, "result": {
            "url": "", "has_custom_certificate": False, "pending_update_count": 0,
        }}
        mock_urlopen.side_effect = _make_urlopen_mock([get_me, webhook_info])

        prober = TelegramProber(token="fake:token")
        result = prober.probe(timeout_s=5.0)

        assert result.ok is True
        assert result.identity is not None
        assert result.identity.bot_id == 999
        assert result.identity.username == "testbot"
        assert result.identity.display_name == "Tst"
        assert result.raw.get("bot", {}).get("id") == 999
        assert "webhook" in result.raw

    @patch("rlm.core.comms.channel_probe.urllib_request.urlopen")
    def test_getme_fails_all_retries(self, mock_urlopen):
        """All 3 retries of /getMe fail → probe fails."""
        mock_urlopen.side_effect = Exception("DNS resolution failed")

        prober = TelegramProber(token="bad:token")
        result = prober.probe(timeout_s=0.1)

        assert result.ok is False
        assert "DNS resolution" in (result.error or "")

    @patch("rlm.core.comms.channel_probe.urllib_request.urlopen")
    def test_getme_not_ok(self, mock_urlopen):
        """/getMe returns ok=False."""
        mock_urlopen.side_effect = _make_urlopen_mock([
            {"ok": False, "description": "Unauthorized"}
        ])

        prober = TelegramProber(token="bad:token")
        result = prober.probe(timeout_s=5.0)

        assert result.ok is False
        assert result.error == "Unauthorized"

    @patch("rlm.core.comms.channel_probe.urllib_request.urlopen")
    def test_webhook_failure_doesnt_break_probe(self, mock_urlopen):
        """If /getWebhookInfo fails, probe still succeeds."""
        get_me = {"ok": True, "result": {"id": 1, "username": "bot", "first_name": "B"}}
        call_count = [0]
        def _side(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return _FakeResponse(get_me)
            raise Exception("webhook fail")
        mock_urlopen.side_effect = _side

        result = TelegramProber(token="t").probe()
        assert result.ok is True
        assert result.identity.username == "bot"  # type: ignore[union-attr]

    @patch("rlm.core.comms.channel_probe.time.sleep")
    @patch("rlm.core.comms.channel_probe.urllib_request.urlopen")
    def test_retry_then_success(self, mock_urlopen, mock_sleep):
        """First call fails, second succeeds."""
        get_me = {"ok": True, "result": {"id": 7, "username": "retry_bot", "first_name": "R"}}
        webhook = {"ok": True, "result": {"url": ""}}
        call_count = [0]
        def _side(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("temporary DNS failure")
            if call_count[0] == 2:
                return _FakeResponse(get_me)
            return _FakeResponse(webhook)
        mock_urlopen.side_effect = _side

        result = TelegramProber(token="t").probe(timeout_s=2)
        assert result.ok is True
        assert result.identity.bot_id == 7  # type: ignore[union-attr]
        mock_sleep.assert_called_once()

    def test_channel_id(self):
        assert TelegramProber(token="t").channel_id == "telegram"


# ===========================================================================
# DiscordProber
# ===========================================================================

class TestDiscordProber:

    @patch("rlm.core.comms.channel_probe.urllib_request.urlopen")
    def test_success(self, mock_urlopen):
        data = {"id": "88888", "username": "dbot", "global_name": "Discord Bot"}
        mock_urlopen.return_value = _FakeResponse(data)

        result = DiscordProber(bot_token="tok").probe()
        assert result.ok is True
        assert result.identity is not None
        assert result.identity.bot_id == 88888
        assert result.identity.username == "dbot"
        assert result.identity.display_name == "Discord Bot"

    @patch("rlm.core.comms.channel_probe.urllib_request.urlopen")
    def test_failure(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("401 Unauthorized")
        result = DiscordProber(bot_token="bad").probe()
        assert result.ok is False
        assert "401" in (result.error or "")

    def test_channel_id(self):
        assert DiscordProber(bot_token="t").channel_id == "discord"


# ===========================================================================
# ChannelAccountSnapshot
# ===========================================================================

class TestChannelAccountSnapshot:

    def test_defaults(self):
        snap = ChannelAccountSnapshot(channel_id="telegram")
        assert snap.enabled is True
        assert snap.configured is False
        assert snap.running is False
        assert snap.healthy is False
        assert snap.identity is None
        assert snap.meta == {}

    def test_to_dict_minimal(self):
        snap = ChannelAccountSnapshot(channel_id="webchat", configured=True, running=True, healthy=True)
        d = snap.to_dict()
        assert d["channel_id"] == "webchat"
        assert d["running"] is True
        assert "identity" not in d  # no identity yet

    def test_to_dict_with_identity(self):
        snap = ChannelAccountSnapshot(
            channel_id="telegram",
            identity=BotIdentity(bot_id=1, username="tbot", display_name="T"),
            last_probe_at=1000.0,
            last_probe_ms=42.5,
            meta={"api_base_url": "http://localhost:5000"},
        )
        d = snap.to_dict()
        assert d["identity"]["bot_id"] == 1
        assert d["identity"]["username"] == "tbot"
        assert d["last_probe_at"] == 1000.0
        assert d["last_probe_ms"] == 42.5
        assert d["meta"]["api_base_url"] == "http://localhost:5000"


# ===========================================================================
# ChannelStatusRegistry
# ===========================================================================

class TestChannelStatusRegistry:

    def setup_method(self):
        self.csr = ChannelStatusRegistry()

    # ── registration ──

    def test_register_no_prober(self):
        snap = self.csr.register("webchat", configured=True)
        assert snap.channel_id == "webchat"
        assert snap.configured is True
        assert snap.identity is None
        assert snap.healthy is False  # no prober → not probed

    def test_register_with_prober(self):
        snap = self.csr.register("webchat", prober=NullProber("webchat"))
        assert snap.healthy is True  # NullProber always ok

    @patch("rlm.core.comms.channel_probe.urllib_request.urlopen")
    def test_register_with_telegram_prober(self, mock_urlopen):
        get_me = {"ok": True, "result": {"id": 55, "username": "testbot", "first_name": "T"}}
        webhook = {"ok": True, "result": {"url": ""}}
        mock_urlopen.side_effect = _make_urlopen_mock([get_me, webhook])

        snap = self.csr.register("telegram", prober=TelegramProber(token="t"))
        assert snap.healthy is True
        assert snap.identity is not None
        assert snap.identity.bot_id == 55

    # ── mark_running / mark_stopped / mark_error ──

    def test_mark_running(self):
        self.csr.register("telegram")
        self.csr.mark_running("telegram")
        snap = self.csr.get("telegram")
        assert snap is not None
        assert snap.running is True
        assert snap.healthy is True
        assert snap.last_start_at > 0

    def test_mark_stopped(self):
        self.csr.register("telegram")
        self.csr.mark_running("telegram")
        self.csr.mark_stopped("telegram", error="killed")
        snap = self.csr.get("telegram")
        assert snap is not None
        assert snap.running is False
        assert snap.healthy is False
        assert snap.last_error == "killed"

    def test_mark_error_increments_reconnect(self):
        self.csr.register("telegram")
        self.csr.mark_error("telegram", error="timeout")
        self.csr.mark_error("telegram", error="timeout 2")
        snap = self.csr.get("telegram")
        assert snap is not None
        assert snap.reconnect_attempts == 2
        assert snap.last_error == "timeout 2"

    # ── update ──

    def test_update_existing(self):
        self.csr.register("discord")
        snap = self.csr.update("discord", healthy=True, running=True)
        assert snap is not None
        assert snap.healthy is True
        assert snap.running is True

    def test_update_nonexistent(self):
        assert self.csr.update("nonexistent") is None

    # ── probe ──

    def test_probe_no_prober(self):
        self.csr.register("telegram")  # no prober
        result = self.csr.probe("telegram")
        assert result.ok is False
        assert "No prober" in (result.error or "")

    def test_probe_with_prober(self):
        self.csr.register("webchat", prober=NullProber("webchat"))
        result = self.csr.probe("webchat")
        assert result.ok is True

    @patch("rlm.core.comms.channel_probe.urllib_request.urlopen")
    def test_probe_updates_snapshot(self, mock_urlopen):
        get_me = {"ok": True, "result": {"id": 10, "username": "pb", "first_name": "P"}}
        webhook = {"ok": True, "result": {"url": ""}}
        mock_urlopen.side_effect = _make_urlopen_mock([get_me, webhook, get_me, webhook])

        self.csr.register("telegram", prober=TelegramProber(token="t"))
        snap_before = self.csr.get("telegram")
        assert snap_before is not None
        old_probe_at = snap_before.last_probe_at

        result = self.csr.probe("telegram")
        assert result.ok is True
        snap_after = self.csr.get("telegram")
        assert snap_after is not None
        assert snap_after.last_probe_at >= old_probe_at

    def test_probe_all(self):
        self.csr.register("webchat", prober=NullProber("webchat"))
        self.csr.register("slack", prober=NullProber("slack"))
        results = self.csr.probe_all()
        assert len(results) == 2
        assert all(r.ok for r in results.values())

    # ── query ──

    def test_get_none(self):
        assert self.csr.get("nonexistent") is None

    def test_get_snapshot_empty(self):
        assert self.csr.get_snapshot() == {}

    def test_get_snapshot_grouped(self):
        self.csr.register("telegram")
        self.csr.register("discord")
        snap = self.csr.get_snapshot()
        assert "telegram" in snap
        assert "discord" in snap
        assert len(snap["telegram"]) == 1

    def test_list_channels(self):
        self.csr.register("telegram")
        self.csr.register("discord")
        self.csr.register("webchat")
        assert self.csr.list_channels() == ["discord", "telegram", "webchat"]

    def test_list_running(self):
        self.csr.register("telegram")
        self.csr.register("discord")
        self.csr.mark_running("telegram")
        running = self.csr.list_running()
        assert len(running) == 1
        assert running[0].channel_id == "telegram"

    def test_summary(self):
        self.csr.register("telegram", prober=NullProber("telegram"))
        self.csr.mark_running("telegram")
        self.csr.register("discord")  # not running
        s = self.csr.summary()
        assert s["total"] == 2
        assert s["running"] == 1
        assert s["healthy"] == 1
        assert "telegram" in s["channels"]
        assert "discord" in s["channels"]

    # ── multi-account ──

    def test_multiple_accounts_same_channel(self):
        self.csr.register("telegram", account_id="bot1")
        self.csr.register("telegram", account_id="bot2")
        snap = self.csr.get_snapshot()
        assert len(snap["telegram"]) == 2

    def test_mark_running_specific_account(self):
        self.csr.register("telegram", account_id="a")
        self.csr.register("telegram", account_id="b")
        self.csr.mark_running("telegram", account_id="a")
        snap_a = self.csr.get("telegram", "a")
        snap_b = self.csr.get("telegram", "b")
        assert snap_a is not None and snap_a.running
        assert snap_b is not None and not snap_b.running


# ===========================================================================
# Singleton
# ===========================================================================

class TestSingleton:

    def teardown_method(self):
        _reset_singleton()

    def test_init_creates_singleton(self):
        csr = init_channel_status_registry()
        assert isinstance(csr, ChannelStatusRegistry)

    def test_init_idempotent(self):
        a = init_channel_status_registry()
        b = init_channel_status_registry()
        assert a is b

    def test_get_before_init_raises(self):
        with pytest.raises(RuntimeError, match="não inicializado"):
            get_channel_status_registry()

    def test_get_after_init_returns_same(self):
        csr = init_channel_status_registry()
        assert get_channel_status_registry() is csr

    def test_reset_clears(self):
        init_channel_status_registry()
        _reset_singleton()
        with pytest.raises(RuntimeError):
            get_channel_status_registry()


# ===========================================================================
# CLI imports
# ===========================================================================

class TestCLIImports:

    def test_channel_commands_importable(self):
        from rlm.cli.commands.channel import (
            cmd_channel_list,
            cmd_channel_probe,
            cmd_channel_status,
        )
        assert callable(cmd_channel_list)
        assert callable(cmd_channel_status)
        assert callable(cmd_channel_probe)

    def test_command_specs_includes_channel_subcommands(self):
        from rlm.cli.command_specs import get_command_specs
        specs = get_command_specs()
        channel_spec = next((s for s in specs if s.name == "channel"), None)
        assert channel_spec is not None
        sub_names = {s.name for s in channel_spec.subcommands}
        assert {"list", "status", "probe"} <= sub_names


# ===========================================================================
# CLI cmd_channel_status (mocked API)
# ===========================================================================

class TestCmdChannelStatus:

    def test_server_offline_returns_1(self):
        """When server is unreachable, should return 1."""
        import argparse
        from rlm.cli.commands.channel import cmd_channel_status
        from rlm.cli.context import CliContext

        ctx = CliContext.from_environment()
        # Use a port that's almost certainly not listening
        ctx.env["RLM_API_PORT"] = "19999"
        ctx.env["RLM_API_HOST"] = "127.0.0.1"
        args = argparse.Namespace()
        result = cmd_channel_status(args, context=ctx)
        assert result == 1


# ===========================================================================
# meta_merge support
# ===========================================================================

class TestUpdateMetaMerge:

    def setup_method(self):
        _reset_singleton()
        self.csr = ChannelStatusRegistry()

    def test_meta_merge_adds_fields(self):
        self.csr.register("telegram", meta={"initial": True})
        self.csr.update("telegram", meta_merge={"last_chat_id": "12345"})
        snap = self.csr.get("telegram")
        assert snap is not None
        assert snap.meta["initial"] is True
        assert snap.meta["last_chat_id"] == "12345"

    def test_meta_merge_overwrites_existing_key(self):
        self.csr.register("telegram", meta={"last_chat_id": "old"})
        self.csr.update("telegram", meta_merge={"last_chat_id": "new"})
        snap = self.csr.get("telegram")
        assert snap is not None
        assert snap.meta["last_chat_id"] == "new"

    def test_meta_merge_none_is_noop(self):
        self.csr.register("telegram", meta={"x": 1})
        self.csr.update("telegram", meta_merge=None)
        snap = self.csr.get("telegram")
        assert snap is not None
        assert snap.meta == {"x": 1}

    def test_meta_merge_combined_with_kwargs(self):
        self.csr.register("telegram")
        self.csr.update("telegram", running=True, meta_merge={"chat": "42"})
        snap = self.csr.get("telegram")
        assert snap is not None
        assert snap.running is True
        assert snap.meta["chat"] == "42"


# ===========================================================================
# channels() REPL callable
# ===========================================================================

class TestChannelsCallable:

    def test_returns_summary_when_csr_initialized(self):
        _reset_singleton()
        csr = init_channel_status_registry()
        csr.register("telegram")
        csr.mark_running("telegram")

        # Simulate what the REPL callable does
        from rlm.core.comms.channel_status import get_channel_status_registry
        result = get_channel_status_registry().summary()
        assert result["total"] == 1
        assert result["running"] == 1
        _reset_singleton()

    def test_channels_callable_error_before_init(self):
        _reset_singleton()
        # Simulate what the REPL callable does when CSR not initialized
        try:
            from rlm.core.comms.channel_status import get_channel_status_registry
            get_channel_status_registry().summary()
            got_error = False
        except RuntimeError:
            got_error = True
        assert got_error


# ===========================================================================
# telegram_get_updates fallback chain
# ===========================================================================

class TestTelegramGetUpdatesFallbackChain:
    """Tests the 3-tier fallback: config → env var → CSR meta."""

    def test_env_var_fallback(self):
        """TELEGRAM_OWNER_CHAT_ID env var provides chat_id when config is empty."""
        import os

        _reset_singleton()
        # Init CSR so the import doesn't fail, but no last_chat_id in meta
        csr = init_channel_status_registry()

        # Mock session manager with no telegram events
        mock_sm = MagicMock()
        mock_sm.get_session_events = MagicMock(return_value=[])

        # Mock config with empty owner_chat_id
        mock_cfg = MagicMock()
        mock_tg_cfg = MagicMock()
        mock_tg_cfg.owner_chat_id = ""
        mock_cfg.channels = {"telegram": mock_tg_cfg}

        mock_session = MagicMock()
        mock_session.session_id = "test-sess"

        with patch.dict(os.environ, {"TELEGRAM_OWNER_CHAT_ID": "99887766"}):
            with patch("rlm.core.config.get_config", return_value=mock_cfg):
                # Simulate the fallback logic inline (same as runtime_pipeline)
                from rlm.core.config import get_config
                cfg = get_config()
                tg_cfg = cfg.channels.get("telegram")
                owner_id = getattr(tg_cfg, "owner_chat_id", "") if tg_cfg else ""
                assert owner_id == ""  # config fallback fails
                if not owner_id:
                    owner_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")
                assert owner_id == "99887766"  # env var fallback succeeds

        _reset_singleton()

    def test_csr_fallback(self):
        """CSR meta.last_chat_id provides chat_id when config and env are empty."""
        import os

        _reset_singleton()
        csr = init_channel_status_registry()
        csr.register("telegram", meta={"last_chat_id": "55443322"})

        with patch.dict(os.environ, {}, clear=False):
            # Ensure env var is not set
            os.environ.pop("TELEGRAM_OWNER_CHAT_ID", None)

            # Simulate fallback chain
            owner_id = ""
            # (1) config — empty
            # (2) env var — not set
            if not owner_id:
                owner_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")
            assert owner_id == ""
            # (3) CSR meta
            if not owner_id:
                try:
                    from rlm.core.comms.channel_status import get_channel_status_registry
                    _snap = get_channel_status_registry().get("telegram")
                    if _snap and _snap.meta:
                        owner_id = str(_snap.meta.get("last_chat_id", ""))
                except Exception:
                    pass
            assert owner_id == "55443322"

        _reset_singleton()


# ===========================================================================
# Phase E — SKILL.md discoverability
# ===========================================================================

class TestChannelsSkillDiscoverability:
    """Verifica que o SKILL.md para channels() é carregado pelo skill_loader."""

    _SKILLS_DIR = Path(__file__).resolve().parent.parent / "rlm" / "skills"

    def test_skill_loader_finds_channels(self):
        from rlm.core.skillkit.skill_loader import SkillLoader

        loader = SkillLoader()
        skills = loader.load_from_dir(self._SKILLS_DIR)
        names = [s.name for s in skills]
        assert "channels" in names, f"'channels' não encontrado em skill_list: {names}"

    def test_channels_skill_metadata(self):
        from rlm.core.skillkit.skill_loader import SkillLoader

        loader = SkillLoader()
        skills = loader.load_from_dir(self._SKILLS_DIR)
        channels_skill = next((s for s in skills if s.name == "channels"), None)
        assert channels_skill is not None
        assert "snapshot" in channels_skill.description.lower()
        assert channels_skill.priority == "contextual"

    def test_channels_skill_sif_no_impl(self):
        """channels() vem de hard-injection; SKILL.md NÃO deve ter impl/codex."""
        from rlm.core.skillkit.skill_loader import SkillLoader

        loader = SkillLoader()
        skills = loader.load_from_dir(self._SKILLS_DIR)
        channels_skill = next((s for s in skills if s.name == "channels"), None)
        assert channels_skill is not None
        # SIF não deve compilar callable — hard-injection é a fonte
        assert not getattr(channels_skill, "has_impl", False) or not channels_skill.impl
        assert not getattr(channels_skill, "has_codex", False) or not channels_skill.codex

    def test_channels_skill_compose_references(self):
        from rlm.core.skillkit.skill_loader import SkillLoader

        loader = SkillLoader()
        skills = loader.load_from_dir(self._SKILLS_DIR)
        channels_skill = next((s for s in skills if s.name == "channels"), None)
        assert channels_skill is not None
        sif = channels_skill.sif_entry
        assert sif is not None, "channels skill deve ter sif_entry"
        compose = getattr(sif, "compose", []) or []
        assert "cross_channel_send" in compose
        assert "telegram_bot" in compose
