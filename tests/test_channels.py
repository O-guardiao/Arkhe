"""Tests para os canais multi-plataforma do RLM.

Cobertura:
  - rlm/plugins/discord.py   — funções REPL, DiscordAdapter, manifest
  - rlm/plugins/whatsapp.py  — funções REPL, WhatsAppAdapter, _normalize_to
  - rlm/plugins/slack.py     — funções REPL, SlackAdapter, manifest
  - rlm/server/discord_gateway.py  — _verify_discord_signature, _extract_interaction_data, endpoints
  - rlm/server/whatsapp_gateway.py — verificação hub.challenge, processamento inbound
  - rlm/server/slack_gateway.py    — _verify_slack_signature, endpoints, filtros
  - rlm/server/webchat.py          — health, message, stream, cleanup
  - rlm/cli/main.py                — novos subcomando doctor, skill, channel

Todos os testes são offline — zero chamadas reais a APIs externas.
Requisições HTTP são mockadas via unittest.mock.patch sobre urllib.request.urlopen.
FastAPI TestClient (httpx) é usado para os endpoints de gateway.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ===========================================================================
# Helpers reutilizáveis
# ===========================================================================

class _MockResponse:
    """Simula um objeto de resposta do urllib.request.urlopen."""

    def __init__(self, data: bytes | dict | list, status: int = 200) -> None:
        if isinstance(data, (dict, list)):
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        else:
            raw = data
        self._data = raw
        self.status = status

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_MockResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def _make_http_error(code: int, body: str = "") -> HTTPError:
    """Cria um HTTPError de urllib com corpo legível."""
    return HTTPError(
        url="https://example.com",
        code=code,
        msg="Error",
        hdrs={},  # type: ignore[arg-type]
        fp=BytesIO(body.encode("utf-8")),
    )


def _mock_urlopen(response: _MockResponse):
    """Retorna um callable que retorna o MockResponse (como context manager)."""
    return MagicMock(return_value=response)


def _slack_hmac_headers(signing_secret: str, body: bytes, ts: str | None = None) -> dict:
    """Gera headers válidos de assinatura Slack para testes."""
    timestamp = ts or str(int(time.time()))
    base = f"v0:{timestamp}:".encode("utf-8") + body
    sig = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), base, hashlib.sha256
    ).hexdigest()
    return {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": sig}


# ===========================================================================
# 1. Discord Plugin
# ===========================================================================

class TestDiscordManifest:
    def test_manifest_name(self) -> None:
        from rlm.plugins.discord import MANIFEST
        assert MANIFEST.name == "discord"

    def test_manifest_version(self) -> None:
        from rlm.plugins.discord import MANIFEST
        assert MANIFEST.version == "1.0.0"

    def test_manifest_has_all_functions(self) -> None:
        from rlm.plugins.discord import MANIFEST
        expected = {
            "send_webhook", "send_embed", "send_channel_message",
            "pin_message", "create_thread", "get_channel_messages",
        }
        assert expected == set(MANIFEST.functions)

    def test_manifest_no_external_requires(self) -> None:
        from rlm.plugins.discord import MANIFEST
        assert MANIFEST.requires == []


class TestDiscordHelpers:
    def test_get_webhook_url_raises_if_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        from rlm.plugins.discord import _get_webhook_url
        with pytest.raises(ValueError, match="DISCORD_WEBHOOK_URL"):
            _get_webhook_url()

    def test_get_webhook_url_returns_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
        from rlm.plugins.discord import _get_webhook_url
        assert _get_webhook_url() == "https://discord.com/api/webhooks/x/y"

    def test_get_bot_token_auto_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "myrealtoken")
        from rlm.plugins.discord import _get_bot_token
        assert _get_bot_token() == "Bot myrealtoken"

    def test_get_bot_token_keeps_existing_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot alreadyprefixed")
        from rlm.plugins.discord import _get_bot_token
        assert _get_bot_token() == "Bot alreadyprefixed"

    def test_get_bot_token_raises_if_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        from rlm.plugins.discord import _get_bot_token
        with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
            _get_bot_token()


class TestDiscordSendWebhook:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
        resp = _MockResponse({})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import send_webhook
            result = send_webhook("Olá Discord!")
        assert result == "✓ enviado para discord"

    def test_explicit_webhook_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        resp = _MockResponse({})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import send_webhook
            result = send_webhook("Hi", webhook_url="https://discord.com/api/webhooks/X/Y")
        assert result == "✓ enviado para discord"

    def test_http_error_returns_error_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
        with patch(
            "rlm.plugins.discord.urllib_request.urlopen",
            side_effect=_make_http_error(400, "Bad Request"),
        ):
            from rlm.plugins.discord import send_webhook
            result = send_webhook("Texto")
        assert "Erro Discord" in result
        assert "400" in result

    def test_truncates_long_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({})

        with patch("rlm.plugins.discord.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.discord import send_webhook
            send_webhook("x" * 3000)

        assert len(captured[0]["content"]) == 2000

    def test_custom_username_and_avatar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({})

        with patch("rlm.plugins.discord.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.discord import send_webhook
            send_webhook("Msg", username="RLM", avatar_url="https://img.example.com/a.png")

        assert captured[0]["username"] == "RLM"
        assert captured[0]["avatar_url"] == "https://img.example.com/a.png"


class TestDiscordSendEmbed:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
        resp = _MockResponse({})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import send_embed
            result = send_embed("Título", "Descrição aqui")
        assert result == "✓ embed enviado"

    def test_with_fields_footer_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({})

        with patch("rlm.plugins.discord.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.discord import send_embed
            send_embed(
                "T", "D",
                color=0xFF0000,
                fields=[{"name": "Key", "value": "Val", "inline": True}],
                footer="Rodapé",
                url="https://example.com",
            )

        embed = captured[0]["embeds"][0]
        assert embed["color"] == 0xFF0000
        assert embed["footer"]["text"] == "Rodapé"
        assert embed["url"] == "https://example.com"
        assert embed["fields"][0]["name"] == "Key"
        assert embed["fields"][0]["inline"] is True

    def test_error_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
        resp = _MockResponse({"ok": False, "error": "missing perms"})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import send_embed
            result = send_embed("T", "D")
        assert "Erro" in result


class TestDiscordSendChannelMessage:
    def test_success_returns_message_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot tkn123")
        resp = _MockResponse({"id": "999888777"})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import send_channel_message
            result = send_channel_message("12345678", "Texto")
        assert result == "999888777"

    def test_error_no_id_in_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot tkn123")
        resp = _MockResponse({"ok": False, "error": "missing_access"})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import send_channel_message
            result = send_channel_message("12345678", "Texto")
        assert "Erro Discord channel" in result

    def test_truncates_to_2000(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot tkn123")
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({"id": "1"})

        with patch("rlm.plugins.discord.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.discord import send_channel_message
            send_channel_message("1", "y" * 5000)

        assert len(captured[0]["content"]) == 2000


class TestDiscordOtherFunctions:
    def test_pin_message_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot t")
        resp = _MockResponse({})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import pin_message
            assert pin_message("ch1", "msg1") == "✓ mensagem fixada"

    def test_pin_message_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot t")
        resp = _MockResponse({"ok": False, "error": "forbidden"})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import pin_message
            result = pin_message("ch1", "msg1")
        assert "Erro" in result

    def test_create_thread_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot t")
        resp = _MockResponse({"id": "thread-1234"})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import create_thread
            result = create_thread("ch1", "Novo thread")
        assert result == "thread-1234"

    def test_create_thread_truncates_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot t")
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({"id": "1"})

        with patch("rlm.plugins.discord.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.discord import create_thread
            create_thread("ch1", "n" * 200)

        assert len(captured[0]["name"]) == 100

    def test_get_channel_messages_returns_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot t")
        messages = [
            {"id": "1", "author": {"username": "alice"}, "content": "hi", "timestamp": "2025-01-01T00:00:00Z"},
            {"id": "2", "author": {"username": "bob"}, "content": "hey", "timestamp": "2025-01-02T00:00:00Z"},
        ]
        resp = _MockResponse(messages)
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import get_channel_messages
            result = get_channel_messages("ch1", limit=2)
        assert len(result) == 2
        assert result[0]["author"] == "alice"
        assert result[1]["content"] == "hey"

    def test_get_channel_messages_empty_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot t")
        # API retorna dict em vez de list → retorna []
        resp = _MockResponse({"ok": False, "error": "no_perm"})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import get_channel_messages
            result = get_channel_messages("ch1")
        assert result == []

    def test_get_channel_messages_limit_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot t")
        captured_url: list[str] = []

        def fake_urlopen(req, timeout=10):
            captured_url.append(req.full_url)
            return _MockResponse([])

        with patch("rlm.plugins.discord.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.discord import get_channel_messages
            get_channel_messages("ch1", limit=200)  # deve ser clamped a 100

        assert "limit=100" in captured_url[0]


class TestDiscordAdapter:
    def test_send_message_webhook_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hooks.discord.com/x")
        resp = _MockResponse({})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import DiscordAdapter
            adapter = DiscordAdapter()
            assert adapter.send_message("webhook", "Mensagem") is True

    def test_send_message_channel_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "Bot tok")
        resp = _MockResponse({"id": "555"})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import DiscordAdapter
            adapter = DiscordAdapter()
            assert adapter.send_message("123456789", "Aviso") is True

    def test_send_message_returns_false_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hooks.discord.com/x")
        with patch(
            "rlm.plugins.discord.urllib_request.urlopen",
            side_effect=_make_http_error(403),
        ):
            from rlm.plugins.discord import DiscordAdapter
            adapter = DiscordAdapter()
            assert adapter.send_message("webhook", "Msg") is False

    def test_send_media_empty_target_uses_webhook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hooks.discord.com/x")
        resp = _MockResponse({})
        with patch("rlm.plugins.discord.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.discord import DiscordAdapter
            adapter = DiscordAdapter()
            result = adapter.send_media("", "https://img.example.com/photo.jpg", "Legenda")
        assert result is True


# ===========================================================================
# 2. WhatsApp Plugin
# ===========================================================================

class TestWhatsAppManifest:
    def test_manifest_name(self) -> None:
        from rlm.plugins.whatsapp import MANIFEST
        assert MANIFEST.name == "whatsapp"

    def test_manifest_has_all_functions(self) -> None:
        from rlm.plugins.whatsapp import MANIFEST
        expected = {
            "send_text", "send_template", "send_image", "send_document",
            "send_audio", "send_reaction", "mark_as_read", "get_media_url",
        }
        assert expected == set(MANIFEST.functions)

    def test_no_external_requires(self) -> None:
        from rlm.plugins.whatsapp import MANIFEST
        assert MANIFEST.requires == []


class TestNormalizeTo:
    def test_strips_plus(self) -> None:
        from rlm.plugins.whatsapp import _normalize_to
        assert _normalize_to("+5511999990000") == "5511999990000"

    def test_strips_spaces_and_dashes(self) -> None:
        from rlm.plugins.whatsapp import _normalize_to
        assert _normalize_to("+55 11 9999-0000") == "551199990000"

    def test_already_normalized(self) -> None:
        from rlm.plugins.whatsapp import _normalize_to
        assert _normalize_to("5511999990000") == "5511999990000"


class TestWhatsAppHelpers:
    def test_get_phone_id_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WHATSAPP_PHONE_ID", raising=False)
        from rlm.plugins.whatsapp import _get_phone_id
        with pytest.raises(ValueError, match="WHATSAPP_PHONE_ID"):
            _get_phone_id()

    def test_get_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WHATSAPP_TOKEN", raising=False)
        from rlm.plugins.whatsapp import _get_token
        with pytest.raises(ValueError, match="WHATSAPP_TOKEN"):
            _get_token()


class TestWhatsAppSendText:
    def _env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHATSAPP_PHONE_ID", "phone123")
        monkeypatch.setenv("WHATSAPP_TOKEN", "token_abc")

    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import send_text
            result = send_text("+5511999990000", "Olá!")
        assert result == "✓ mensagem enviada"

    def test_error_no_messages_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"error": {"message": "Invalid phone"}})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import send_text
            result = send_text("+5511999990000", "Olá!")
        assert "Erro WhatsApp text" in result

    def test_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        with patch(
            "rlm.plugins.whatsapp.urllib_request.urlopen",
            side_effect=_make_http_error(401, "Unauthorized"),
        ):
            from rlm.plugins.whatsapp import send_text
            result = send_text("+5511999990000", "Msg")
        assert "Erro WhatsApp text" in result

    def test_payload_structure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({"messages": [{"id": "1"}]})

        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.whatsapp import send_text
            send_text("+55 11 9999-0000", "Texto teste")

        p = captured[0]
        assert p["messaging_product"] == "whatsapp"
        assert p["to"] == "551199990000"  # normalizado
        assert p["type"] == "text"
        assert p["text"]["body"] == "Texto teste"


class TestWhatsAppSendTemplate:
    def _env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHATSAPP_PHONE_ID", "phone123")
        monkeypatch.setenv("WHATSAPP_TOKEN", "token_abc")

    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import send_template
            result = send_template("+5511999990000", "hello_world", "pt_BR")
        assert result == "✓ template enviado"

    def test_with_components(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({"messages": [{"id": "1"}]})

        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.whatsapp import send_template
            components = [{"type": "body", "parameters": [{"type": "text", "text": "João"}]}]
            send_template("+5511999990000", "order_update", "pt_BR", components=components)

        tmpl = captured[0]["template"]
        assert tmpl["name"] == "order_update"
        assert len(tmpl["components"]) == 1


class TestWhatsAppMediaFunctions:
    def _env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHATSAPP_PHONE_ID", "phone123")
        monkeypatch.setenv("WHATSAPP_TOKEN", "token_abc")

    def test_send_image_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import send_image
            assert send_image("+5511999990000", "https://img.com/foto.jpg", "Legenda") == "✓ imagem enviada"

    def test_send_image_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({"messages": [{"id": "1"}]})

        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.whatsapp import send_image
            send_image("+5511999990000", "https://img.com/foto.jpg", "Caption aqui")

        img = captured[0]["image"]
        assert img["link"] == "https://img.com/foto.jpg"
        assert img["caption"] == "Caption aqui"

    def test_send_document_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import send_document
            assert send_document(
                "+5511999990000", "https://docs.com/r.pdf", "Relatório", "relatorio.pdf"
            ) == "✓ documento enviado"

    def test_send_audio_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import send_audio
            assert send_audio("+5511999990000", "https://cdn.com/audio.mp3") == "✓ áudio enviado"

    def test_send_reaction_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import send_reaction
            assert send_reaction("+5511999990000", "wamid.ABC123", "👍") == "✓ reação enviada"

    def test_mark_as_read_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"success": True})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import mark_as_read
            assert mark_as_read("wamid.MSG123") == "✓ marcado como lido"

    def test_get_media_url_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"url": "https://cdn.whatsapp.net/media/abc123", "mime_type": "image/jpeg"})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import get_media_url
            result = get_media_url("media_id_123")
        assert result == "https://cdn.whatsapp.net/media/abc123"

    def test_get_media_url_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"ok": False, "error": "not_found"})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import get_media_url
            result = get_media_url("media_id_123")
        assert "Erro get_media_url" in result


class TestWhatsAppAdapter:
    def _env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHATSAPP_PHONE_ID", "phone123")
        monkeypatch.setenv("WHATSAPP_TOKEN", "token_abc")

    def test_send_message_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.whatsapp import WhatsAppAdapter
            adapter = WhatsAppAdapter()
            assert adapter.send_message("5511999990000", "Olá") is True

    def test_send_message_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        with patch(
            "rlm.plugins.whatsapp.urllib_request.urlopen",
            side_effect=_make_http_error(401),
        ):
            from rlm.plugins.whatsapp import WhatsAppAdapter
            assert WhatsAppAdapter().send_message("5511999990000", "Msg") is False

    def test_send_media_routes_image_by_extension(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        calls: list[str] = []

        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url)
            return resp

        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.whatsapp import WhatsAppAdapter
            adapter = WhatsAppAdapter()
            result = adapter.send_media("5511999990000", "https://cdn.com/photo.jpg", "Foto")
        assert result is True

    def test_send_media_routes_audio_by_extension(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return resp

        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.whatsapp import WhatsAppAdapter
            WhatsAppAdapter().send_media("5511999990000", "https://cdn.com/voz.mp3")

        assert captured[0]["type"] == "audio"

    def test_send_media_routes_document_by_extension(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return resp

        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.whatsapp import WhatsAppAdapter
            WhatsAppAdapter().send_media("5511999990000", "https://cdn.com/relatorio.pdf", "Rel")

        assert captured[0]["type"] == "document"

    def test_send_media_routes_webp_as_image(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._env(monkeypatch)
        resp = _MockResponse({"messages": [{"id": "m1"}]})
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return resp

        with patch("rlm.plugins.whatsapp.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.whatsapp import WhatsAppAdapter
            WhatsAppAdapter().send_media("5511999990000", "https://cdn.com/sticker.webp")

        assert captured[0]["type"] == "image"


# ===========================================================================
# 3. Slack Plugin
# ===========================================================================

class TestSlackManifest:
    def test_manifest_name(self) -> None:
        from rlm.plugins.slack import MANIFEST
        assert MANIFEST.name == "slack"

    def test_manifest_has_all_functions(self) -> None:
        from rlm.plugins.slack import MANIFEST
        expected = {
            "post_message", "post_blocks", "post_ephemeral", "post_reply",
            "add_reaction", "upload_snippet", "get_channel_history", "send_webhook",
        }
        assert expected == set(MANIFEST.functions)


class TestSlackHelpers:
    def test_get_bot_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        from rlm.plugins.slack import _get_bot_token
        with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
            _get_bot_token()

    def test_get_webhook_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        from rlm.plugins.slack import _get_webhook_url
        with pytest.raises(ValueError, match="SLACK_WEBHOOK_URL"):
            _get_webhook_url()


class TestSlackPostMessage:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": True, "channel": "C123", "ts": "111.222"})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import post_message
            assert post_message("#geral", "Deploy ok!") == "✓ mensagem enviada ao Slack"

    def test_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": False, "error": "channel_not_found"})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import post_message
            result = post_message("#invalido", "Msg")
        assert "Erro Slack" in result
        assert "channel_not_found" in result

    def test_payload_contains_channel_and_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({"ok": True})

        with patch("rlm.plugins.slack.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.slack import post_message
            post_message("C12345ABC", "Texto Slack")

        assert captured[0]["channel"] == "C12345ABC"
        assert captured[0]["text"] == "Texto Slack"


class TestSlackPostBlocks:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": True})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import post_blocks
            blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*Alerta*"}}]
            assert post_blocks("#alertas", blocks) == "✓ blocks enviados"

    def test_blocks_in_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({"ok": True})

        with patch("rlm.plugins.slack.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.slack import post_blocks
            blocks = [{"type": "divider"}]
            post_blocks("C123", blocks, text="fallback")

        assert captured[0]["blocks"] == [{"type": "divider"}]
        assert captured[0]["text"] == "fallback"


class TestSlackPostEphemeral:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": True})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import post_ephemeral
            assert post_ephemeral("C123", "U456", "Só pra você") == "✓ ephemeral enviado"


class TestSlackPostReply:
    def test_success_returns_ts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": True, "ts": "1700000000.123456"})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import post_reply
            result = post_reply("C123", "1700000000.000000", "Resposta")
        assert result.startswith("ts:")
        assert "1700000000.123456" in result

    def test_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": False, "error": "not_in_channel"})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import post_reply
            result = post_reply("C123", "ts.001", "Resposta")
        assert "Erro Slack thread" in result


class TestSlackAddReaction:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": True})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import add_reaction
            assert add_reaction("C123", "ts.001", "white_check_mark") == "✓ reação adicionada"

    def test_strips_colon_from_emoji(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({"ok": True})

        with patch("rlm.plugins.slack.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.slack import add_reaction
            add_reaction("C123", "ts.001", ":thumbsup:")

        assert captured[0]["name"] == "thumbsup"


class TestSlackGetChannelHistory:
    def test_returns_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        messages = [
            {"ts": "1.0", "user": "U1", "text": "olá", "type": "message"},
            {"ts": "2.0", "user": "U2", "text": "oi", "type": "message"},
        ]
        resp = _MockResponse({"ok": True, "messages": messages})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import get_channel_history
            result = get_channel_history("C123", limit=2)
        assert len(result) == 2
        assert result[0]["text"] == "olá"

    def test_returns_empty_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": False, "error": "channel_not_found"})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import get_channel_history
            result = get_channel_history("C123")
        assert result == []

    def test_limit_clamped_to_100(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        captured: list[dict] = []

        def fake_urlopen(req, timeout=10):
            captured.append(json.loads(req.data.decode()))
            return _MockResponse({"ok": True, "messages": []})

        with patch("rlm.plugins.slack.urllib_request.urlopen", fake_urlopen):
            from rlm.plugins.slack import get_channel_history
            get_channel_history("C123", limit=9999)

        assert captured[0]["limit"] == 100


class TestSlackSendWebhook:
    def test_success_body_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
        resp = _MockResponse(b"ok")
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import send_webhook
            assert send_webhook("Alerta!") == "✓ enviado via webhook"

    def test_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
        with patch(
            "rlm.plugins.slack.urllib_request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            from rlm.plugins.slack import send_webhook
            result = send_webhook("Msg")
        assert "403" in result

    def test_generic_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
        with patch(
            "rlm.plugins.slack.urllib_request.urlopen",
            side_effect=ConnectionError("timeout"),
        ):
            from rlm.plugins.slack import send_webhook
            result = send_webhook("Msg")
        assert "Erro Slack webhook" in result


class TestSlackAdapter:
    def test_send_message_webhook_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
        resp = _MockResponse(b"ok")
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import SlackAdapter
            assert SlackAdapter().send_message("webhook", "Msg") is True

    def test_send_message_channel_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": True})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import SlackAdapter
            assert SlackAdapter().send_message("#geral", "Aviso") is True

    def test_send_message_returns_false_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-123")
        resp = _MockResponse({"ok": False, "error": "invalid_auth"})
        with patch("rlm.plugins.slack.urllib_request.urlopen", _mock_urlopen(resp)):
            from rlm.plugins.slack import SlackAdapter
            assert SlackAdapter().send_message("C123", "Msg") is False


# ===========================================================================
# 4. Discord Gateway
# ===========================================================================

@pytest.fixture(scope="module")
def discord_client() -> TestClient:
    from rlm.gateway.discord_gateway import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


class TestVerifyDiscordSignature:
    def test_skip_verify_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RLM_DISCORD_SKIP_VERIFY", "true")
        from rlm.gateway.discord_gateway import _verify_discord_signature
        import unittest.mock as mock
        # Simula ausência do pacote cryptography para acionar o branch de skip
        crypto_mods = {
            "cryptography": None,
            "cryptography.exceptions": None,
            "cryptography.hazmat": None,
            "cryptography.hazmat.primitives": None,
            "cryptography.hazmat.primitives.asymmetric": None,
            "cryptography.hazmat.primitives.asymmetric.ed25519": None,
        }
        with mock.patch.dict("sys.modules", crypto_mods):
            result = _verify_discord_signature("", "12345", b"body", "aabbcc")
        assert result is True

    def test_missing_cryptography_without_skip_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RLM_DISCORD_SKIP_VERIFY", "false")
        from rlm.gateway.discord_gateway import _verify_discord_signature
        import unittest.mock as mock

        with mock.patch.dict("sys.modules", {"cryptography": None,
                                              "cryptography.hazmat": None,
                                              "cryptography.hazmat.primitives": None,
                                              "cryptography.hazmat.primitives.asymmetric": None,
                                              "cryptography.hazmat.primitives.asymmetric.ed25519": None,
                                              "cryptography.exceptions": None}):
            with pytest.raises((RuntimeError, ImportError)):
                _verify_discord_signature("deadbeef", "ts", b"body", "sigXX")


class TestExtractInteractionData:
    def test_application_command_with_options(self) -> None:
        from rlm.gateway.discord_gateway import _extract_interaction_data
        interaction = {
            "type": 2,
            "guild_id": "guild_abc",
            "channel_id": "ch_111",
            "member": {"user": {"id": "user_123", "username": "alice", "global_name": "Alice"}},
            "data": {
                "name": "rlm",
                "options": [{"name": "query", "value": "qual é o clima?"}],
            },
            "token": "tok_xyz",
        }
        info = _extract_interaction_data(interaction)
        assert info["type"] == 2
        assert info["command"] == "rlm"
        assert info["args"] == "qual é o clima?"
        assert info["user_id"] == "user_123"
        assert info["username"] == "Alice"
        assert info["guild_id"] == "guild_abc"

    def test_message_component_custom_id(self) -> None:
        from rlm.gateway.discord_gateway import _extract_interaction_data
        interaction = {
            "type": 3,
            "guild_id": "guild_x",
            "data": {"custom_id": "btn_confirm", "component_type": 2},
            "member": {"user": {"id": "u1", "username": "bob"}},
        }
        info = _extract_interaction_data(interaction)
        assert info["type"] == 3
        assert info["custom_id"] == "btn_confirm"

    def test_dm_uses_user_not_member(self) -> None:
        from rlm.gateway.discord_gateway import _extract_interaction_data
        interaction = {
            "type": 2,
            "user": {"id": "dm_user", "username": "carlos"},
            "data": {"name": "ping", "options": []},
        }
        info = _extract_interaction_data(interaction)
        assert info["user_id"] == "dm_user"
        assert info["guild_id"] == "dm"

    def test_no_options_empty_args(self) -> None:
        from rlm.gateway.discord_gateway import _extract_interaction_data
        interaction = {
            "type": 2,
            "data": {"name": "help", "options": []},
            "member": {"user": {"id": "u1", "username": "x"}},
        }
        info = _extract_interaction_data(interaction)
        assert info["args"] == ""
        assert info["command"] == "help"


class TestDiscordInteractionsEndpoint:
    def test_ping_returns_type_1(
        self, discord_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RLM_DISCORD_SKIP_VERIFY", "true")
        monkeypatch.delenv("DISCORD_APP_PUBLIC_KEY", raising=False)

        body = json.dumps({"type": 1}).encode()
        resp = discord_client.post(
            "/discord/interactions",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": "aabbcc",
                "X-Signature-Timestamp": "12345",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["type"] == 1

    def test_application_command_returns_deferred(
        self, discord_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RLM_DISCORD_SKIP_VERIFY", "true")
        monkeypatch.delenv("DISCORD_APP_PUBLIC_KEY", raising=False)

        interaction = {
            "type": 2,
            "guild_id": "g1",
            "member": {"user": {"id": "u1", "username": "tester"}},
            "data": {"name": "rlm", "options": [{"name": "q", "value": "teste"}]},
            "token": "interaction_token_xyz",
        }
        body = json.dumps(interaction).encode()

        with patch("rlm.gateway.discord_gateway._run_rlm_and_followup", new_callable=AsyncMock) as mock_fn:
            resp = discord_client.post(
                "/discord/interactions",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature-Ed25519": "aabbcc",
                    "X-Signature-Timestamp": "12345",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["type"] == 5  # DEFERRED
        assert mock_fn.called

    def test_message_component_returns_deferred(
        self, discord_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RLM_DISCORD_SKIP_VERIFY", "true")
        monkeypatch.delenv("DISCORD_APP_PUBLIC_KEY", raising=False)

        interaction = {
            "type": 3,
            "guild_id": "g1",
            "member": {"user": {"id": "u1", "username": "btn_user"}},
            "data": {"custom_id": "approve_action"},
            "token": "tok_comp",
        }
        body = json.dumps(interaction).encode()

        with patch("rlm.gateway.discord_gateway._run_rlm_and_followup", new_callable=AsyncMock):
            resp = discord_client.post(
                "/discord/interactions",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature-Ed25519": "aabbcc",
                    "X-Signature-Timestamp": "12345",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["type"] == 5

    def test_invalid_json_returns_400(
        self, discord_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RLM_DISCORD_SKIP_VERIFY", "true")
        monkeypatch.delenv("DISCORD_APP_PUBLIC_KEY", raising=False)

        resp = discord_client.post(
            "/discord/interactions",
            content=b"NOT_JSON{{{",
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": "aabbcc",
                "X-Signature-Timestamp": "12345",
            },
        )
        assert resp.status_code == 400

    def test_missing_public_key_and_no_skip_returns_500(
        self, discord_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DISCORD_APP_PUBLIC_KEY", raising=False)
        monkeypatch.setenv("RLM_DISCORD_SKIP_VERIFY", "false")

        body = json.dumps({"type": 1}).encode()
        resp = discord_client.post(
            "/discord/interactions",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": "",
                "X-Signature-Timestamp": "12345",
            },
        )
        assert resp.status_code == 500

    def test_unknown_interaction_type_returns_type_1(
        self, discord_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RLM_DISCORD_SKIP_VERIFY", "true")
        monkeypatch.delenv("DISCORD_APP_PUBLIC_KEY", raising=False)

        body = json.dumps({"type": 99, "guild_id": "g", "member": {"user": {"id": "u"}}, "data": {}}).encode()
        resp = discord_client.post(
            "/discord/interactions",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": "xx",
                "X-Signature-Timestamp": "12345",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["type"] == 1


# ===========================================================================
# 5. WhatsApp Gateway
# ===========================================================================

@pytest.fixture(scope="module")
def wa_client() -> TestClient:
    from rlm.gateway.whatsapp_gateway import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


class TestWhatsAppHubChallenge:
    def test_valid_challenge(
        self, wa_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "meu-token-secreto")
        resp = wa_client.get(
            "/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "meu-token-secreto",
                "hub.challenge": "challenge_abc123",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "challenge_abc123"

    def test_wrong_token_returns_403(
        self, wa_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "correto")
        resp = wa_client.get(
            "/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "errado",
                "hub.challenge": "x",
            },
        )
        assert resp.status_code == 403

    def test_wrong_mode_returns_403(
        self, wa_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "tok")
        resp = wa_client.get(
            "/whatsapp/webhook",
            params={
                "hub.mode": "unsubscribe",  # modo errado
                "hub.verify_token": "tok",
                "hub.challenge": "x",
            },
        )
        assert resp.status_code == 403

    def test_missing_verify_token_env_returns_500(
        self, wa_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)
        resp = wa_client.get(
            "/whatsapp/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "y"},
        )
        assert resp.status_code == 500


class TestWhatsAppInbound:
    def _text_payload(self, wa_id: str = "5511999990000", text: str = "Olá") -> bytes:
        return json.dumps({
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": wa_id,
                            "id": "wamid.TEST123",
                            "type": "text",
                            "text": {"body": text},
                        }],
                        "metadata": {"phone_number_id": "phone123"},
                        "contacts": [{"wa_id": wa_id, "profile": {"name": "Usuário Teste"}}],
                    },
                }],
            }],
        }).encode()

    def test_text_message_returns_200(
        self, wa_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with patch("rlm.gateway.whatsapp_gateway._process_whatsapp_payload", new_callable=AsyncMock):
            resp = wa_client.post(
                "/whatsapp/webhook",
                content=self._text_payload(),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_invalid_json_returns_400(self, wa_client: TestClient) -> None:
        resp = wa_client.post(
            "/whatsapp/webhook",
            content=b"NOT JSON<<<",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_location_message_dispatched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "5511999990000",
                            "id": "wamid.LOC",
                            "type": "location",
                            "location": {"latitude": -23.55, "longitude": -46.63, "name": "SP"},
                        }],
                        "metadata": {},
                        "contacts": [],
                    },
                }],
            }],
        }
        dispatched: list[tuple] = []

        async def fake_dispatch(client_id: str, data: dict) -> None:
            dispatched.append((client_id, data))

        with patch("rlm.gateway.whatsapp_gateway._dispatch_to_rlm", fake_dispatch):
            with patch("rlm.gateway.whatsapp_gateway._mark_read_async", AsyncMock()):
                asyncio.run(
                    __import__(
                        "rlm.gateway.whatsapp_gateway",
                        fromlist=["_process_whatsapp_payload"],
                    )._process_whatsapp_payload(None, payload)
                )

        assert len(dispatched) == 1
        assert "-23.55" in dispatched[0][1]["text"]

    def test_interactive_button_reply_dispatched(self) -> None:
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "5511111111",
                            "id": "wamid.BTN",
                            "type": "interactive",
                            "interactive": {
                                "type": "button_reply",
                                "button_reply": {"id": "btn_ok", "title": "Confirmar"},
                            },
                        }],
                        "metadata": {},
                        "contacts": [],
                    },
                }],
            }],
        }
        dispatched: list[tuple] = []

        async def fake_dispatch(client_id: str, data: dict) -> None:
            dispatched.append((client_id, data))

        with patch("rlm.gateway.whatsapp_gateway._dispatch_to_rlm", fake_dispatch):
            with patch("rlm.gateway.whatsapp_gateway._mark_read_async", AsyncMock()):
                asyncio.run(
                    __import__(
                        "rlm.gateway.whatsapp_gateway",
                        fromlist=["_process_whatsapp_payload"],
                    )._process_whatsapp_payload(None, payload)
                )

        assert len(dispatched) == 1
        assert "Confirmar" in dispatched[0][1]["text"]

    def test_reaction_not_dispatched(self) -> None:
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "5511111111",
                            "id": "wamid.REA",
                            "type": "reaction",
                            "reaction": {"message_id": "wamid.X", "emoji": "👍"},
                        }],
                        "metadata": {},
                        "contacts": [],
                    },
                }],
            }],
        }
        dispatched: list[tuple] = []

        async def fake_dispatch(client_id: str, data: dict) -> None:
            dispatched.append((client_id, data))

        with patch("rlm.gateway.whatsapp_gateway._dispatch_to_rlm", fake_dispatch):
            asyncio.run(
                __import__(
                    "rlm.gateway.whatsapp_gateway",
                    fromlist=["_process_whatsapp_payload"],
                )._process_whatsapp_payload(None, payload)
            )

        # Reaction não deve ser despachado ao RLM
        assert len(dispatched) == 0

    def test_image_message_dispatched_with_media_hint(self) -> None:
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "5511111111",
                            "id": "wamid.IMG",
                            "type": "image",
                            "image": {"id": "media_img_id", "mime_type": "image/jpeg", "caption": "Foto aqui"},
                        }],
                        "metadata": {},
                        "contacts": [],
                    },
                }],
            }],
        }
        dispatched: list[tuple] = []

        async def fake_dispatch(client_id: str, data: dict) -> None:
            dispatched.append((client_id, data))

        with patch("rlm.gateway.whatsapp_gateway._dispatch_to_rlm", fake_dispatch):
            with patch("rlm.gateway.whatsapp_gateway._mark_read_async", AsyncMock()):
                asyncio.run(
                    __import__(
                        "rlm.gateway.whatsapp_gateway",
                        fromlist=["_process_whatsapp_payload"],
                    )._process_whatsapp_payload(None, payload)
                )

        assert len(dispatched) == 1
        text = dispatched[0][1]["text"]
        assert "media_img_id" in text
        assert "get_media_url" in text

    def test_mark_as_read_called_for_text(self) -> None:
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "5511111111",
                            "id": "wamid.TXT",
                            "type": "text",
                            "text": {"body": "Oi"},
                        }],
                        "metadata": {},
                        "contacts": [],
                    },
                }],
            }],
        }
        mark_called: list[str] = []

        async def fake_mark_read(msg_id: str) -> None:
            mark_called.append(msg_id)

        async def fake_dispatch(client_id: str, data: dict) -> None:
            pass

        with patch("rlm.gateway.whatsapp_gateway._dispatch_to_rlm", fake_dispatch):
            with patch("rlm.gateway.whatsapp_gateway._mark_read_async", fake_mark_read):
                asyncio.run(
                    __import__(
                        "rlm.gateway.whatsapp_gateway",
                        fromlist=["_process_whatsapp_payload"],
                    )._process_whatsapp_payload(None, payload)
                )

        assert "wamid.TXT" in mark_called


# ===========================================================================
# 6. Slack Gateway
# ===========================================================================

@pytest.fixture(scope="module")
def slack_client() -> TestClient:
    from rlm.gateway.slack_gateway import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


class TestVerifySlackSignature:
    _SECRET = "slack_test_secret"

    def _valid_headers(self, body: bytes) -> tuple[str, str, str]:
        ts = str(int(time.time()))
        base = f"v0:{ts}:".encode() + body
        sig = "v0=" + hmac.new(self._SECRET.encode(), base, hashlib.sha256).hexdigest()
        return ts, sig, self._SECRET

    def test_valid_signature(self) -> None:
        from rlm.gateway.slack_gateway import _verify_slack_signature
        body = b'{"type":"event_callback"}'
        ts, sig, secret = self._valid_headers(body)
        assert _verify_slack_signature(secret, ts, body, sig) is True

    def test_invalid_signature_raises(self) -> None:
        from rlm.gateway.slack_gateway import _verify_slack_signature
        body = b'{"type":"event_callback"}'
        ts = str(int(time.time()))
        with pytest.raises(ValueError, match="inválida"):
            _verify_slack_signature(self._SECRET, ts, body, "v0=deadbeef")

    def test_expired_timestamp_raises(self) -> None:
        from rlm.gateway.slack_gateway import _verify_slack_signature
        body = b'body'
        old_ts = str(int(time.time()) - 400)  # 400s ago > 300s tolerance
        with pytest.raises(ValueError, match="expirado|replay"):
            _verify_slack_signature(self._SECRET, old_ts, body, "v0=any")

    def test_invalid_timestamp_format_raises(self) -> None:
        from rlm.gateway.slack_gateway import _verify_slack_signature
        with pytest.raises(ValueError, match="inválido"):
            _verify_slack_signature(self._SECRET, "not-a-number", b"body", "v0=x")


class TestSlackEventsEndpoint:
    _SECRET = "slack_signing_secret_test"

    def _headers(self, body: bytes, secret: str | None = None) -> dict:
        return _slack_hmac_headers(secret or self._SECRET, body)

    def test_url_verification_returns_challenge(
        self, slack_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # url_verification deve ser respondido ANTES de verificar assinatura
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        payload = {"type": "url_verification", "challenge": "testchallenge_xyz"}
        body = json.dumps(payload).encode()
        resp = slack_client.post(
            "/slack/events",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["challenge"] == "testchallenge_xyz"

    def test_app_mention_dispatched(
        self, slack_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self._SECRET)
        monkeypatch.delenv("SLACK_APP_ID", raising=False)

        payload = {
            "type": "event_callback",
            "team_id": "T12345",
            "event": {
                "type": "app_mention",
                "user": "U99999",
                "text": "<@UBOT> faça algo",
                "channel": "C11111",
                "ts": "1700000000.000001",
            },
        }
        body = json.dumps(payload).encode()
        headers = self._headers(body)
        headers["Content-Type"] = "application/json"

        with patch("rlm.gateway.slack_gateway._process_slack_event", new_callable=AsyncMock) as mock_fn:
            resp = slack_client.post("/slack/events", content=body, headers=headers)

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert mock_fn.called

    def test_bot_message_not_dispatched(
        self, slack_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self._SECRET)

        payload = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "bot_id": "BBOT123",  # é um bot → deve ser filtrado
                "text": "Mensagem de bot",
                "channel": "C111",
                "ts": "1700000000.000002",
            },
        }
        body = json.dumps(payload).encode()
        headers = self._headers(body)
        headers["Content-Type"] = "application/json"

        with patch("rlm.gateway.slack_gateway._dispatch_to_rlm", new_callable=AsyncMock) as mock_dispatch:
            resp = slack_client.post("/slack/events", content=body, headers=headers)

        assert resp.status_code == 200
        assert not mock_dispatch.called  # bot messages must NOT be dispatched

    def test_invalid_signature_returns_401(
        self, slack_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self._SECRET)
        payload = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {"type": "app_mention", "user": "U1", "text": "oi", "channel": "C1"},
        }
        body = json.dumps(payload).encode()
        # Headers com assinatura errada
        bad_headers = {
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=invalida",
            "Content-Type": "application/json",
        }
        resp = slack_client.post("/slack/events", content=body, headers=bad_headers)
        assert resp.status_code == 401

    def test_message_subtype_ignored(self) -> None:
        """Mensagens com subtype (edit, delete) não devem ser despachadas."""
        import asyncio as aio
        payload_base = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "subtype": "message_changed",
                "user": "U1",
                "text": "editado",
                "channel": "C1",
            },
        }
        dispatched: list = []

        async def fake_dispatch(cid: str, data: dict) -> None:
            dispatched.append(cid)

        with patch("rlm.gateway.slack_gateway._dispatch_to_rlm", fake_dispatch):
            aio.run(
                __import__(
                    "rlm.gateway.slack_gateway",
                    fromlist=["_process_slack_event"],
                )._process_slack_event(None, payload_base, "")
            )

        assert len(dispatched) == 0

    def test_mention_stripped_from_text(self) -> None:
        """O @menção deve ser removido do texto antes de enviar ao RLM."""
        import asyncio as aio
        payload = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "<@UBOT123> resumir o relatório",
                "channel": "C1",
                "ts": "1700000000.000005",
            },
        }
        captured: list[dict] = []

        async def fake_dispatch(cid: str, data: dict) -> None:
            captured.append(data)

        with patch("rlm.gateway.slack_gateway._dispatch_to_rlm", fake_dispatch):
            aio.run(
                __import__(
                    "rlm.gateway.slack_gateway",
                    fromlist=["_process_slack_event"],
                )._process_slack_event(None, payload, "")
            )

        assert len(captured) == 1
        assert "<@" not in captured[0]["text"]
        assert "resumir o relatório" in captured[0]["text"]

    def test_empty_text_after_mention_strip_not_dispatched(self) -> None:
        """Texto vazio após remover menção não deve ser despachado."""
        import asyncio as aio
        payload = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "<@UBOT123>",  # apenas menção, sem conteúdo
                "channel": "C1",
            },
        }
        dispatched: list = []

        async def fake_dispatch(cid: str, data: dict) -> None:
            dispatched.append(cid)

        with patch("rlm.gateway.slack_gateway._dispatch_to_rlm", fake_dispatch):
            aio.run(
                __import__(
                    "rlm.gateway.slack_gateway",
                    fromlist=["_process_slack_event"],
                )._process_slack_event(None, payload, "")
            )

        assert len(dispatched) == 0

    def test_client_id_format(self) -> None:
        """client_id deve ser 'slack:{team_id}:{channel}'."""
        import asyncio as aio
        payload = {
            "type": "event_callback",
            "team_id": "TTEAM123",
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "deploy",
                "channel": "CCHAN456",
                "ts": "1700000000.000010",
            },
        }
        captured: list[tuple] = []

        async def fake_dispatch(cid: str, data: dict) -> None:
            captured.append((cid, data))

        with patch("rlm.gateway.slack_gateway._dispatch_to_rlm", fake_dispatch):
            aio.run(
                __import__(
                    "rlm.gateway.slack_gateway",
                    fromlist=["_process_slack_event"],
                )._process_slack_event(None, payload, "")
            )

        assert len(captured) == 1
        assert captured[0][0] == "slack:TTEAM123:CCHAN456"

    def test_no_signing_secret_proceeds_with_warning(
        self, slack_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sem SLACK_SIGNING_SECRET, o endpoint aceita o request com warning."""
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        payload = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "oi",
                "channel": "C1",
                "ts": "1700000000.000099",
            },
        }
        body = json.dumps(payload).encode()
        with patch("rlm.gateway.slack_gateway._process_slack_event", new_callable=AsyncMock):
            resp = slack_client.post(
                "/slack/events",
                content=body,
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200


# ===========================================================================
# 7. WebChat
# ===========================================================================

@pytest.fixture(scope="module")
def webchat_client() -> TestClient:
    from rlm.gateway.webchat import router

    class _DummyEnv:
        def __init__(self) -> None:
            self._next_command_id = 1
            self.saved_paths: list[str] = []
            self._snapshot: dict[str, Any] = {
                "tasks": {
                    "current": {"task_id": 7, "title": "Analisar sessão", "status": "in-progress", "note": "inspecionando runtime"},
                    "items": [{"task_id": 7, "title": "Analisar sessão", "status": "in-progress", "note": "inspecionando runtime"}],
                },
                "attachments": {"items": []},
                "timeline": {"entries": []},
                "recursive_session": {
                    "state": {
                        "message_count": 2,
                        "event_count": 2,
                        "queued_commands": 0,
                        "latest_event": {"event_type": "assistant_message_emitted"},
                    },
                    "messages": [
                        {"message_id": 1, "role": "user", "content": "oi", "timestamp": "2026-03-20T00:00:00+00:00"},
                        {"message_id": 2, "role": "assistant", "content": "olá", "timestamp": "2026-03-20T00:00:01+00:00"},
                    ],
                    "commands": [],
                    "events": [
                        {"event_id": 1, "event_type": "user_message_received", "source": "user", "payload": {"role": "user"}, "timestamp": "2026-03-20T00:00:00+00:00"},
                        {"event_id": 2, "event_type": "assistant_message_emitted", "source": "assistant", "payload": {"role": "assistant"}, "timestamp": "2026-03-20T00:00:01+00:00"},
                    ],
                },
                "coordination": {
                    "events": [
                        {"event_id": 1, "operation": "fanout", "topic": "riemann", "sender_id": 0, "receiver_id": 1, "payload_preview": "branch 1 started", "metadata": {}, "timestamp": "2026-03-20T00:00:01+00:00"},
                        {"event_id": 2, "operation": "consensus", "topic": "riemann", "sender_id": 2, "receiver_id": 0, "payload_preview": "branch 2 won", "metadata": {}, "timestamp": "2026-03-20T00:00:02+00:00"},
                    ],
                    "branch_tasks": [
                        {"branch_id": 1, "task_id": 101, "mode": "explore", "title": "Branch 1", "parent_task_id": 7, "status": "completed", "metadata": {"role": "analysis"}, "created_at": "2026-03-20T00:00:00+00:00", "updated_at": "2026-03-20T00:00:03+00:00"},
                        {"branch_id": 2, "task_id": 102, "mode": "implement", "title": "Branch 2", "parent_task_id": 7, "status": "completed", "metadata": {"role": "implementation"}, "created_at": "2026-03-20T00:00:00+00:00", "updated_at": "2026-03-20T00:00:03+00:00"},
                    ],
                    "latest_parallel_summary": {
                        "winner_branch_id": 2,
                        "cancelled_count": 0,
                        "failed_count": 0,
                        "total_tasks": 2,
                        "task_ids_by_branch": {"1": 101, "2": 102},
                        "strategy": {"mode": "parallel"},
                        "stop_evaluation": {"reason": "winner-chosen"},
                    },
                },
                "controls": {
                    "paused": False,
                    "pause_reason": "",
                    "focused_branch_id": None,
                    "fixed_winner_branch_id": None,
                    "branch_priorities": {},
                    "last_checkpoint_path": None,
                    "last_checkpoint_at": None,
                    "last_operator_note": "",
                },
                "strategy": {"active_recursive_strategy": None},
            }

        def get_runtime_state_snapshot(self) -> dict[str, Any]:
            return json.loads(json.dumps(self._snapshot))

        def queue_recursive_command(
            self,
            command_type: str,
            *,
            payload: dict[str, Any] | None = None,
            status: str = "queued",
            branch_id: int | None = None,
        ) -> dict[str, Any]:
            entry = {
                "command_id": self._next_command_id,
                "command_type": command_type,
                "payload": dict(payload or {}),
                "status": status,
                "branch_id": branch_id,
                "outcome": {},
                "timestamp": "2026-03-20T00:00:04+00:00",
                "updated_at": "2026-03-20T00:00:04+00:00",
            }
            self._next_command_id += 1
            self._snapshot["recursive_session"]["commands"].append(entry)
            self._snapshot["recursive_session"]["state"]["queued_commands"] += 1
            self._snapshot["recursive_session"]["events"].append({
                "event_id": len(self._snapshot["recursive_session"]["events"]) + 1,
                "event_type": "command_queued",
                "source": "control",
                "payload": {"command_type": command_type},
                "timestamp": "2026-03-20T00:00:04+00:00",
            })
            self._snapshot["recursive_session"]["state"]["event_count"] += 1
            self._snapshot["recursive_session"]["state"]["latest_event"] = {"event_type": "command_queued"}
            return dict(entry)

        def update_recursive_command(
            self,
            command_id: int,
            *,
            status: str,
            outcome: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            for entry in self._snapshot["recursive_session"]["commands"]:
                if entry["command_id"] == command_id:
                    entry["status"] = status
                    entry["outcome"] = dict(outcome or {})
                    entry["updated_at"] = "2026-03-20T00:00:05+00:00"
                    self._snapshot["recursive_session"]["state"]["queued_commands"] = max(
                        0,
                        self._snapshot["recursive_session"]["state"]["queued_commands"] - 1,
                    )
                    return dict(entry)
            raise KeyError(command_id)

        def set_runtime_paused(self, paused: bool, *, reason: str = "", origin: str = "operator") -> dict[str, Any]:
            self._snapshot["controls"]["paused"] = paused
            self._snapshot["controls"]["pause_reason"] = reason
            self._snapshot["controls"]["last_operator_note"] = reason
            return self._snapshot["controls"]

        def set_runtime_focus(self, branch_id: int, *, fixed: bool = False, reason: str = "", origin: str = "operator") -> dict[str, Any]:
            self._snapshot["controls"]["focused_branch_id"] = branch_id
            if fixed:
                self._snapshot["controls"]["fixed_winner_branch_id"] = branch_id
                self._snapshot["coordination"]["latest_parallel_summary"]["winner_branch_id"] = branch_id
            for item in self._snapshot["coordination"]["branch_tasks"]:
                item.setdefault("metadata", {})["operator_focus"] = item["branch_id"] == branch_id
                item["metadata"]["operator_fixed_winner"] = fixed and item["branch_id"] == branch_id
            return self._snapshot["controls"]

        def reprioritize_branch(self, branch_id: int, priority: int, *, reason: str = "", origin: str = "operator") -> dict[str, Any]:
            self._snapshot["controls"]["branch_priorities"][str(branch_id)] = priority
            for item in self._snapshot["coordination"]["branch_tasks"]:
                if item["branch_id"] == branch_id:
                    item.setdefault("metadata", {})["operator_priority"] = priority
            return self._snapshot["controls"]

        def record_operator_note(self, note: str, *, branch_id: int | None = None, origin: str = "operator") -> dict[str, Any]:
            self._snapshot["controls"]["last_operator_note"] = note
            return self._snapshot["controls"]

        def mark_runtime_checkpoint(self, checkpoint_path: str, *, origin: str = "operator") -> dict[str, Any]:
            self._snapshot["controls"]["last_checkpoint_path"] = checkpoint_path
            self._snapshot["controls"]["last_checkpoint_at"] = "2026-03-20T00:00:06+00:00"
            return self._snapshot["controls"]

    class _DummySessionManager:
        def __init__(self) -> None:
            self._sessions: dict[str, Any] = {}
            self._events: dict[str, list[dict[str, Any]]] = {}

        def get_or_create(self, client_id: str) -> Any:
            browser_id = client_id.split(":", 1)[1]
            runtime_id = f"runtime-{browser_id}"
            session = self._sessions.get(runtime_id)
            if session is None:
                session = SimpleNamespace(
                    session_id=runtime_id,
                    client_id=client_id,
                    status="idle",
                    created_at="2026-03-20T00:00:00+00:00",
                    last_active="2026-03-20T00:00:00+00:00",
                    state_dir=os.path.join(os.getcwd(), runtime_id),
                    total_completions=0,
                    total_tokens_used=0,
                    last_error="",
                    metadata={},
                    rlm_instance=SimpleNamespace(
                        _persistent_env=_DummyEnv(),
                        save_state=lambda path: f"State saved to {path}",
                    ),
                )
                self._sessions[runtime_id] = session
                self._events[runtime_id] = []
            return session

        def get_session(self, session_id: str) -> Any | None:
            return self._sessions.get(session_id)

        def session_to_dict(self, session: Any) -> dict[str, Any]:
            return {
                "session_id": session.session_id,
                "client_id": session.client_id,
                "status": session.status,
                "created_at": session.created_at,
                "last_active": session.last_active,
                "total_completions": session.total_completions,
                "total_tokens_used": session.total_tokens_used,
                "last_error": session.last_error,
                "metadata": session.metadata,
                "has_rlm_instance": True,
            }

        def log_event(self, session_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
            self._events.setdefault(session_id, []).append({
                "timestamp": "2026-03-20T00:00:02+00:00",
                "event_type": event_type,
                "payload": payload or {},
            })

        def update_session(self, session: Any) -> None:
            self._sessions[session.session_id] = session

        def get_events(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
            items = list(self._events.get(session_id, []))
            if limit > 0:
                items = items[-limit:]
            return list(reversed(items))

    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = _DummySessionManager()
    app.state.supervisor = SimpleNamespace(abort=lambda session_id, reason="": True)
    return TestClient(app, raise_server_exceptions=True)


class TestWebChatHealth:
    def test_health_returns_ok(self, webchat_client: TestClient) -> None:
        resp = webchat_client.get("/webchat/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["webchat"] is True
        assert "timestamp" in data

    def test_health_timestamp_is_current(self, webchat_client: TestClient) -> None:
        before = int(time.time()) - 2
        resp = webchat_client.get("/webchat/health")
        after = int(time.time()) + 2
        ts = resp.json()["timestamp"]
        assert before <= ts <= after


class TestWebChatMessage:
    def test_empty_text_returns_400(self, webchat_client: TestClient) -> None:
        resp = webchat_client.post(
            "/webchat/message",
            json={"text": "", "session_id": "s1"},
        )
        assert resp.status_code == 400

    def test_whitespace_text_returns_400(self, webchat_client: TestClient) -> None:
        resp = webchat_client.post(
            "/webchat/message",
            json={"text": "   ", "session_id": "s1"},
        )
        assert resp.status_code == 400

    def test_disabled_returns_503(
        self, webchat_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RLM_WEBCHAT_DISABLED", "true")
        resp = webchat_client.post(
            "/webchat/message",
            json={"text": "Oi", "session_id": "s1"},
        )
        assert resp.status_code == 503
        monkeypatch.delenv("RLM_WEBCHAT_DISABLED")

    def test_valid_message_returns_runtime_activity_contract(
        self, webchat_client: TestClient
    ) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            resp = webchat_client.post(
                "/webchat/message",
                json={"text": "Qual é a capital do Brasil?", "session_id": "sess_abc"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess_abc"
        assert data["runtime_session_id"] == "runtime-sess_abc"
        assert data["activity_url"] == "/webchat/session/runtime-sess_abc/activity"
        assert "stream_url" not in data
        assert "result_key" not in data

    def test_generates_session_id_if_missing(self, webchat_client: TestClient) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            resp = webchat_client.post(
                "/webchat/message",
                json={"text": "Olá!"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["session_id"]) == 16  # secrets.token_hex(8) = 16 chars hex

    def test_runtime_session_id_matches_browser_session(self, webchat_client: TestClient) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            resp = webchat_client.post(
                "/webchat/message",
                json={"text": "Teste", "session_id": "my_session"},
            )
        data = resp.json()
        assert data["runtime_session_id"] == "runtime-my_session"


class TestWebChatActivity:
    def test_activity_returns_runtime_snapshot(self, webchat_client: TestClient) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            webchat_client.post(
                "/webchat/message",
                json={"text": "Teste", "session_id": "sess_runtime"},
            )

        resp = webchat_client.get("/webchat/session/runtime-sess_runtime/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session"]["session_id"] == "runtime-sess_runtime"
        assert data["runtime"]["recursive_session"]["messages"][1]["content"] == "olá"
        assert data["runtime"]["coordination"]["latest_parallel_summary"]["winner_branch_id"] == 2
        assert len(data["runtime"]["coordination"]["branch_tasks"]) == 2
        assert data["runtime"]["controls"]["paused"] is False
        assert data["event_log"][-1]["event_type"] == "webchat_message_enqueued"


class TestWebChatCommands:
    def test_command_endpoint_executes_focus_branch(self, webchat_client: TestClient) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            webchat_client.post(
                "/webchat/message",
                json={"text": "Teste", "session_id": "sess_cmd"},
            )

        resp = webchat_client.post(
            "/webchat/session/runtime-sess_cmd/commands",
            json={
                "command_type": "focus_branch",
                "payload": {"note": "olhar branch 2"},
                "branch_id": 2,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["command"]["command_type"] == "focus_branch"
        assert data["command"]["branch_id"] == 2
        assert data["command"]["status"] == "completed"
        assert data["runtime"]["controls"]["focused_branch_id"] == 2
        assert data["runtime"]["recursive_session"]["commands"][-1]["command_type"] == "focus_branch"

    def test_command_endpoint_executes_pause_runtime(self, webchat_client: TestClient) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            webchat_client.post(
                "/webchat/message",
                json={"text": "Teste", "session_id": "sess_pause"},
            )

        resp = webchat_client.post(
            "/webchat/session/runtime-sess_pause/commands",
            json={
                "command_type": "pause_runtime",
                "payload": {"reason": "congelar execucao"},
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["command"]["status"] == "completed"
        assert data["runtime"]["controls"]["paused"] is True
        assert data["runtime"]["controls"]["pause_reason"] == "congelar execucao"

    def test_command_endpoint_executes_checkpoint(self, webchat_client: TestClient) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            webchat_client.post(
                "/webchat/message",
                json={"text": "Teste", "session_id": "sess_checkpoint"},
            )

        resp = webchat_client.post(
            "/webchat/session/runtime-sess_checkpoint/commands",
            json={
                "command_type": "create_checkpoint",
                "payload": {"checkpoint_name": "snapshot-a"},
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["command"]["status"] == "completed"
        assert "operator_checkpoints" in data["command"]["outcome"]["checkpoint_path"]
        assert data["runtime"]["controls"]["last_checkpoint_path"].endswith("snapshot-a")

    def test_command_endpoint_requires_branch_for_reprioritize(self, webchat_client: TestClient) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            webchat_client.post(
                "/webchat/message",
                json={"text": "Teste", "session_id": "sess_reprio"},
            )

        resp = webchat_client.post(
            "/webchat/session/runtime-sess_reprio/commands",
            json={
                "command_type": "reprioritize_branch",
                "payload": {"priority": 9},
            },
        )
        assert resp.status_code == 400

    def test_command_endpoint_rejects_unknown_command_type(self, webchat_client: TestClient) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            webchat_client.post(
                "/webchat/message",
                json={"text": "Teste", "session_id": "sess_unknown_cmd"},
            )

        resp = webchat_client.post(
            "/webchat/session/runtime-sess_unknown_cmd/commands",
            json={
                "command_type": "unknown_control",
                "payload": {"note": "nao existe"},
            },
        )

        assert resp.status_code == 400
        assert "command_type sem executor dedicado" in resp.json()["detail"]

        activity = webchat_client.get("/webchat/session/runtime-sess_unknown_cmd/activity")
        assert activity.status_code == 200
        command = activity.json()["runtime"]["recursive_session"]["commands"][-1]
        assert command["command_type"] == "unknown_control"
        assert command["status"] == "failed"
        assert "command_type sem executor dedicado" in command["outcome"]["error"]

    def test_command_endpoint_rejects_empty_type(self, webchat_client: TestClient) -> None:
        with patch("rlm.gateway.webchat._dispatch_to_rlm", new_callable=AsyncMock):
            webchat_client.post(
                "/webchat/message",
                json={"text": "Teste", "session_id": "sess_cmd_empty"},
            )

        resp = webchat_client.post(
            "/webchat/session/runtime-sess_cmd_empty/commands",
            json={"command_type": "   "},
        )
        assert resp.status_code == 400


class TestWebChatUI:
    def test_html_not_found_returns_404(self, webchat_client: TestClient, tmp_path) -> None:
        """Se webchat.html não existe, retorna 404."""
        import rlm.gateway.webchat as wc_module

        original_static = wc_module._STATIC_DIR
        try:
            wc_module._STATIC_DIR = tmp_path / "nonexistent_static"
            resp = webchat_client.get("/webchat/")
            assert resp.status_code == 404
        finally:
            wc_module._STATIC_DIR = original_static


# ===========================================================================
# 8. CLI — Novos subcomandos
# ===========================================================================

class TestCLINewParsers:
    def test_doctor_parser_exists(self) -> None:
        from rlm.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"

    def test_skill_list_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        args = build_parser().parse_args(["skill", "list"])
        assert args.command == "skill"
        assert args.skill_command == "list"

    def test_skill_install_source_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        args = build_parser().parse_args(["skill", "install", "github:usuario/repo@main"])
        assert args.skill_command == "install"
        assert args.source == "github:usuario/repo@main"
        assert args.force is False

    def test_skill_install_force_flag(self) -> None:
        from rlm.cli.parser import build_parser
        args = build_parser().parse_args(["skill", "install", "github:usuario/repo", "--force"])
        assert args.force is True

    def test_channel_list_parsed(self) -> None:
        from rlm.cli.parser import build_parser
        args = build_parser().parse_args(["channel", "list"])
        assert args.command == "channel"
        assert args.channel_command == "list"

    def test_doctor_in_dispatch(self) -> None:
        from rlm.cli.main import DISPATCH
        handler = DISPATCH.get("doctor")
        assert handler is not None
        assert callable(handler)
        # Lazy proxy: qualname aponta para o handler real
        assert "cmd_doctor" in getattr(handler, "__qualname__", "")

    def test_skill_subcommand_without_action_shows_help(self) -> None:
        from rlm.cli.main import main
        # "skill" sem subcomando deve mostrar help (e não crashar com erro)
        try:
            main(["skill"])
        except SystemExit as e:
            assert e.code in (0, None)

    def test_channel_subcommand_without_action_shows_help(self) -> None:
        from rlm.cli.main import main
        try:
            main(["channel"])
        except SystemExit as e:
            assert e.code in (0, None)


class TestCmdChannelList:
    def test_runs_without_env_vars(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import argparse
        from rlm.cli.commands.channel import cmd_channel_list

        for var in ["TELEGRAM_TOKEN", "DISCORD_WEBHOOK_URL", "DISCORD_BOT_TOKEN",
                    "WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "SLACK_BOT_TOKEN",
                    "SLACK_WEBHOOK_URL"]:
            monkeypatch.delenv(var, raising=False)

        rc = cmd_channel_list(argparse.Namespace())
        assert rc == 0

    def test_shows_all_channels(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import argparse
        from rlm.cli.commands.channel import cmd_channel_list

        for var in ["TELEGRAM_TOKEN", "DISCORD_WEBHOOK_URL", "DISCORD_BOT_TOKEN",
                    "WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "SLACK_BOT_TOKEN"]:
            monkeypatch.delenv(var, raising=False)

        cmd_channel_list(argparse.Namespace())
        out = capsys.readouterr().out
        # Todos os 5 canais devem aparecer na saída
        assert "Telegram" in out
        assert "Discord" in out
        assert "WhatsApp" in out
        assert "Slack" in out
        assert "WebChat" in out


class TestCmdSkillList:
    def test_empty_skills_dir(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import argparse
        from rlm.cli.commands.skill import cmd_skill_list

        monkeypatch.chdir(tmp_path)
        skills_dir = tmp_path / "rlm" / "skills"
        skills_dir.mkdir(parents=True)

        rc = cmd_skill_list(argparse.Namespace())
        assert rc == 0

    def test_skill_with_frontmatter_listed(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import argparse
        from rlm.cli.commands.skill import cmd_skill_list

        skill_dir = tmp_path / "rlm" / "skills" / "minha-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            '+++\nname = "minha-skill"\nversion = "0.2.0"\ndescription = "Skill de teste"\n+++\n'
        )
        monkeypatch.setenv("RLM_SKILLS_DIR", str(tmp_path / "rlm" / "skills"))

        cmd_skill_list(argparse.Namespace())
        out = capsys.readouterr().out
        assert "minha-skill" in out


class TestCmdSkillInstall:
    def test_invalid_source_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import argparse
        from rlm.cli.commands.skill import cmd_skill_install

        args = argparse.Namespace(source="nao_e_github_nem_url", force=False)
        rc = cmd_skill_install(args)
        assert rc == 1

    def test_github_source_fetched(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import argparse
        from rlm.cli.commands.skill import cmd_skill_install

        (tmp_path / "rlm" / "skills").mkdir(parents=True)
        monkeypatch.setenv("RLM_SKILLS_DIR", str(tmp_path / "rlm" / "skills"))

        skill_content = b'+++\nname = "testskill"\nversion = "1.0.0"\ndescription = "Teste"\n+++\n# Skill\n'

        fake_resp = _MockResponse(skill_content)
        with patch("urllib.request.urlopen", _mock_urlopen(fake_resp)):
            args = argparse.Namespace(source="github:usuario/testskill", force=False)
            rc = cmd_skill_install(args)

        # Skill instalada com sucesso
        assert rc == 0
        skill_file = tmp_path / "rlm" / "skills" / "testskill" / "SKILL.md"
        assert skill_file.exists()

    def test_force_overwrites_existing(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import argparse
        from rlm.cli.commands.skill import cmd_skill_install

        skill_dir = tmp_path / "rlm" / "skills" / "overskill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("versão antiga")
        monkeypatch.setenv("RLM_SKILLS_DIR", str(tmp_path / "rlm" / "skills"))

        skill_content = b'+++\nname = "overskill"\nversion = "2.0.0"\ndescription = "Nova"\n+++\n'
        fake_resp = _MockResponse(skill_content)

        with patch("urllib.request.urlopen", _mock_urlopen(fake_resp)):
            args = argparse.Namespace(source="github:user/overskill", force=True)
            rc = cmd_skill_install(args)

        assert rc == 0
        content = (skill_dir / "SKILL.md").read_text()
        assert "2.0.0" in content

    def test_no_force_with_existing_returns_error(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import argparse
        from rlm.cli.commands.skill import cmd_skill_install

        skill_dir = tmp_path / "rlm" / "skills" / "existingskill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("existente")
        monkeypatch.setenv("RLM_SKILLS_DIR", str(tmp_path / "rlm" / "skills"))

        skill_content = b'+++\nname = "existingskill"\nversion = "1.0.0"\ndescription = "D"\n+++\n'
        fake_resp = _MockResponse(skill_content)

        with patch("urllib.request.urlopen", _mock_urlopen(fake_resp)):
            args = argparse.Namespace(source="github:user/existingskill", force=False)
            rc = cmd_skill_install(args)

        assert rc == 1  # deve falhar sem --force
