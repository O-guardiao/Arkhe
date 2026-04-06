"""Especificações e testes de canais de comunicação (Telegram, Discord, etc.)."""

from __future__ import annotations

from typing import Any

# Especificação de cada canal: (env_var, label, é_obrigatório, hint_setup)
_CHANNEL_SPECS: list[dict[str, Any]] = [
    {
        "name": "Telegram",
        "id": "telegram",
        "vars": [
            ("TELEGRAM_BOT_TOKEN", "Bot Token (do @BotFather)", True),
            ("TELEGRAM_OWNER_CHAT_ID", "Chat ID do dono (para notificações)", False),
        ],
        "hint": "Converse com @BotFather → /newbot → copie o token",
        "test_fn": "_test_telegram_token",
    },
    {
        "name": "Discord",
        "id": "discord",
        "vars": [
            ("DISCORD_BOT_TOKEN", "Bot Token", False),
            ("DISCORD_APP_PUBLIC_KEY", "Public Key (Ed25519)", True),
            ("DISCORD_APP_ID", "Application ID", True),
        ],
        "hint": "Discord Developer Portal → Applications → Bot → Token",
        "test_fn": "_test_discord_token",
    },
    {
        "name": "WhatsApp",
        "id": "whatsapp",
        "vars": [
            ("WHATSAPP_TOKEN", "Access Token (Meta Cloud API)", True),
            ("WHATSAPP_PHONE_ID", "Phone Number ID", True),
            ("WHATSAPP_VERIFY_TOKEN", "Webhook Verify Token (defina você)", True),
        ],
        "hint": "Meta for Developers → Your App → WhatsApp → Configuration",
        "test_fn": None,
    },
    {
        "name": "Slack",
        "id": "slack",
        "vars": [
            ("SLACK_BOT_TOKEN", "Bot User OAuth Token (xoxb-…)", True),
            ("SLACK_SIGNING_SECRET", "Signing Secret", True),
        ],
        "hint": "Slack API → Your App → OAuth & Permissions + Basic Information",
        "test_fn": None,
    },
]


def _test_telegram_token(token: str) -> tuple[bool, str]:
    """Testa token do Telegram via /getMe. Retorna (ok, mensagem)."""
    import json as _json
    from urllib import error as _urlerr
    from urllib import request as _urlreq

    url = f"https://api.telegram.org/bot{token}/getMe"
    req = _urlreq.Request(url, method="GET")
    try:
        with _urlreq.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                bot = data.get("result", {})
                name = bot.get("first_name", "?")
                uname = bot.get("username", "?")
                return True, f"@{uname} ({name})"
            return False, "API retornou ok=false"
    except _urlerr.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


def _test_discord_token(token: str) -> tuple[bool, str]:
    """Testa token do Discord via /users/@me. Retorna (ok, mensagem)."""
    import json as _json
    from urllib import error as _urlerr
    from urllib import request as _urlreq

    url = "https://discord.com/api/v10/users/@me"
    req = _urlreq.Request(url, headers={"Authorization": f"Bot {token}"})
    try:
        with _urlreq.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
            uname = data.get("username", "?")
            return True, f"@{uname}"
    except _urlerr.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)
