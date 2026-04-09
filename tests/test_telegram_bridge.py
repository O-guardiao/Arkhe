"""
Testes do Telegram Gateway em modo bridge.

Valida:
- GatewayConfig com novos campos bridge
- _bridge_post (HTTP POST para api.py)
- _process_via_bridge (extração de resposta)
- _handle_update (fluxo completo: update → bridge → reply)
- TelegramGateway sem RLM (thin client)
- run_in_thread
- poll_once
- Fallback: markdown falha → reenvia texto puro
"""
import json
import threading
import time
from unittest.mock import MagicMock, patch, ANY

import pytest


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestBridgeConfig:
    def test_default_api_base_url(self):
        from rlm.gateway.telegram_gateway import GatewayConfig
        cfg = GatewayConfig()
        assert cfg.api_base_url == "http://127.0.0.1:5000"

    def test_custom_api_base_url(self):
        from rlm.gateway.telegram_gateway import GatewayConfig
        cfg = GatewayConfig(api_base_url="http://10.0.0.5:9000")
        assert cfg.api_base_url == "http://10.0.0.5:9000"

    def test_api_timeout_default(self):
        from rlm.gateway.telegram_gateway import GatewayConfig
        cfg = GatewayConfig()
        assert cfg.api_timeout_s == 120

    def test_no_rlm_fields(self):
        """GatewayConfig não deve ter campos RLM (max_depth, persistent_per_chat)."""
        from rlm.gateway.telegram_gateway import GatewayConfig
        cfg = GatewayConfig()
        assert not hasattr(cfg, "max_depth")
        assert not hasattr(cfg, "persistent_per_chat")
        assert not hasattr(cfg, "max_iterations")


class TestResolveInternalApiBaseUrl:
    def test_prefers_rlm_internal_host(self, monkeypatch: pytest.MonkeyPatch):
        from rlm.core.comms.internal_api import resolve_internal_api_base_url

        monkeypatch.setenv("RLM_INTERNAL_HOST", "http://brain.internal:7777/")
        monkeypatch.setenv("RLM_API_HOST", "127.0.0.1")
        monkeypatch.setenv("RLM_API_PORT", "1")

        assert resolve_internal_api_base_url() == "http://brain.internal:7777"

    def test_falls_back_to_api_host_and_port(self, monkeypatch: pytest.MonkeyPatch):
        from rlm.core.comms.internal_api import resolve_internal_api_base_url

        monkeypatch.delenv("RLM_INTERNAL_HOST", raising=False)
        monkeypatch.setenv("RLM_API_HOST", "0.0.0.0")
        monkeypatch.setenv("RLM_API_PORT", "5001")

        assert resolve_internal_api_base_url() == "http://127.0.0.1:5001"


# ---------------------------------------------------------------------------
# Gateway Init (thin client — sem RLM)
# ---------------------------------------------------------------------------

class TestGatewayInit:
    def test_init_with_config_token(self):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        cfg = GatewayConfig(bot_token="fake:TOKEN123")
        gw = TelegramGateway(config=cfg)
        assert gw.token == "fake:TOKEN123"

    def test_init_with_bot_token_override(self):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        cfg = GatewayConfig(bot_token="config:TOKEN")
        gw = TelegramGateway(config=cfg, bot_token="override:TOKEN")
        assert gw.token == "override:TOKEN"

    def test_init_from_env(self, monkeypatch):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env:TOKEN")
        gw = TelegramGateway(config=GatewayConfig())
        assert gw.token == "env:TOKEN"

    def test_init_no_token_raises(self, monkeypatch):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(ValueError, match="Token do Telegram"):
            TelegramGateway(config=GatewayConfig())

    def test_no_session_manager(self):
        """Gateway bridge não deve ter SessionManager local."""
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        gw = TelegramGateway(config=GatewayConfig(bot_token="fake:T"))
        assert not hasattr(gw, "_sessions")

    def test_stats_include_bridge_errors(self):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        gw = TelegramGateway(config=GatewayConfig(bot_token="fake:T"))
        assert "bridge_errors" in gw._stats


