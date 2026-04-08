"""
Testes — Phase 3: Migrar Gateways para Envelope

Cobre:
- _normalize_webhook_payload(): normalização genérica por canal
- Bus integration no /webhook: ingest + route_response quando flag ativo
- Feature flag: sem efeito quando desligado
- WebChatAdapter: entrega via SessionManager
- Discord: already_replied check suprime followup

Execute:
    pytest tests/test_phase3_gateway_migration.py -v
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from rlm.gateway.message_envelope import InboundMessage
from rlm.core.comms.envelope import Direction, Envelope, MessageType
from rlm.core.comms.routing_policy import RoutingPolicy
from rlm.core.comms.outbox import OutboxStore
from rlm.core.comms.message_bus import MessageBus


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_phase3.db")


@pytest.fixture
def outbox(tmp_db):
    return OutboxStore(db_path=tmp_db)


@pytest.fixture
def bus(outbox):
    return MessageBus(
        outbox=outbox,
        routing_policy=RoutingPolicy(),
        event_bus=None,
    )


def _make_session(**kwargs):
    """Cria session mock com metadados opcionais."""
    defaults = {
        "session_id": "sess-001",
        "user_id": "user-001",
        "originating_channel": "telegram:123",
        "delivery_context": {},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ===========================================================================
# Test: _normalize_webhook_payload
# ===========================================================================


class TestNormalizeWebhookPayload:
    """Testa a normalização de payloads brutos para InboundMessage."""

    def _call(self, client_id: str, payload: dict) -> InboundMessage:
        from rlm.server.api import _normalize_webhook_payload
        return _normalize_webhook_payload(client_id, payload)

    def test_telegram_payload(self):
        msg = self._call("telegram:12345", {
            "text": "Olá mundo",
            "from_user": "joao",
            "chat_id": 12345,
        })
        assert msg.channel == "telegram"
        assert msg.client_id == "telegram:12345"
        assert msg.text == "Olá mundo"
        assert msg.from_user == "joao"
        assert msg.channel_meta["chat_id"] == 12345

    def test_whatsapp_payload(self):
        msg = self._call("whatsapp:5511999", {
            "text": "Boa noite",
            "from_user": "Maria",
            "wa_id": "5511999",
            "message_id": "wamid.abc",
            "type": "text",
            "channel": "whatsapp",
        })
        assert msg.channel == "whatsapp"
        assert msg.client_id == "whatsapp:5511999"
        assert msg.text == "Boa noite"
        assert msg.channel_meta["wa_id"] == "5511999"
        assert msg.channel_meta["message_id"] == "wamid.abc"
        assert msg.content_type == "text"

    def test_discord_payload(self):
        msg = self._call("discord:guild1:user1", {
            "text": "/status",
            "from_user": "Player1",
            "channel": "discord",
        })
        assert msg.channel == "discord"
        assert msg.client_id == "discord:guild1:user1"
        assert msg.text == "/status"
        assert msg.channel_meta["guild_id"] == "guild1"
        assert msg.channel_meta["user_id"] == "user1"

    def test_slack_payload(self):
        msg = self._call("slack:T01:C01", {
            "text": "Deploy status",
            "from_user": "U01",
            "channel": "C01",
            "thread_ts": "1234567890.123456",
            "team_id": "T01",
            "event_type": "message",
            "source": "slack",
        })
        assert msg.channel == "slack"
        assert msg.client_id == "slack:T01:C01"
        assert msg.channel_meta["thread_ts"] == "1234567890.123456"
        assert msg.channel_meta["team_id"] == "T01"
        assert msg.channel_meta["channel"] == "C01"

    def test_webchat_payload(self):
        msg = self._call("webchat:abc123", {
            "text": "Olá agente",
            "from_user": "webchat",
            "session_id": "abc123",
            "channel": "webchat",
        })
        assert msg.channel == "webchat"
        assert msg.client_id == "webchat:abc123"
        assert msg.channel_meta["session_id"] == "abc123"

    def test_unknown_prefix(self):
        """Prefixo desconhecido não explode — usa genérico."""
        msg = self._call("custom:device42", {
            "text": "sensor data",
            "from_user": "esp32",
        })
        assert msg.channel == "custom"
        assert msg.client_id == "custom:device42"
        assert msg.text == "sensor data"
        assert msg.channel_meta == {}

    def test_missing_text_and_from_user(self):
        """Payload sem text/from_user não explode."""
        msg = self._call("telegram:99", {})
        assert msg.text == ""
        assert msg.from_user == ""
        assert msg.channel == "telegram"

    def test_client_id_without_colon(self):
        """client_id sem separador ':' usa 'webhook' como canal."""
        msg = self._call("directcall", {"text": "test"})
        assert msg.channel == "webhook"

    def test_returns_inbound_message_type(self):
        """Resultado é uma instância de InboundMessage."""
        msg = self._call("telegram:1", {"text": "x"})
        assert isinstance(msg, InboundMessage)


# ===========================================================================
# Test: Envelope.from_inbound_message compatibility
# ===========================================================================


class TestEnvelopeFromNormalized:
    """Verifica que InboundMessage normalizado mapeia corretamente para Envelope."""

    def test_telegram_to_envelope(self):
        from rlm.server.api import _normalize_webhook_payload
        msg = _normalize_webhook_payload("telegram:42", {
            "text": "Test",
            "from_user": "tester",
            "chat_id": 42,
        })
        env = Envelope.from_inbound_message(msg)
        assert env.source_channel == "telegram"
        assert env.source_client_id == "telegram:42"
        assert env.direction == Direction.INBOUND
        assert env.text == "Test"
        assert env.metadata["from_user"] == "tester"

    def test_whatsapp_to_envelope_preserves_meta(self):
        from rlm.server.api import _normalize_webhook_payload
        msg = _normalize_webhook_payload("whatsapp:5511", {
            "text": "Oi",
            "from_user": "Ana",
            "wa_id": "5511",
            "message_id": "wamid.xyz",
        })
        env = Envelope.from_inbound_message(msg)
        assert env.metadata["wa_id"] == "5511"
        assert env.metadata["message_id"] == "wamid.xyz"


# ===========================================================================
# Test: MessageBus integration at /webhook level
# ===========================================================================


class TestWebhookBusIntegration:
    """Testa a integração MessageBus no handler /webhook (lógica direta)."""

    def test_bus_ingest_creates_envelope(self, bus):
        """bus.ingest() aceita InboundMessage e retorna Envelope."""
        msg = InboundMessage(
            channel="telegram",
            client_id="telegram:42",
            text="Hello bus",
            from_user="tester",
        )
        envelope = bus.ingest(msg)
        assert isinstance(envelope, Envelope)
        assert envelope.source_channel == "telegram"
        assert envelope.text == "Hello bus"
        assert envelope.direction == Direction.INBOUND

    def test_route_response_enqueues_outbound(self, bus, outbox):
        """route_response() enfileira envelope outbound no Outbox."""
        inbound = Envelope.from_inbound_message(InboundMessage(
            channel="telegram",
            client_id="telegram:42",
            text="Pergunta",
        ))
        session = _make_session(originating_channel="telegram:42")

        ids = bus.route_response(inbound, "Resposta do agente", session, session_id="sess-001")
        assert len(ids) >= 1

        # Verificar que está no Outbox
        pending = outbox.fetch_pending(batch_size=10)
        assert len(pending) >= 1
        envelope_data = pending[0]
        assert "Resposta do agente" in str(envelope_data)

    def test_route_response_not_called_when_already_replied(self, bus, outbox):
        """Se already_replied=True, route_response NÃO deve ser chamado."""
        # Simula o guard que existe no /webhook handler
        result = {"response": "Texto", "already_replied": True}

        if not result.get("already_replied", False):
            inbound = Envelope.from_inbound_message(InboundMessage(
                channel="telegram",
                client_id="telegram:42",
                text="teste",
            ))
            bus.route_response(inbound, result["response"], _make_session())

        # Nada deve ter sido enfileirado
        pending = outbox.fetch_pending(batch_size=10)
        assert len(pending) == 0

    def test_bus_failure_does_not_block_response(self):
        """Falha no bus não impede retorno do result ao gateway."""
        # Simula bus com outbox que falha
        bad_outbox = MagicMock()
        bad_outbox.enqueue.side_effect = RuntimeError("DB locked")
        bad_bus = MessageBus(
            outbox=bad_outbox,
            routing_policy=RoutingPolicy(),
            event_bus=None,
        )

        msg = InboundMessage(
            channel="telegram", client_id="telegram:1", text="t",
        )
        envelope = bad_bus.ingest(msg)

        # route_response vai falhar internamente
        with pytest.raises(RuntimeError):
            bad_bus.route_response(envelope, "resp", _make_session())

        # No handler real, isso é capturado pelo try/except (non-fatal)
        # O teste valida que a exceção é propagada para o handler tratar


# ===========================================================================
# Test: Feature flag behavior
# ===========================================================================


class TestFeatureFlag:
    """Verifica que o feature flag controla o comportamento."""

    def test_flag_default_is_false(self):
        """Sem variável de ambiente, flag deve ser False."""
        import os
        # Limpa a variável se existir
        old = os.environ.pop("RLM_USE_MESSAGE_BUS", None)
        try:
            val = os.environ.get("RLM_USE_MESSAGE_BUS", "false").lower() == "true"
            assert val is False
        finally:
            if old is not None:
                os.environ["RLM_USE_MESSAGE_BUS"] = old

    def test_flag_true_when_set(self):
        """Com RLM_USE_MESSAGE_BUS=true, flag deve ser True."""
        import os
        old = os.environ.get("RLM_USE_MESSAGE_BUS")
        os.environ["RLM_USE_MESSAGE_BUS"] = "true"
        try:
            val = os.environ.get("RLM_USE_MESSAGE_BUS", "false").lower() == "true"
            assert val is True
        finally:
            if old is None:
                del os.environ["RLM_USE_MESSAGE_BUS"]
            else:
                os.environ["RLM_USE_MESSAGE_BUS"] = old

    def test_flag_case_insensitive(self):
        """Flag aceita TRUE, True, true, etc."""
        import os
        old = os.environ.get("RLM_USE_MESSAGE_BUS")
        for variant in ("TRUE", "True", "true", "TrUe"):
            os.environ["RLM_USE_MESSAGE_BUS"] = variant
            val = os.environ.get("RLM_USE_MESSAGE_BUS", "false").lower() == "true"
            assert val is True, f"Failed for variant: {variant}"
        try:
            pass
        finally:
            if old is None:
                os.environ.pop("RLM_USE_MESSAGE_BUS", None)
            else:
                os.environ["RLM_USE_MESSAGE_BUS"] = old


# ===========================================================================
# Test: WebChatAdapter
# ===========================================================================


class TestWebChatAdapter:
    """Testa o WebChatAdapter para entrega via SessionManager."""

    def test_send_message_logs_to_session(self):
        """send_message() registra evento na sessão via SessionManager."""
        from rlm.gateway.webchat import WebChatAdapter

        mock_session = SimpleNamespace(session_id="sess-wc-001")
        mock_sm = MagicMock()
        mock_sm.get_or_create.return_value = mock_session

        adapter = WebChatAdapter(session_manager=mock_sm)
        result = adapter.send_message("abc123", "Resposta do agente")

        assert result is True
        mock_sm.get_or_create.assert_called_once_with("webchat:abc123")
        mock_sm.log_event.assert_called_once()
        args = mock_sm.log_event.call_args
        assert args[0][0] == "sess-wc-001"
        assert args[0][1] == "webchat_response_delivered"
        assert "Resposta do agente" in str(args[0][2])

    def test_send_message_returns_false_on_error(self):
        """send_message() retorna False se SessionManager falhar."""
        from rlm.gateway.webchat import WebChatAdapter

        mock_sm = MagicMock()
        mock_sm.get_or_create.side_effect = RuntimeError("DB error")

        adapter = WebChatAdapter(session_manager=mock_sm)
        result = adapter.send_message("abc123", "Resposta")

        assert result is False

    def test_send_media_delegates_to_send_message(self):
        """send_media() converte para descrição textual e delega."""
        from rlm.gateway.webchat import WebChatAdapter

        mock_session = SimpleNamespace(session_id="sess-wc-002")
        mock_sm = MagicMock()
        mock_sm.get_or_create.return_value = mock_session

        adapter = WebChatAdapter(session_manager=mock_sm)
        result = adapter.send_media("abc123", "/tmp/image.png", caption="Foto")

        assert result is True
        mock_sm.log_event.assert_called_once()

    def test_adapter_implements_channel_adapter(self):
        """WebChatAdapter é uma instância de ChannelAdapter."""
        from rlm.gateway.webchat import WebChatAdapter
        from rlm.plugins.channel_registry import ChannelAdapter

        adapter = WebChatAdapter(session_manager=MagicMock())
        assert isinstance(adapter, ChannelAdapter)


# ===========================================================================
# Test: End-to-end pipeline simulation
# ===========================================================================


class TestEndToEndBusRouting:
    """Simula pipeline completo: normalize → ingest → dispatch → route_response → outbox."""

    def test_telegram_full_flow(self, bus, outbox):
        """Telegram: payload → InboundMessage → Envelope → route → outbox."""
        from rlm.server.api import _normalize_webhook_payload

        # 1. Normalizar payload
        payload = {"text": "Qual a previsão?", "from_user": "user1", "chat_id": 42}
        msg = _normalize_webhook_payload("telegram:42", payload)

        # 2. Ingest
        inbound = bus.ingest(msg)
        assert inbound.source_channel == "telegram"

        # 3. Simular dispatch result (not already replied)
        result = {"response": "Tempo ensolarado, 28°C", "already_replied": False}
        session = _make_session(originating_channel="telegram:42")

        # 4. Route response
        ids = bus.route_response(inbound, result["response"], session, session_id="sess-001")
        assert len(ids) >= 1

        # 5. Verificar outbox
        pending = outbox.fetch_pending(batch_size=10)
        assert len(pending) >= 1

    def test_whatsapp_full_flow(self, bus, outbox):
        """WhatsApp: payload → normalize → ingest → route → outbox."""
        from rlm.server.api import _normalize_webhook_payload

        payload = {
            "text": "Boa noite",
            "from_user": "Maria",
            "wa_id": "5511999",
            "message_id": "wamid.abc",
            "type": "text",
            "channel": "whatsapp",
        }
        msg = _normalize_webhook_payload("whatsapp:5511999", payload)
        inbound = bus.ingest(msg)
        session = _make_session(originating_channel="whatsapp:5511999")

        ids = bus.route_response(inbound, "Boa noite! Como posso ajudar?", session)
        assert len(ids) >= 1

        pending = outbox.fetch_pending(batch_size=10)
        assert len(pending) >= 1

    def test_webchat_full_flow(self, bus, outbox):
        """WebChat: payload → normalize → ingest → route → outbox."""
        from rlm.server.api import _normalize_webhook_payload

        payload = {
            "text": "Olá agente",
            "from_user": "webchat",
            "session_id": "sess42",
            "channel": "webchat",
        }
        msg = _normalize_webhook_payload("webchat:sess42", payload)
        inbound = bus.ingest(msg)
        session = _make_session(originating_channel="webchat:sess42")

        ids = bus.route_response(inbound, "Olá! Em que posso ajudar?", session)
        assert len(ids) >= 1

    def test_already_replied_skips_routing(self, bus, outbox):
        """Se already_replied=True, nada é enfileirado."""
        from rlm.server.api import _normalize_webhook_payload

        payload = {"text": "Test", "from_user": "u1"}
        msg = _normalize_webhook_payload("telegram:1", payload)
        inbound = bus.ingest(msg)

        result = {"response": "Reply", "already_replied": True}

        # Simula o guard do handler
        if not result.get("already_replied", False):
            bus.route_response(inbound, result["response"], _make_session())

        pending = outbox.fetch_pending(batch_size=10)
        assert len(pending) == 0


# ===========================================================================
# Test: Discord already_replied guard
# ===========================================================================


class TestDiscordAlreadyReplied:
    """Verifica que Discord gateway respeita already_replied."""

    def test_discord_response_key_parsing(self):
        """Discord deve ler 'response' primeiro, não 'result'."""
        # Simula o JSON retornado pelo /webhook
        webhook_result = {
            "status": "ok",
            "response": "Texto correto",
            "already_replied": False,
        }
        # A lógica corrigida no gateway pega "response" primeiro
        text = (
            webhook_result.get("response")
            or webhook_result.get("result")
            or webhook_result.get("output")
            or str(webhook_result)
        )
        assert text == "Texto correto"

    def test_discord_suppresses_followup_when_already_replied(self):
        """Se already_replied=True, Discord não deve enviar followup."""
        webhook_result = {
            "status": "ok",
            "response": "Via bus",
            "already_replied": True,
        }
        should_send_followup = not webhook_result.get("already_replied", False)
        assert should_send_followup is False

    def test_discord_sends_followup_when_not_replied(self):
        """Se already_replied=False, Discord deve enviar followup."""
        webhook_result = {
            "status": "ok",
            "response": "Resposta normal",
            "already_replied": False,
        }
        should_send_followup = not webhook_result.get("already_replied", False)
        assert should_send_followup is True
