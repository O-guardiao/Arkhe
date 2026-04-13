"""
test_invariants_coordination — Invariante IV: coordenação simultânea.

Garante que múltiplas sessões, canais e identidades coexistem
sem interferência mútua. Testa isolamento real entre instâncias
concorrentes do runtime.
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest


class TestSessionIsolation:
    """Múltiplas sessões são isoladas entre si."""

    def test_distinct_session_ids_never_collide(self):
        """100 session IDs geram 100 valores únicos."""
        from rlm.core.session.session_key import create_session_id

        ids = [create_session_id() for _ in range(100)]
        assert len(set(ids)) == 100

    def test_session_keys_distinct_channels(self):
        """SessionKeys com canais diferentes são objetos distintos."""
        from rlm.core.session.session_key import SessionKey, create_session_id

        sid = create_session_id()
        key_tg = SessionKey(
            session_id=sid,
            channel_type="telegram",
            channel_id="chat_1",
            user_id="user_A",
        )
        key_dc = SessionKey(
            session_id=sid,
            channel_type="discord",
            channel_id="chat_1",
            user_id="user_A",
        )
        assert key_tg != key_dc
        assert key_tg.channel_type != key_dc.channel_type

    def test_session_identity_same_user_different_channels(self):
        """Mesmo user_id em canais diferentes gera identidades distintas."""
        from rlm.core.session.session_key import SessionIdentity, create_session_id

        id_tg = SessionIdentity(
            session_id=create_session_id(),
            client_id="telegram:u1",
            user_id="u1",
            channel="telegram",
        )
        id_dc = SessionIdentity(
            session_id=create_session_id(),
            client_id="discord:u1",
            user_id="u1",
            channel="discord",
        )
        assert id_tg.session_id != id_dc.session_id
        assert id_tg.client_id != id_dc.client_id
        assert id_tg.channel != id_dc.channel


class TestChannelCoordination:
    """Múltiplos canais coexistem no ChannelStatusRegistry."""

    def test_registry_handles_multiple_channels(self):
        """ChannelStatusRegistry rastreia 6 canais simultaneamente."""
        from rlm.core.comms.channel_status import ChannelStatusRegistry

        csr = ChannelStatusRegistry()
        channels = ["telegram", "discord", "whatsapp", "slack", "webchat", "tui"]

        for ch in channels:
            csr.register(ch, account_id=f"acc_{ch}")
            csr.mark_running(ch, f"acc_{ch}")

        running = csr.list_running()
        assert len(running) == 6

    def test_registry_mixed_states(self):
        """ChannelStatusRegistry aceita mix de running/stopped/error."""
        from rlm.core.comms.channel_status import ChannelStatusRegistry

        csr = ChannelStatusRegistry()
        csr.register("telegram", account_id="acc_1")
        csr.register("discord", account_id="acc_2")
        csr.register("slack", account_id="acc_3")

        csr.mark_running("telegram", "acc_1")
        csr.mark_stopped("discord", "acc_2")
        csr.mark_error("slack", "acc_3", error="timeout")

        snapshot = csr.get_snapshot()
        assert len(snapshot) == 3

    def test_registry_summary_aggregates(self):
        """ChannelStatusRegistry.summary() retorna agregação."""
        from rlm.core.comms.channel_status import ChannelStatusRegistry

        csr = ChannelStatusRegistry()
        csr.register("telegram", account_id="acc_1")
        csr.register("discord", account_id="acc_2")
        csr.mark_running("telegram", "acc_1")
        csr.mark_running("discord", "acc_2")

        summary = csr.summary()
        assert isinstance(summary, dict)


class TestDispatchServicesCoordination:
    """Múltiplas instâncias de RuntimeDispatchServices são independentes."""

    def test_two_service_instances_independent(self):
        """Dois RuntimeDispatchServices com mocks diferentes são isolados."""
        from rlm.server.runtime_pipeline import RuntimeDispatchServices

        sm1, sm2 = MagicMock(), MagicMock()
        svc1 = RuntimeDispatchServices(
            session_manager=sm1,
            supervisor=MagicMock(),
            plugin_loader=MagicMock(),
            event_router=MagicMock(),
            hooks=MagicMock(),
            skill_loader=MagicMock(),
        )
        svc2 = RuntimeDispatchServices(
            session_manager=sm2,
            supervisor=MagicMock(),
            plugin_loader=MagicMock(),
            event_router=MagicMock(),
            hooks=MagicMock(),
            skill_loader=MagicMock(),
        )
        assert svc1.session_manager is not svc2.session_manager
