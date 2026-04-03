"""
Testes — MessageBus multichannel (Fase 1)

Cobre:
- Envelope: criação, serialização, from_inbound_message, reply()
- RoutingPolicy: EchoBack, AgentDirective, UserPreference, Broadcast
- OutboxStore: enqueue, fetch_pending, mark_delivered, mark_failed, DLQ
- DeliveryWorker: drain batch com mocks
- MessageBus: ingest, route_response, enqueue_outbound
- Integração: pipeline completo inbound → route → outbox → delivery

Execute:
    pytest tests/test_message_bus.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rlm.core.comms.envelope import Direction, Envelope, MessageType
from rlm.core.comms.routing_policy import (
    AgentDirectiveRule,
    BroadcastRule,
    EchoBackRule,
    RoutingPolicy,
    UserPreferenceRule,
)
from rlm.core.comms.outbox import OutboxStore
from rlm.core.comms.delivery_worker import DeliveryWorker
from rlm.core.comms.message_bus import MessageBus


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def tmp_db(tmp_path):
    """DB SQLite temporário para testes."""
    return str(tmp_path / "test_outbox.db")


@pytest.fixture
def outbox(tmp_db):
    return OutboxStore(db_path=tmp_db)


@pytest.fixture
def policy():
    return RoutingPolicy()


@pytest.fixture
def bus(outbox, policy):
    return MessageBus(outbox=outbox, routing_policy=policy)


def _make_inbound(
    channel="telegram",
    source_id="12345",
    text="Olá",
) -> Envelope:
    return Envelope(
        source_channel=channel,
        source_id=source_id,
        source_client_id=f"{channel}:{source_id}",
        direction=Direction.INBOUND,
        message_type=MessageType.TEXT,
        text=text,
    )


def _make_session(**meta):
    """Fake session com metadata dict."""
    return SimpleNamespace(metadata=meta)


# ===========================================================================
# Envelope
# ===========================================================================


class TestEnvelope:
    def test_create_defaults(self):
        env = Envelope()
        assert env.id  # UUID gerado
        assert env.direction == Direction.INBOUND
        assert env.message_type == MessageType.TEXT
        assert isinstance(env.timestamp, datetime)

    def test_client_id_from_source(self):
        env = Envelope(source_channel="whatsapp", source_id="5511999")
        assert env.client_id == "whatsapp:5511999"

    def test_client_id_prefers_source_client_id(self):
        env = Envelope(
            source_channel="whatsapp",
            source_id="5511999",
            source_client_id="whatsapp:5511999887766",
        )
        assert env.client_id == "whatsapp:5511999887766"

    def test_delivery_target_cross_channel(self):
        env = Envelope(
            source_client_id="whatsapp:123",
            target_client_id="telegram:456",
        )
        assert env.delivery_target == "telegram:456"

    def test_delivery_target_echo_back(self):
        env = Envelope(source_client_id="whatsapp:123")
        assert env.delivery_target == "whatsapp:123"

    def test_reply_inverts_source_target(self):
        env = _make_inbound()
        resp = env.reply("Resposta")
        assert resp.direction == Direction.OUTBOUND
        assert resp.target_client_id == env.source_client_id
        assert resp.source_channel == "rlm"
        assert resp.text == "Resposta"
        assert resp.correlation_id == env.id

    def test_to_dict_from_dict_roundtrip(self):
        env = _make_inbound()
        d = env.to_dict()
        assert isinstance(d, dict)
        assert d["direction"] == "inbound"
        assert d["message_type"] == "text"
        restored = Envelope.from_dict(d)
        assert restored.id == env.id
        assert restored.direction == Direction.INBOUND
        assert restored.text == "Olá"

    def test_from_inbound_message(self):
        """Testa conversão InboundMessage → Envelope."""
        from rlm.server.message_envelope import InboundMessage

        msg = InboundMessage(
            channel="whatsapp",
            client_id="whatsapp:5511999",
            text="teste",
            msg_id="abc123",
            from_user="Demet",
            content_type="text",
            channel_meta={"wa_id": "5511999"},
        )
        env = Envelope.from_inbound_message(msg)
        assert env.source_channel == "whatsapp"
        assert env.source_client_id == "whatsapp:5511999"
        assert env.direction == Direction.INBOUND
        assert env.text == "teste"
        assert env.metadata["from_user"] == "Demet"
        assert env.metadata["wa_id"] == "5511999"
        assert env.correlation_id == "abc123"

    def test_message_types(self):
        for mt in MessageType:
            env = Envelope(message_type=mt)
            assert env.message_type == mt

    def test_direction_types(self):
        for d in Direction:
            env = Envelope(direction=d)
            assert env.direction == d


# ===========================================================================
# RoutingPolicy
# ===========================================================================


class TestEchoBackRule:
    def test_always_returns_one_envelope(self):
        rule = EchoBackRule()
        inb = _make_inbound()
        result = rule.evaluate(inb, "ok", _make_session())
        assert len(result) == 1
        assert result[0].text == "ok"
        assert result[0].target_client_id == inb.source_client_id

    def test_preserves_correlation(self):
        rule = EchoBackRule()
        inb = _make_inbound()
        result = rule.evaluate(inb, "reply", _make_session())
        assert result[0].correlation_id == inb.id


class TestAgentDirectiveRule:
    def test_parses_directive(self):
        rule = AgentDirectiveRule()
        inb = _make_inbound()
        text = "@@route:telegram:999@@ Alerta de temperatura"
        result = rule.evaluate(inb, text, _make_session())
        assert len(result) == 1
        assert result[0].target_client_id == "telegram:999"
        assert result[0].text == "Alerta de temperatura"
        assert "@@route" not in result[0].text

    def test_no_directive_returns_empty(self):
        rule = AgentDirectiveRule()
        result = rule.evaluate(_make_inbound(), "texto normal", _make_session())
        assert result == []


class TestUserPreferenceRule:
    def test_routes_to_preferred(self):
        rule = UserPreferenceRule()
        session = _make_session(preferred_channel="slack:T01:C02")
        result = rule.evaluate(_make_inbound(), "msg", session)
        assert len(result) == 1
        assert result[0].target_client_id == "slack:T01:C02"

    def test_no_preference_returns_empty(self):
        rule = UserPreferenceRule()
        result = rule.evaluate(_make_inbound(), "msg", _make_session())
        assert result == []


class TestBroadcastRule:
    def test_multi_target(self):
        rule = BroadcastRule()
        session = _make_session(
            broadcast_channels=["telegram:111", "slack:T:C"],
        )
        result = rule.evaluate(_make_inbound(), "alert", session)
        assert len(result) == 2
        targets = {e.target_client_id for e in result}
        assert targets == {"telegram:111", "slack:T:C"}

    def test_no_channels_returns_empty(self):
        rule = BroadcastRule()
        result = rule.evaluate(_make_inbound(), "msg", _make_session())
        assert result == []


class TestRoutingPolicy:
    def test_agent_directive_wins_over_echoback(self):
        p = RoutingPolicy()
        inb = _make_inbound()
        result = p.route(inb, "@@route:discord:x:y@@ msg", _make_session())
        assert result[0].target_client_id == "discord:x:y"

    def test_fallback_to_echoback(self):
        p = RoutingPolicy()
        inb = _make_inbound()
        result = p.route(inb, "resposta normal", _make_session())
        assert result[0].target_client_id == inb.source_client_id

    def test_broadcast_over_preference(self):
        p = RoutingPolicy()
        session = _make_session(
            broadcast_channels=["telegram:111"],
            preferred_channel="slack:T:C",
        )
        result = p.route(_make_inbound(), "msg", session)
        # Broadcast é regra 2, preferência é regra 3
        assert result[0].target_client_id == "telegram:111"


# ===========================================================================
# OutboxStore
# ===========================================================================


class TestOutboxStore:
    def test_enqueue_and_fetch(self, outbox):
        env = _make_inbound().reply("resposta")
        outbox.enqueue(env)
        rows = outbox.fetch_pending(10)
        assert len(rows) == 1
        assert rows[0]["id"] == env.id
        # fetch_pending retorna rows PRÉ-update; status no DB muda para delivering
        # Verificamos que a segunda chamada não retorna mais (claiming atômico)
        rows2 = outbox.fetch_pending(10)
        assert len(rows2) == 0

    def test_fetch_empty_returns_empty(self, outbox):
        assert outbox.fetch_pending() == []

    def test_mark_delivered(self, outbox):
        env = _make_inbound().reply("ok")
        outbox.enqueue(env)
        outbox.fetch_pending()  # marca como delivering
        outbox.mark_delivered(env.id)
        stats = outbox.stats()
        assert stats.get("delivered") == 1

    def test_mark_failed_retries(self, outbox):
        env = _make_inbound().reply("retry")
        env.max_retries = 3
        outbox.enqueue(env)
        outbox.fetch_pending()
        status = outbox.mark_failed(env.id, "timeout")
        assert status == "pending"

    def test_mark_failed_dlq(self, outbox):
        env = _make_inbound().reply("will fail")
        env.max_retries = 1
        outbox.enqueue(env)
        outbox.fetch_pending()
        status = outbox.mark_failed(env.id, "permanent error")
        assert status == "dlq"

    def test_priority_ordering(self, outbox):
        low = _make_inbound().reply("low")
        low.priority = -1
        high = _make_inbound().reply("high")
        high.priority = 1
        normal = _make_inbound().reply("normal")
        normal.priority = 0
        outbox.enqueue(low)
        outbox.enqueue(high)
        outbox.enqueue(normal)
        rows = outbox.fetch_pending(10)
        # Ordem: priority DESC → high, normal, low
        assert rows[0]["priority"] == 1
        assert rows[1]["priority"] == 0
        assert rows[2]["priority"] == -1

    def test_stats(self, outbox):
        env = _make_inbound().reply("ok")
        outbox.enqueue(env)
        stats = outbox.stats()
        assert stats.get("pending", 0) == 1

    def test_backoff_prevents_immediate_refetch(self, outbox):
        env = _make_inbound().reply("retry")
        env.max_retries = 5
        outbox.enqueue(env)
        outbox.fetch_pending()
        outbox.mark_failed(env.id, "err")
        # A mensagem está pending mas com next_attempt_at no futuro
        rows = outbox.fetch_pending()
        assert len(rows) == 0  # não deve ser fetchada imediatamente


# ===========================================================================
# DeliveryWorker
# ===========================================================================


class TestDeliveryWorker:
    @pytest.mark.asyncio
    async def test_drains_pending(self, outbox):
        """Worker entrega mensagem pendente via adapter mock."""
        mock_registry = MagicMock()
        mock_registry.reply.return_value = True

        env = _make_inbound().reply("entrega")
        outbox.enqueue(env)

        worker = DeliveryWorker(outbox, mock_registry)
        delivered = await worker._drain_batch()
        assert delivered == 1
        mock_registry.reply.assert_called_once()

        stats = outbox.stats()
        assert stats.get("delivered") == 1

    @pytest.mark.asyncio
    async def test_handles_adapter_failure(self, outbox):
        mock_registry = MagicMock()
        mock_registry.reply.return_value = False

        env = _make_inbound().reply("will fail")
        env.max_retries = 3
        outbox.enqueue(env)

        worker = DeliveryWorker(outbox, mock_registry)
        delivered = await worker._drain_batch()
        assert delivered == 0

    @pytest.mark.asyncio
    async def test_handles_adapter_exception(self, outbox):
        mock_registry = MagicMock()
        mock_registry.reply.side_effect = ConnectionError("network down")

        env = _make_inbound().reply("will explode")
        env.max_retries = 3
        outbox.enqueue(env)

        worker = DeliveryWorker(outbox, mock_registry)
        delivered = await worker._drain_batch()
        assert delivered == 0

    @pytest.mark.asyncio
    async def test_start_stop(self, outbox):
        mock_registry = MagicMock()
        worker = DeliveryWorker(outbox, mock_registry)
        await worker.start()
        assert worker.is_alive()
        worker.stop()
        await asyncio.sleep(0.1)  # allow task to finish
        # Após stop, não deve mais drenar
        assert not worker.is_alive() or worker._stopped.is_set()

    @pytest.mark.asyncio
    async def test_emits_events(self, outbox):
        mock_registry = MagicMock()
        mock_registry.reply.return_value = True
        mock_bus = MagicMock()

        env = _make_inbound().reply("ok")
        outbox.enqueue(env)

        worker = DeliveryWorker(outbox, mock_registry, event_bus=mock_bus)
        await worker._drain_batch()
        mock_bus.emit.assert_called()
        args = mock_bus.emit.call_args_list[0]
        assert args[0][0] == "delivery.sent"


# ===========================================================================
# MessageBus
# ===========================================================================


class TestMessageBus:
    def test_ingest_inbound_message(self, bus):
        from rlm.server.message_envelope import InboundMessage

        msg = InboundMessage(
            channel="telegram",
            client_id="tg:12345",
            text="oi",
        )
        env = bus.ingest(msg)
        assert isinstance(env, Envelope)
        assert env.source_client_id == "tg:12345"
        assert env.direction == Direction.INBOUND

    def test_ingest_envelope(self, bus):
        env = _make_inbound()
        result = bus.ingest(env)
        assert result is env

    def test_ingest_rejects_unknown(self, bus):
        with pytest.raises(TypeError):
            bus.ingest({"not": "an envelope"})

    def test_route_response_echoback(self, bus, outbox):
        inb = _make_inbound()
        ids = bus.route_response(inb, "ok", _make_session())
        assert len(ids) == 1
        rows = outbox.fetch_pending()
        assert len(rows) == 1
        assert rows[0]["target_client_id"] == "telegram:12345"

    def test_route_response_directive(self, bus, outbox):
        inb = _make_inbound()
        ids = bus.route_response(
            inb,
            "@@route:whatsapp:999@@ Alerta",
            _make_session(),
        )
        assert len(ids) == 1
        rows = outbox.fetch_pending()
        assert rows[0]["target_client_id"] == "whatsapp:999"
        payload = json.loads(rows[0]["payload"])
        assert "Alerta" in payload["text"]

    def test_enqueue_outbound_direct(self, bus, outbox):
        env = Envelope(
            source_channel="rlm",
            source_id="agent",
            target_client_id="mqtt:esp32-sala",
            direction=Direction.OUTBOUND,
            message_type=MessageType.ACTION,
            text='{"command":"ac_on"}',
        )
        eid = bus.enqueue_outbound(env)
        rows = outbox.fetch_pending()
        assert len(rows) == 1
        assert rows[0]["target_client_id"] == "mqtt:esp32-sala"


# ===========================================================================
# Integration: Pipeline completo
# ===========================================================================


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_flow_inbound_to_delivery(self, bus, outbox):
        """
        Simula fluxo completo:
        1. InboundMessage chega
        2. MessageBus ingere → Envelope
        3. RLM produz response (mock)
        4. MessageBus roteia → Outbox
        5. DeliveryWorker entrega
        """
        from rlm.server.message_envelope import InboundMessage

        # 1. Gateway normaliza
        msg = InboundMessage(
            channel="whatsapp",
            client_id="whatsapp:5511999",
            text="Qual a temperatura?",
        )

        # 2. MessageBus ingere
        inbound_env = bus.ingest(msg)
        assert inbound_env.source_channel == "whatsapp"

        # 3. RLM responde (simulado)
        response = "Sala: 27.3°C"

        # 4. MessageBus roteia
        ids = bus.route_response(inbound_env, response, _make_session())
        assert len(ids) == 1

        # 5. DeliveryWorker entrega
        mock_registry = MagicMock()
        mock_registry.reply.return_value = True
        worker = DeliveryWorker(outbox, mock_registry)
        delivered = await worker._drain_batch()
        assert delivered == 1

        # Verifica que o adapter recebeu o destino correto
        call_args = mock_registry.reply.call_args
        assert call_args[0][0] == "whatsapp:5511999"
        assert "27.3°C" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_cross_channel_via_directive(self, bus, outbox):
        """WhatsApp pergunta → resposta direcionada ao Telegram."""
        inb = Envelope(
            source_channel="whatsapp",
            source_id="5511999",
            source_client_id="whatsapp:5511999",
            direction=Direction.INBOUND,
            text="alerta",
        )
        ids = bus.route_response(
            inb,
            "@@route:telegram:12345@@ Temperatura alta!",
            _make_session(),
        )
        mock_registry = MagicMock()
        mock_registry.reply.return_value = True
        worker = DeliveryWorker(outbox, mock_registry)
        delivered = await worker._drain_batch()
        assert delivered == 1
        assert mock_registry.reply.call_args[0][0] == "telegram:12345"
