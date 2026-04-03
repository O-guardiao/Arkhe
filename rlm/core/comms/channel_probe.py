"""
Channel Probe — verifica identidade e saúde de canais via API nativa.

Inspirado em OpenClaw src/telegram/probe.ts:
    probeTelegram(token, timeoutMs) → /getMe → bot_id, username, capabilities

Cada canal expõe um prober concreto que implementa probe() → ProbeResult.
O probe é executado:
    1. No startup — capturar identidade do bot (username, id)
    2. Periodicamente — health check via ChannelStatusRegistry
    3. Sob demanda — CLI `rlm channels probe`
"""
from __future__ import annotations

import abc
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from rlm.core.structured_log import get_logger

_log = get_logger("channel_probe")

TELEGRAM_API_BASE = "https://api.telegram.org"


@dataclass(frozen=True)
class BotIdentity:
    """Identidade descoberta via probe (imutável)."""
    bot_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProbeResult:
    """Resultado de um probe de canal."""
    ok: bool = False
    elapsed_ms: float = 0.0
    error: str | None = None
    identity: BotIdentity | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ChannelProber(abc.ABC):
    """Interface abstrata para probers de canal."""

    @abc.abstractmethod
    def probe(self, timeout_s: float = 10.0) -> ProbeResult:
        """Executa probe síncrono e retorna resultado."""
        ...

    @property
    @abc.abstractmethod
    def channel_id(self) -> str:
        """Identificador do canal (e.g. 'telegram', 'discord')."""
        ...


class TelegramProber(ChannelProber):
    """
    Probe do Telegram — chama /getMe para obter identidade do bot.

    Padrão OpenClaw: retry loop de 3 tentativas no /getMe,
    seguido de /getWebhookInfo opcional para informação extra.
    """

    def __init__(self, token: str, account_id: str = "default"):
        self._token = token
        self._account_id = account_id

    @property
    def channel_id(self) -> str:
        return "telegram"

    def probe(self, timeout_s: float = 10.0) -> ProbeResult:
        started = time.monotonic()
        base_url = f"{TELEGRAM_API_BASE}/bot{self._token}"
        timeout_ms = int(timeout_s * 1000)

        # Retry loop (OpenClaw pattern: 3 attempts for DNS/startup races)
        me_json: dict | None = None
        last_err: str = ""
        for attempt in range(3):
            try:
                req = urllib_request.Request(f"{base_url}/getMe")
                with urllib_request.urlopen(req, timeout=timeout_s) as resp:
                    me_json = json.loads(resp.read().decode("utf-8"))
                break
            except urllib_error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:200]
                last_err = f"HTTP {e.code}: {body}"
            except Exception as e:
                last_err = str(e)
            if attempt < 2:
                time.sleep(min(1.0, timeout_s / 3))

        elapsed = (time.monotonic() - started) * 1000

        if me_json is None:
            return ProbeResult(ok=False, elapsed_ms=elapsed, error=last_err)

        if not me_json.get("ok"):
            return ProbeResult(
                ok=False,
                elapsed_ms=elapsed,
                error=me_json.get("description", "getMe failed"),
                raw=me_json,
            )

        result_data = me_json.get("result", {})
        identity = BotIdentity(
            bot_id=result_data.get("id"),
            username=result_data.get("username"),
            display_name=result_data.get("first_name"),
            extras={
                "can_join_groups": result_data.get("can_join_groups"),
                "can_read_all_group_messages": result_data.get("can_read_all_group_messages"),
                "supports_inline_queries": result_data.get("supports_inline_queries"),
            },
        )

        # Webhook info (best-effort, não falha o probe)
        webhook_info: dict[str, Any] = {}
        try:
            req = urllib_request.Request(f"{base_url}/getWebhookInfo")
            with urllib_request.urlopen(req, timeout=timeout_s) as resp:
                wh_json = json.loads(resp.read().decode("utf-8"))
            if wh_json.get("ok"):
                wh_result = wh_json.get("result", {})
                webhook_info = {
                    "webhook_url": wh_result.get("url") or None,
                    "has_custom_cert": wh_result.get("has_custom_certificate"),
                    "pending_update_count": wh_result.get("pending_update_count"),
                }
        except Exception:
            pass

        return ProbeResult(
            ok=True,
            elapsed_ms=elapsed,
            identity=identity,
            raw={"bot": result_data, "webhook": webhook_info},
        )


class DiscordProber(ChannelProber):
    """Probe stub para Discord — verifica se token é válido via /users/@me."""

    def __init__(self, bot_token: str):
        self._token = bot_token

    @property
    def channel_id(self) -> str:
        return "discord"

    def probe(self, timeout_s: float = 10.0) -> ProbeResult:
        started = time.monotonic()
        try:
            req = urllib_request.Request(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {self._token}"},
            )
            with urllib_request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            elapsed = (time.monotonic() - started) * 1000
            return ProbeResult(
                ok=True,
                elapsed_ms=elapsed,
                identity=BotIdentity(
                    bot_id=int(data.get("id", 0)),
                    username=data.get("username"),
                    display_name=data.get("global_name") or data.get("username"),
                ),
                raw=data,
            )
        except Exception as e:
            elapsed = (time.monotonic() - started) * 1000
            return ProbeResult(ok=False, elapsed_ms=elapsed, error=str(e))


class NullProber(ChannelProber):
    """Prober passivo — para canais que não suportam probe ativo (webhooks puros)."""

    def __init__(self, channel: str):
        self._channel = channel

    @property
    def channel_id(self) -> str:
        return self._channel

    def probe(self, timeout_s: float = 10.0) -> ProbeResult:
        return ProbeResult(ok=True, elapsed_ms=0.0)
