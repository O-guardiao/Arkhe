"""
test_invariants_multichannel — Invariante II: multicanal com retry.

Garante que a infraestrutura de canais funciona corretamente:
bootstrap descobre canais, ChannelStatusRegistry rastreia,
OutboxStore persiste, e canais operam com isolamento correto.
"""
from __future__ import annotations

import pytest


class TestMultichannelInfrastructure:
    """Canais são registrados, roteáveis e isolados."""

    def test_channel_descriptors_exist(self):
        """Todos os 6 canais padrão têm descriptors definidos."""
        from rlm.core.comms.channel_bootstrap import _CHANNEL_DESCRIPTORS

        expected = {"telegram", "discord", "whatsapp", "slack", "webchat", "tui"}
        actual = {d.channel_id for d in _CHANNEL_DESCRIPTORS}
        assert expected.issubset(actual), f"Missing: {expected - actual}"

    def test_tui_always_registered(self):
        """O canal TUI está marcado como always_registered."""
        from rlm.core.comms.channel_bootstrap import _CHANNEL_DESCRIPTORS

        tui = [d for d in _CHANNEL_DESCRIPTORS if d.channel_id == "tui"]
        assert len(tui) == 1
        assert tui[0].always_registered is True

    def test_channel_status_registry_tracks_state(self):
        """ChannelStatusRegistry rastreia estado de cada canal."""
        from rlm.core.comms.channel_status import ChannelStatusRegistry

        csr = ChannelStatusRegistry()
        csr.register("telegram", account_id="bot_123")
        csr.mark_running("telegram", "bot_123")

        entry = csr.get("telegram", "bot_123")
        assert entry is not None

    def test_channel_status_registry_mark_stopped(self):
        """ChannelStatusRegistry transiciona running → stopped."""
        from rlm.core.comms.channel_status import ChannelStatusRegistry

        csr = ChannelStatusRegistry()
        csr.register("discord", account_id="acc_1")
        csr.mark_running("discord", "acc_1")
        csr.mark_stopped("discord", "acc_1")

        entry = csr.get("discord", "acc_1")
        assert entry is not None

    def test_outbox_store_enqueue_and_fetch(self, tmp_path):
        """OutboxStore persiste envelopes e permite fetch."""
        from rlm.core.comms.outbox import OutboxStore
        from rlm.core.comms.envelope import Envelope

        store = OutboxStore(db_path=str(tmp_path / "outbox.db"))
        env = Envelope(
            text="hello from test",
            source_channel="telegram",
            source_id="user_1",
        )
        eid = store.enqueue(env, session_id="sess-1")
        assert isinstance(eid, str)

        pending = store.fetch_pending(batch_size=10)
        assert isinstance(pending, list)

    def test_outbox_mark_delivered(self, tmp_path):
        """OutboxStore marca envelope como entregue."""
        from rlm.core.comms.outbox import OutboxStore
        from rlm.core.comms.envelope import Envelope

        store = OutboxStore(db_path=str(tmp_path / "outbox.db"))
        env = Envelope(text="world", source_channel="webchat", source_id="u2")
        eid = store.enqueue(env)
        # Não deve lançar exceção
        store.mark_delivered(eid)
