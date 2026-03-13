"""
Plugin Discord — rlm/plugins/discord.py

Integração com o Discord via Webhook URL e Bot API.

Arquitetura RLM-nativa:
    - Outbound via Webhook URL público (POST JSON, sem auth extra)
    - Outbound via Bot Token para canais específicos
    - DiscordAdapter registrado no ChannelRegistry como prefixo "discord"
    - O REPL chama diretamente: from rlm.plugins.discord import send_webhook

Variáveis de ambiente:
    DISCORD_WEBHOOK_URL   — URL padrão do webhook (canal default)
    DISCORD_BOT_TOKEN     — Token do bot (para enviar em canais por ID)
    DISCORD_APP_PUBLIC_KEY — Chave pública Ed25519 (para verificar interactions)

Uso no REPL:
    >>> from rlm.plugins.discord import send_webhook, send_embed
    >>> send_webhook("Deploy do backend concluído com sucesso!")
    '✓ enviado para discord'
    >>> send_embed("Relatório diário", "- 42 pedidos\\n- R$8.900 faturado", color=0x00FF00)
    '✓ embed enviado'
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    from rlm.plugins import PluginManifest
except ImportError:
    from dataclasses import dataclass as _dc

    @_dc
    class PluginManifest:  # type: ignore
        name: str = ""
        version: str = ""
        description: str = ""
        functions: list = field(default_factory=list)
        author: str = ""
        requires: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Plugin Manifest
# ---------------------------------------------------------------------------

MANIFEST = PluginManifest(
    name="discord",
    version="1.0.0",
    description="Discord — envio via Webhook URL e Bot API (texto, embeds, arquivos).",
    functions=[
        "send_webhook",
        "send_embed",
        "send_channel_message",
        "pin_message",
        "create_thread",
        "get_channel_messages",
    ],
    author="RLM Engine",
    requires=[],  # stdlib urllib apenas
)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _get_webhook_url() -> str:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        raise ValueError(
            "DISCORD_WEBHOOK_URL não configurada. "
            "Defina no .env: DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/..."
        )
    return url


def _get_bot_token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise ValueError(
            "DISCORD_BOT_TOKEN não configurado. "
            "Defina no .env: DISCORD_BOT_TOKEN=Bot seu_token"
        )
    # Discord exige o prefixo "Bot "
    if not token.startswith("Bot "):
        return f"Bot {token}"
    return token


def _discord_request(
    method: str,
    url: str,
    payload: dict | None = None,
    token: str | None = None,
) -> dict:
    """Realiza chamada HTTP à API do Discord (stdlib urllib)."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token

    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib_request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {"ok": True}
    except urllib_error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}: {body[:300]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Funções REPL (chamadas diretamente pelo agente no REPL)
# ---------------------------------------------------------------------------

def send_webhook(
    text: str,
    webhook_url: str | None = None,
    username: str | None = None,
    avatar_url: str | None = None,
) -> str:
    """
    Envia mensagem de texto para um canal Discord via Webhook.

    O Webhook URL não precisa de autenticação — é suficiente para notificações.

    Args:
        text: Conteúdo da mensagem (até 2000 caracteres).
        webhook_url: URL do webhook. None = usa DISCORD_WEBHOOK_URL do ambiente.
        username: Sobrescreve o nome do bot exibido na mensagem.
        avatar_url: URL do avatar exibido (opcional).

    Returns:
        '✓ enviado para discord' em sucesso, string de erro em falha.
    """
    url = webhook_url or _get_webhook_url()
    payload: dict = {"content": text[:2000]}
    if username:
        payload["username"] = username
    if avatar_url:
        payload["avatar_url"] = avatar_url

    result = _discord_request("POST", url, payload)
    if result.get("ok") is False:
        return f"Erro Discord: {result.get('error', 'desconhecido')}"
    return "✓ enviado para discord"


def send_embed(
    title: str,
    description: str,
    color: int = 0x5865F2,
    webhook_url: str | None = None,
    fields: list[dict] | None = None,
    footer: str | None = None,
    url: str | None = None,
) -> str:
    """
    Envia um embed formatado para um canal Discord.

    Embeds suportam markdown, links, campos nomeados, rodapé e cor.

    Args:
        title: Título do embed (até 256 chars).
        description: Corpo do embed (até 4096 chars, suporta markdown).
        color: Cor da barra lateral em formato hexadecimal (ex: 0xFF0000 = vermelho).
        webhook_url: URL do webhook. None = usa DISCORD_WEBHOOK_URL do ambiente.
        fields: Lista de campos adicionais: [{"name": "Chave", "value": "Valor", "inline": True}].
        footer: Texto do rodapé.
        url: URL que o título linkará.

    Returns:
        '✓ embed enviado' em sucesso, string de erro em falha.
    """
    wh_url = webhook_url or _get_webhook_url()

    embed: dict = {
        "title": title[:256],
        "description": description[:4096],
        "color": color,
    }
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = [
            {
                "name": str(f.get("name", ""))[:256],
                "value": str(f.get("value", ""))[:1024],
                "inline": bool(f.get("inline", False)),
            }
            for f in fields[:25]  # limite Discord
        ]
    if footer:
        embed["footer"] = {"text": footer[:2048]}

    payload = {"embeds": [embed]}
    result = _discord_request("POST", wh_url, payload)
    if result.get("ok") is False:
        return f"Erro Discord embed: {result.get('error', 'desconhecido')}"
    return "✓ embed enviado"


def send_channel_message(channel_id: str, text: str, bot_token: str | None = None) -> str:
    """
    Envia mensagem para um canal Discord por ID usando Bot Token.

    Requer DISCORD_BOT_TOKEN no ambiente (ou bot_token explícito).
    O bot deve ter permissão "Send Messages" no canal alvo.

    Args:
        channel_id: ID numérico do canal Discord.
        text: Conteúdo da mensagem (até 2000 chars).
        bot_token: Token do bot. None = usa DISCORD_BOT_TOKEN do ambiente.

    Returns:
        ID da mensagem criada em sucesso, string de erro em falha.
    """
    token = bot_token or _get_bot_token()
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    payload = {"content": text[:2000]}

    result = _discord_request("POST", url, payload, token=token)
    if result.get("ok") is False or "id" not in result:
        err = result.get("error") or result.get("message", "desconhecido")
        return f"Erro Discord channel: {err}"
    return result["id"]


def pin_message(channel_id: str, message_id: str, bot_token: str | None = None) -> str:
    """
    Fixa uma mensagem em um canal Discord.

    Args:
        channel_id: ID do canal.
        message_id: ID da mensagem a fixar.
        bot_token: Token do bot. None = usa DISCORD_BOT_TOKEN do ambiente.

    Returns:
        '✓ mensagem fixada' ou string de erro.
    """
    token = bot_token or _get_bot_token()
    url = f"https://discord.com/api/v10/channels/{channel_id}/pins/{message_id}"
    result = _discord_request("PUT", url, token=token)
    if result.get("ok") is False:
        return f"Erro ao fixar: {result.get('error', 'desconhecido')}"
    return "✓ mensagem fixada"


def create_thread(
    channel_id: str,
    name: str,
    auto_archive_minutes: int = 1440,
    bot_token: str | None = None,
) -> str:
    """
    Cria um thread em um canal Discord.

    Args:
        channel_id: ID do canal pai.
        name: Nome do thread (até 100 chars).
        auto_archive_minutes: Inatividade para arquivamento (60, 1440, 4320, 10080).
        bot_token: Token do bot.

    Returns:
        ID do thread criado ou string de erro.
    """
    token = bot_token or _get_bot_token()
    url = f"https://discord.com/api/v10/channels/{channel_id}/threads"
    payload = {
        "name": name[:100],
        "auto_archive_duration": auto_archive_minutes,
        "type": 11,  # PUBLIC_THREAD
    }
    result = _discord_request("POST", url, payload, token=token)
    if result.get("ok") is False or "id" not in result:
        err = result.get("error") or result.get("message", "desconhecido")
        return f"Erro ao criar thread: {err}"
    return result["id"]


def get_channel_messages(
    channel_id: str,
    limit: int = 10,
    bot_token: str | None = None,
) -> list[dict]:
    """
    Retorna as últimas mensagens de um canal Discord.

    Args:
        channel_id: ID do canal.
        limit: Número de mensagens (1-100).
        bot_token: Token do bot.

    Returns:
        Lista de dicts com {id, author, content, timestamp} ou lista vazia em erro.
    """
    token = bot_token or _get_bot_token()
    n = max(1, min(100, limit))
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={n}"
    result = _discord_request("GET", url, token=token)

    if isinstance(result, list):
        return [
            {
                "id": m.get("id"),
                "author": m.get("author", {}).get("username"),
                "content": m.get("content"),
                "timestamp": m.get("timestamp"),
            }
            for m in result
        ]
    return []


# ---------------------------------------------------------------------------
# Channel Registry Hook (DiscordAdapter)
# ---------------------------------------------------------------------------

try:
    from rlm.plugins.channel_registry import ChannelAdapter, ChannelRegistry

    class DiscordAdapter(ChannelAdapter):
        """
        Adapter Discord para o ChannelRegistry do RLM.

        Suporta dois modos por target_id:
        - "webhook" (ou vazio)   → posta no DISCORD_WEBHOOK_URL padrão
        - "<channel_id>"         → posta no canal via DISCORD_BOT_TOKEN

        Uso pelo REPL via ChannelRegistry:
            ChannelRegistry.reply("discord:webhook", "Deploy concluído")
            ChannelRegistry.reply("discord:1234567890", "Alerta: CPU alta")
        """

        def send_message(self, target_id: str, text: str) -> bool:
            """Envia texto ao target. 'webhook' = webhook URL, senão = channel_id."""
            if not target_id or target_id.lower() == "webhook":
                res = send_webhook(text)
            else:
                res = send_channel_message(target_id, text)
            return not res.startswith("Erro")

        def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
            """
            Envia arquivo para um canal Discord.

            Para webhook: embeds com link para o arquivo.
            Para channel_id: mensagem com botão de link.
            """
            message = f"{caption}\n{media_url_or_path}".strip() if caption else media_url_or_path
            return self.send_message(target_id, message)

    # Auto-registro — ativado quando DISCORD_WEBHOOK_URL ou DISCORD_BOT_TOKEN estão presentes
    if os.environ.get("DISCORD_WEBHOOK_URL") or os.environ.get("DISCORD_BOT_TOKEN"):
        ChannelRegistry.register("discord", DiscordAdapter())

except ImportError:
    pass  # Permite uso standalone sem o registry