# ---------------------------------------------------------------------------
# _bridge_post
# ---------------------------------------------------------------------------

class TestBridgePost:
    def test_bridge_post_success(self):
        from rlm.gateway.telegram_gateway import _bridge_post
        fake_response = json.dumps({"status": "completed", "response": "Olá!"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("rlm.gateway.telegram_gateway.urllib_request.urlopen", return_value=mock_resp):
            with patch("rlm.gateway.telegram_gateway._build_auth_headers", return_value={"Content-Type": "application/json"}):
                result = _bridge_post("http://127.0.0.1:8000", "telegram:123", {"text": "oi"})

        assert result["status"] == "completed"
        assert result["response"] == "Olá!"

    def test_bridge_post_http_error(self):
        from rlm.gateway.telegram_gateway import _bridge_post
        from urllib.error import HTTPError
        from io import BytesIO

        err = HTTPError(
            url="http://x", code=503, msg="Unavailable",
            hdrs=None, fp=BytesIO(b'{"detail":"draining"}')
        )
        with patch("rlm.gateway.telegram_gateway.urllib_request.urlopen", side_effect=err):
            with patch("rlm.gateway.telegram_gateway._build_auth_headers", return_value={}):
                result = _bridge_post("http://127.0.0.1:8000", "telegram:123", {"text": "oi"})

        assert "error" in result
        assert "503" in result["error"]

    def test_bridge_post_connection_refused(self):
        from rlm.gateway.telegram_gateway import _bridge_post
        with patch("rlm.gateway.telegram_gateway.urllib_request.urlopen", side_effect=ConnectionRefusedError("refused")):
            with patch("rlm.gateway.telegram_gateway._build_auth_headers", return_value={}):
                result = _bridge_post("http://127.0.0.1:8000", "telegram:123", {"text": "oi"})

        assert "error" in result


# ---------------------------------------------------------------------------
# _process_via_bridge
# ---------------------------------------------------------------------------

class TestProcessViaBridge:
    def _make_gw(self):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        return TelegramGateway(config=GatewayConfig(bot_token="fake:T"))

    def test_extracts_response(self):
        gw = self._make_gw()
        with patch("rlm.gateway.telegram_gateway._bridge_post", return_value={"status": "completed", "response": "Resposta OK"}):
            result, already_replied = gw._process_via_bridge(chat_id=123, text="oi", username="user")
        assert result == "Resposta OK"
        assert already_replied is False

    def test_returns_error_on_bridge_failure(self):
        gw = self._make_gw()
        with patch("rlm.gateway.telegram_gateway._bridge_post", return_value={"error": "timeout"}):
            result, already_replied = gw._process_via_bridge(chat_id=123, text="oi", username="user")
        assert "Erro" in result or "erro" in result.lower()
        assert already_replied is False
        assert gw._stats["bridge_errors"] == 1

    def test_truncates_long_input(self):
        gw = self._make_gw()
        long_text = "x" * 5000
        captured_payload = {}

        def capture_post(api_url, client_id, payload, timeout_s=120):
            captured_payload.update(payload)
            return {"status": "completed", "response": "ok"}

        with patch("rlm.gateway.telegram_gateway._bridge_post", side_effect=capture_post):
            gw._process_via_bridge(chat_id=123, text=long_text, username="user")

        assert len(captured_payload["text"]) <= 4000 + 50  # margem para sufixo

    def test_fallback_to_json_dumps_when_no_response_key(self):
        gw = self._make_gw()
        with patch("rlm.gateway.telegram_gateway._bridge_post", return_value={"status": "completed", "unusual_key": "data"}):
            result, already_replied = gw._process_via_bridge(chat_id=123, text="oi", username="user")
        # Deve fazer json.dumps do resultado
        assert "unusual_key" in result

    def test_client_id_format(self):
        gw = self._make_gw()
        captured_client_id = {}

        def capture_post(api_url, client_id, payload, timeout_s=120):
            captured_client_id["id"] = client_id
            return {"status": "completed", "response": "ok"}

        with patch("rlm.gateway.telegram_gateway._bridge_post", side_effect=capture_post):
            gw._process_via_bridge(chat_id=1968290446, text="oi", username="user")

        assert captured_client_id["id"] == "telegram:1968290446"

    def test_already_replied_flag_propagated(self):
        gw = self._make_gw()
        with patch("rlm.gateway.telegram_gateway._bridge_post", return_value={
            "status": "completed", "response": "answer", "already_replied": True,
        }):
            result, already_replied = gw._process_via_bridge(chat_id=123, text="oi", username="user")
        assert result == "answer"
        assert already_replied is True

    def test_already_replied_defaults_false(self):
        gw = self._make_gw()
        with patch("rlm.gateway.telegram_gateway._bridge_post", return_value={
            "status": "completed", "response": "answer",
        }):
            result, already_replied = gw._process_via_bridge(chat_id=123, text="oi", username="user")
        assert already_replied is False

# ---------------------------------------------------------------------------
# _handle_update (fluxo completo)
# ---------------------------------------------------------------------------

class TestHandleUpdate:
    def _make_gw(self, allowed=None):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        cfg = GatewayConfig(bot_token="fake:T", typing_feedback=False)
        if allowed:
            cfg.allowed_chat_ids = allowed
        return TelegramGateway(config=cfg)

    def test_ignores_empty_text(self):
        gw = self._make_gw()
        update = {"message": {"chat": {"id": 1}, "from": {"username": "u"}, "text": ""}}
        gw._handle_update(update)
        assert gw._stats["messages_received"] == 0

    def test_ignores_no_message(self):
        gw = self._make_gw()
        gw._handle_update({})
        assert gw._stats["messages_received"] == 0

    def test_blocks_unauthorized_chat(self):
        gw = self._make_gw(allowed=[999])
        sent = []
        with patch("rlm.gateway.telegram_gateway._send_message", side_effect=lambda *a, **kw: sent.append(a)):
            update = {"message": {"chat": {"id": 123}, "from": {"username": "u"}, "text": "hi"}}
            gw._handle_update(update)
        assert any("não autorizado" in str(s) for s in sent)

    def test_rate_limited(self):
        gw = self._make_gw()
        gw.config.max_requests_per_min = 1
        gw._rate_limiter._max = 1
        sent = []
        with patch("rlm.gateway.telegram_gateway._send_message", side_effect=lambda *a, **kw: sent.append(a)):
            with patch("rlm.gateway.telegram_gateway._bridge_post", return_value={"response": "ok"}):
                update = {"message": {"chat": {"id": 1}, "from": {"username": "u"}, "text": "msg1"}}
                gw._handle_update(update)
                time.sleep(0.05)  # espera thread iniciar
                gw._handle_update(update)
        # Segunda mensagem deve ser bloqueada pelo rate limiter
        rate_msgs = [s for s in sent if "Limite" in str(s)]
        assert len(rate_msgs) >= 1

    def test_command_handled_locally(self):
        gw = self._make_gw()
        sent = []
        with patch("rlm.gateway.telegram_gateway._send_message", side_effect=lambda *a, **kw: sent.append(a)):
            update = {"message": {"chat": {"id": 1}, "from": {"username": "u"}, "text": "/help"}}
            gw._handle_update(update)
        assert any("Arkhe" in str(s) or "help" in str(s).lower() for s in sent)

    def test_regular_message_calls_bridge(self):
        gw = self._make_gw()
        sent_msgs = []
        with patch("rlm.gateway.telegram_gateway._send_message", side_effect=lambda *a, **kw: sent_msgs.append(a)):
            with patch("rlm.gateway.telegram_gateway._bridge_post", return_value={"status": "completed", "response": "Bridge OK"}) as mock_bridge:
                update = {"message": {"chat": {"id": 42}, "from": {"username": "demetrius"}, "text": "hello"}}
                gw._handle_update(update)
                time.sleep(0.3)  # espera thread daemon
        mock_bridge.assert_called_once()
        assert any("Bridge OK" in str(s) for s in sent_msgs)


# ---------------------------------------------------------------------------
# poll_once
# ---------------------------------------------------------------------------

class TestPollOnce:
    def test_poll_once_processes_updates(self):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        gw = TelegramGateway(config=GatewayConfig(bot_token="fake:T", typing_feedback=False))

        updates = [
            {"update_id": 100, "message": {"chat": {"id": 1}, "from": {"username": "u"}, "text": "hi"}}
        ]
        with patch("rlm.gateway.telegram_gateway._get_updates", return_value=updates):
            with patch("rlm.gateway.telegram_gateway._bridge_post", return_value={"response": "ok"}):
                with patch("rlm.gateway.telegram_gateway._send_message"):
                    count = gw.poll_once()
        assert count == 1
        assert gw._offset == 101

    def test_poll_once_empty(self):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        gw = TelegramGateway(config=GatewayConfig(bot_token="fake:T"))
        with patch("rlm.gateway.telegram_gateway._get_updates", return_value=[]):
            count = gw.poll_once()
        assert count == 0


# ---------------------------------------------------------------------------
# run_in_thread
# ---------------------------------------------------------------------------

class TestRunInThread:
    def test_run_in_thread_returns_daemon_thread(self):
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig
        gw = TelegramGateway(config=GatewayConfig(bot_token="fake:T"))

        # Simula getMe ok e depois para imediatamente
        with patch("rlm.gateway.telegram_gateway._tg_request", return_value={"ok": True, "result": {"username": "bot"}}):
            with patch("rlm.gateway.telegram_gateway._get_updates", side_effect=lambda *a, **kw: (gw.stop(), [])[1]):
                t = gw.run_in_thread()
                t.join(timeout=3)
        assert t.daemon is True
        assert t.name == "telegram-gateway"


# ---------------------------------------------------------------------------
# _send_message Markdown fallback
# ---------------------------------------------------------------------------

class TestSendMessageFallback:
    def test_markdown_failure_retries_plaintext(self):
        from rlm.gateway.telegram_gateway import _send_message
        calls = []

        def fake_tg_request(token, method, data=None, timeout=35):
            calls.append(data)
            if data and data.get("parse_mode") == "Markdown":
                return {"ok": False, "description": "bad markdown"}
            return {"ok": True}

        with patch("rlm.gateway.telegram_gateway._tg_request", side_effect=fake_tg_request):
            result = _send_message("fake:T", 123, "test *broken markdown")

        assert len(calls) == 2
        assert calls[0].get("parse_mode") == "Markdown"
        assert "parse_mode" not in calls[1]


# ---------------------------------------------------------------------------
# End-to-end: update → bridge → reply
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_full_flow_update_to_reply(self):
        """Simula update do Telegram passando pelo bridge e recebendo resposta."""
        from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig

        gw = TelegramGateway(config=GatewayConfig(bot_token="fake:T", typing_feedback=False))

        bridge_response = {"status": "completed", "response": "Resposta do RLM via API"}
        sent_replies = []

        def capture_send(token, chat_id, text, parse_mode="Markdown"):
            sent_replies.append({"chat_id": chat_id, "text": text})
            return {"ok": True}

        with patch("rlm.gateway.telegram_gateway._bridge_post", return_value=bridge_response) as mock_bridge:
            with patch("rlm.gateway.telegram_gateway._send_message", side_effect=capture_send):
                update = {
                    "update_id": 200,
                    "message": {
                        "chat": {"id": 1968290446},
                        "from": {"username": "demetrius"},
                        "text": "Qual o status do deploy?"
                    }
                }
                gw._handle_update(update)
                time.sleep(0.5)  # espera thread daemon

        # Verificar que o bridge foi chamado com o client_id correto
        mock_bridge.assert_called_once()
        call_args = mock_bridge.call_args
        assert call_args[0][1] == "telegram:1968290446"
        assert call_args[0][2]["text"] == "Qual o status do deploy?"
        assert call_args[0][2]["from_user"] == "demetrius"

        # Verificar que a resposta foi enviada de volta ao Telegram
        assert len(sent_replies) == 1
        assert sent_replies[0]["chat_id"] == 1968290446
        assert "Resposta do RLM via API" in sent_replies[0]["text"]
