"""Especificações e testes de canais de comunicação (Telegram, Discord, etc.)."""

from __future__ import annotations

from typing import Any, Callable, TypedDict

ChannelVarSpec = tuple[str, str, bool]
ChannelTestFn = Callable[[str], tuple[bool, str]]
_TEST_REQUEST_TIMEOUT_S = 10


class ChannelSpec(TypedDict):
    name: str
    id: str
    vars: list[ChannelVarSpec]
    hint: str
    test_fn: ChannelTestFn | None
    test_env_var: str | None


def _redact_secret(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "***")


def _http_json_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    redact_secret: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
    import json as _json
    from urllib import error as _urlerr
    from urllib import request as _urlreq

    req = _urlreq.Request(url, headers=headers or {}, method="GET")
    try:
        with _urlreq.urlopen(req, timeout=_TEST_REQUEST_TIMEOUT_S) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
        return payload, None
    except _urlerr.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:
        return None, _redact_secret(str(exc), redact_secret)


def is_sensitive_channel_var(var_name: str) -> bool:
    upper_name = var_name.upper()
    return any(token in upper_name for token in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))


def _test_telegram_token(token: str) -> tuple[bool, str]:
    """Testa token do Telegram via /getMe. Retorna (ok, mensagem)."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    data, error = _http_json_get(url, redact_secret=token)
    if error is not None:
        return False, error
    assert data is not None
    if data.get("ok"):
        bot = data.get("result", {})
        name = bot.get("first_name", "?")
        uname = bot.get("username", "?")
        return True, f"@{uname} ({name})"
    return False, "API retornou ok=false"


def _test_discord_token(token: str) -> tuple[bool, str]:
    """Testa token do Discord via /users/@me. Retorna (ok, mensagem)."""
    url = "https://discord.com/api/v10/users/@me"
    data, error = _http_json_get(
        url,
        headers={"Authorization": f"Bot {token}"},
        redact_secret=token,
    )
    if error is not None:
        return False, error
    assert data is not None
    uname = data.get("username", "?")
    return True, f"@{uname}"


_CHANNEL_SPECS: list[ChannelSpec] = [
    {
        "name": "Telegram",
        "id": "telegram",
        "vars": [
            ("TELEGRAM_BOT_TOKEN", "Bot Token (do @BotFather)", True),
            ("TELEGRAM_OWNER_CHAT_ID", "Chat ID do dono (para notificações)", False),
        ],
        "hint": "Converse com @BotFather -> /newbot -> copie o token",
        "test_fn": _test_telegram_token,
        "test_env_var": "TELEGRAM_BOT_TOKEN",
    },
    {
        "name": "Discord",
        "id": "discord",
        "vars": [
            ("DISCORD_BOT_TOKEN", "Bot Token", False),
            ("DISCORD_APP_PUBLIC_KEY", "Public Key (Ed25519)", True),
            ("DISCORD_APP_ID", "Application ID", True),
        ],
        "hint": "Discord Developer Portal -> Applications -> Bot -> Token",
        "test_fn": _test_discord_token,
        "test_env_var": "DISCORD_BOT_TOKEN",
    },
    {
        "name": "WhatsApp",
        "id": "whatsapp",
        "vars": [
            ("WHATSAPP_TOKEN", "Access Token (Meta Cloud API)", True),
            ("WHATSAPP_PHONE_ID", "Phone Number ID", True),
            ("WHATSAPP_VERIFY_TOKEN", "Webhook Verify Token (defina você)", True),
        ],
        "hint": "Meta for Developers -> Your App -> WhatsApp -> Configuration",
        "test_fn": None,
        "test_env_var": None,
    },
    {
        "name": "Slack",
        "id": "slack",
        "vars": [
            ("SLACK_BOT_TOKEN", "Bot User OAuth Token (xoxb-...)", True),
            ("SLACK_SIGNING_SECRET", "Signing Secret", True),
        ],
        "hint": "Slack API -> Your App -> OAuth & Permissions + Basic Information",
        "test_fn": None,
        "test_env_var": None,
    },
]

CHANNEL_SPECS = _CHANNEL_SPECS
