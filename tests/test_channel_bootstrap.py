"""Testes do channel_bootstrap — inicialização unificada de infraestrutura multichannel."""

from __future__ import annotations

import importlib
import os
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from rlm.core.comms.channel_bootstrap import (
    ChannelDescriptor,
    ChannelInfrastructure,
    _CHANNEL_DESCRIPTORS,
    _import_attr,
    _is_channel_configured,
    _try_import_module,
    bootstrap_channel_infrastructure,
)


# ── Helpers ───────────────────────────────────────────────────────────────

def _reset_singletons():
    """Reseta singletons de CSR e MessageBus para isolamento entre testes."""
    import rlm.core.comms.channel_status as cs_mod
    import rlm.core.comms.message_bus as mb_mod
    cs_mod._registry_instance = None
    mb_mod._bus_instance = None


def _mock_session_manager():
    sm = MagicMock()
    sm.close_all = MagicMock()
    return sm


def _mock_event_bus():
    return MagicMock()


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_singletons():
    """Garante que cada teste começa com singletons limpos."""
    _reset_singletons()
    yield
    _reset_singletons()


@pytest.fixture(autouse=True)
def _clean_channel_registry():
    """Limpa ChannelRegistry._adapters entre testes."""
    from rlm.plugins.channel_registry import ChannelRegistry
    original = dict(ChannelRegistry._adapters)
    yield
    ChannelRegistry._adapters.clear()
    ChannelRegistry._adapters.update(original)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove env vars de canais para garantir testes isolados."""
    for key in (
        "TELEGRAM_BOT_TOKEN", "DISCORD_APP_PUBLIC_KEY", "DISCORD_BOT_TOKEN",
        "RLM_DISCORD_SKIP_VERIFY", "WHATSAPP_VERIFY_TOKEN",
        "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET",
        "RLM_USE_MESSAGE_BUS",
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


# ── ChannelDescriptor ─────────────────────────────────────────────────────

class TestChannelDescriptor:
    def test_is_frozen(self):
        d = ChannelDescriptor(channel_id="test", env_keys=())
        with pytest.raises(AttributeError):
            d.channel_id = "x"  # type: ignore[misc]

    def test_known_channels(self):
        ids = {d.channel_id for d in _CHANNEL_DESCRIPTORS}
        assert "telegram" in ids
        assert "discord" in ids
        assert "whatsapp" in ids
        assert "slack" in ids
        assert "webchat" in ids
        assert "tui" in ids

    def test_tui_always_registered(self):
        tui = next(d for d in _CHANNEL_DESCRIPTORS if d.channel_id == "tui")
        assert tui.always_registered is True


# ── _import_attr ──────────────────────────────────────────────────────────

class TestImportAttr:
    def test_valid_import(self):
        cls = _import_attr("rlm.core.comms.channel_probe:NullProber")
        from rlm.core.comms.channel_probe import NullProber
        assert cls is NullProber

    def test_invalid_module(self):
        with pytest.raises(ModuleNotFoundError):
            _import_attr("rlm.nonexistent.module:Foo")

    def test_invalid_attr(self):
        with pytest.raises(AttributeError):
            _import_attr("rlm.core.comms.channel_probe:NonExistentClass")


# ── _try_import_module ────────────────────────────────────────────────────

class TestTryImportModule:
    def test_valid_module(self):
        assert _try_import_module("rlm.core.comms.channel_probe") is True

    def test_invalid_module(self):
        assert _try_import_module("rlm.nonexistent_module") is False


# ── _is_channel_configured ───────────────────────────────────────────────

class TestIsChannelConfigured:
    def test_always_registered(self):
        d = ChannelDescriptor(channel_id="tui", env_keys=(), always_registered=True)
        assert _is_channel_configured(d) is True

    def test_env_key_present(self, clean_env):
        clean_env.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
        d = ChannelDescriptor(channel_id="telegram", env_keys=("TELEGRAM_BOT_TOKEN",))
        assert _is_channel_configured(d) is True

    def test_env_key_absent(self, clean_env):
        d = ChannelDescriptor(channel_id="telegram", env_keys=("TELEGRAM_BOT_TOKEN",))
        assert _is_channel_configured(d) is False

    def test_env_key_empty_string(self, clean_env):
        clean_env.setenv("TELEGRAM_BOT_TOKEN", "   ")
        d = ChannelDescriptor(channel_id="telegram", env_keys=("TELEGRAM_BOT_TOKEN",))
        assert _is_channel_configured(d) is False

    def test_multiple_env_keys_any_present(self, clean_env):
        clean_env.setenv("SLACK_SIGNING_SECRET", "secret123")
        d = ChannelDescriptor(
            channel_id="slack",
            env_keys=("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"),
        )
        assert _is_channel_configured(d) is True


# ── ChannelInfrastructure ─────────────────────────────────────────────────

class TestChannelInfrastructure:
    def test_close_stops_delivery_worker(self):
        dw = MagicMock()
        infra = ChannelInfrastructure(
            csr=MagicMock(),
            message_bus=MagicMock(),
            outbox=MagicMock(),
            delivery_worker=dw,
        )
        infra.close()
        dw.stop.assert_called_once()


# ── bootstrap_channel_infrastructure ──────────────────────────────────────

class TestBootstrapChannelInfrastructure:
    def test_returns_infrastructure_object(self, clean_env, tmp_path):
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        assert isinstance(infra, ChannelInfrastructure)
        assert infra.csr is not None
        assert infra.message_bus is not None
        assert infra.delivery_worker is not None
        assert infra.outbox is not None

    def test_csr_has_tui_registered(self, clean_env, tmp_path):
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        snap = infra.csr.get("tui")
        assert snap is not None
        assert snap.configured is True

    def test_tui_always_in_registered_channels(self, clean_env, tmp_path):
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        assert "tui" in infra.registered_channels

    def test_tui_adapter_in_channel_registry(self, clean_env, tmp_path):
        from rlm.plugins.channel_registry import ChannelRegistry
        db = str(tmp_path / "test.db")
        bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        adapter = ChannelRegistry.get_adapter("tui")
        assert adapter is not None

    def test_telegram_unconfigured_without_token(self, clean_env, tmp_path):
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        snap = infra.csr.get("telegram")
        assert snap is not None
        assert snap.configured is False

    def test_telegram_configured_with_token(self, clean_env, tmp_path):
        clean_env.setenv("TELEGRAM_BOT_TOKEN", "fake:token")
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        snap = infra.csr.get("telegram")
        assert snap is not None
        assert snap.configured is True

    def test_telegram_meta_prefers_internal_host(self, clean_env, tmp_path):
        clean_env.setenv("TELEGRAM_BOT_TOKEN", "fake:token")
        clean_env.setenv("RLM_INTERNAL_HOST", "http://brain.internal:9000")
        clean_env.setenv("RLM_API_PORT", "1")
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        snap = infra.csr.get("telegram")
        assert snap is not None
        assert snap.meta["api_base_url"] == "http://brain.internal:9000"

    def test_message_bus_off_by_default(self, clean_env, tmp_path):
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        assert infra.use_message_bus is False

    def test_message_bus_on_via_env(self, clean_env, tmp_path):
        clean_env.setenv("RLM_USE_MESSAGE_BUS", "true")
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        assert infra.use_message_bus is True

    def test_idempotent_singletons(self, clean_env, tmp_path):
        """Chamar bootstrap duas vezes não deve crashar (singletons idempotentes)."""
        db = str(tmp_path / "test.db")
        kwargs = dict(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        infra1 = bootstrap_channel_infrastructure(**kwargs)
        # Segundo call — mesmo CSR e MessageBus
        infra2 = bootstrap_channel_infrastructure(**kwargs)
        assert infra1.csr is infra2.csr
        assert infra1.message_bus is infra2.message_bus

    def test_all_known_channels_appear_in_csr(self, clean_env, tmp_path):
        """Todo canal em _CHANNEL_DESCRIPTORS deve ter entry no CSR."""
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        for desc in _CHANNEL_DESCRIPTORS:
            snap = infra.csr.get(desc.channel_id)
            assert snap is not None, f"CSR deveria ter entry para '{desc.channel_id}'"

    def test_summary_reports_registered_counts(self, clean_env, tmp_path):
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        summary = infra.csr.summary()
        assert summary["total"] > 0
        # Pelo menos TUI deve estar running
        assert summary["running"] >= 1


# ── Integration: runtime_factory ──────────────────────────────────────────

class TestRuntimeFactoryIntegration:
    """Verifica que WorkbenchRuntime agora inclui channel_infra."""

    def test_workbench_runtime_has_channel_infra_field(self):
        from rlm.cli.tui.runtime_factory import WorkbenchRuntime
        fields = {f.name for f in WorkbenchRuntime.__dataclass_fields__.values()}
        assert "channel_infra" in fields

    def test_workbench_runtime_close_calls_infra_close(self):
        from rlm.cli.tui.runtime_factory import WorkbenchRuntime
        sm = MagicMock()
        sup = MagicMock()
        dispatch = MagicMock()
        infra = MagicMock()
        rt = WorkbenchRuntime(
            session_manager=sm,
            supervisor=sup,
            dispatch_services=dispatch,
            channel_infra=infra,
        )
        rt.close()
        infra.close.assert_called_once()
        sm.close_all.assert_called_once()
        sup.shutdown.assert_called_once()

    def test_workbench_runtime_close_without_infra(self):
        """close() não falha se channel_infra é None."""
        from rlm.cli.tui.runtime_factory import WorkbenchRuntime
        rt = WorkbenchRuntime(
            session_manager=MagicMock(),
            supervisor=MagicMock(),
            dispatch_services=None,
            channel_infra=None,
        )
        rt.close()  # não deve levantar exceção


# ── Adapter factory fallback (no session_manager) ────────────────────────

class TestAdapterFactoryFallback:
    """Testa que bootstrap tenta instanciar adapter sem args se TypeError."""

    def test_telegram_adapter_registered_via_bootstrap(self, clean_env, tmp_path):
        """Telegram descriptor agora tem adapter_factory; deve registrar."""
        tg = next(d for d in _CHANNEL_DESCRIPTORS if d.channel_id == "telegram")
        assert tg.adapter_factory is not None

    def test_no_arg_adapter_fallback(self, clean_env, tmp_path):
        """Adapters que não aceitam session_manager devem ser instanciados sem args."""
        clean_env.setenv("TELEGRAM_BOT_TOKEN", "fake:token")
        db = str(tmp_path / "test.db")
        from rlm.plugins.channel_registry import ChannelRegistry
        bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        adapter = ChannelRegistry.get_adapter("telegram")
        assert adapter is not None

    def test_session_arg_adapter_still_works(self, clean_env, tmp_path):
        """TuiAdapter que aceita session_manager continua funcionando."""
        db = str(tmp_path / "test.db")
        from rlm.plugins.channel_registry import ChannelRegistry
        bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        adapter = ChannelRegistry.get_adapter("tui")
        assert adapter is not None


# ── channels() REPL function format ──────────────────────────────────────

class TestChannelsReplFormat:
    """Valida que channels() retorna dicts keyed por account_id, não listas."""

    def test_channels_keyed_by_account_id(self, clean_env, tmp_path):
        """Cada canal em channels()['channels'] deve ser dict{account_id: data}."""
        db = str(tmp_path / "test.db")
        infra = bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        # Simula o que channels() faz internamente
        from rlm.core.comms.channel_status import get_channel_status_registry
        csr = get_channel_status_registry()
        raw = csr.summary()
        raw_channels = raw.get("channels") or {}
        keyed = {}
        for ch_id, accounts in raw_channels.items():
            if isinstance(accounts, list):
                keyed[ch_id] = {
                    acc.get("account_id", "default"): acc for acc in accounts
                }
            else:
                keyed[ch_id] = {"default": accounts}
        # Verifica estrutura
        for ch_id, by_account in keyed.items():
            assert isinstance(by_account, dict), f"{ch_id} deveria ser dict, não {type(by_account)}"
            for acc_id, data in by_account.items():
                assert isinstance(data, dict), f"{ch_id}.{acc_id} deveria ser dict"
                assert "channel_id" in data

    def test_telegram_default_account_accessible(self, clean_env, tmp_path):
        """channels()['channels']['telegram']['default'] deve funcionar."""
        clean_env.setenv("TELEGRAM_BOT_TOKEN", "fake:token")
        db = str(tmp_path / "test.db")
        bootstrap_channel_infrastructure(
            session_manager=_mock_session_manager(),
            event_bus=_mock_event_bus(),
            db_path=db,
            start_gateways=False,
        )
        from rlm.core.comms.channel_status import get_channel_status_registry
        csr = get_channel_status_registry()
        raw = csr.summary()
        raw_channels = raw.get("channels") or {}
        keyed = {}
        for ch_id, accounts in raw_channels.items():
            if isinstance(accounts, list):
                keyed[ch_id] = {
                    acc.get("account_id", "default"): acc for acc in accounts
                }
            else:
                keyed[ch_id] = {"default": accounts}
        tg = keyed.get("telegram", {}).get("default", {})
        assert tg.get("channel_id") == "telegram"
        assert tg.get("configured") is True
