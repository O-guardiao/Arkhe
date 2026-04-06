"""
Plugin Slack — rlm/plugins/slack.py

Integração com Slack via Web API e Incoming Webhooks.

Arquitetura RLM-nativa:
    - Outbound via Slack Web API (stdlib urllib, token xoxb-...)
    - Outbound simplificado via Incoming Webhook URL (sem bot token)
    - SlackAdapter registrado no ChannelRegistry como prefixo "slack"
    - Inbound: rlm/server/slack_gateway.py recebe Slack Events API

Variáveis de ambiente:
    SLACK_BOT_TOKEN    — Token do bot (xoxb-...) para Web API completa
    SLACK_WEBHOOK_URL  — URL do Incoming Webhook (mais simples, sem bot token)
    SLACK_SIGNING_SECRET — Segredo de assinatura para verificação HMAC (gateway)

Uso no REPL:
    >>> from rlm.plugins.slack import post_message, post_blocks
    >>> post_message("#geral", "Deploy concluído! ✅")
    '✓ mensagem enviada ao Slack'
    >>> post_blocks("#alertas", [{"type": "section", "text": {"type": "mrkdwn", "text": "*Alerta*"}}])
    '✓ blocks enviados'
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from urllib import error as urllib_error
from urllib import parse as urllib_parse
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
    name="slack",
    version="1.0.0",
    description="Slack — Web API e Incoming Webhooks (mensagens, blocks, reactions, threads, uploads).",
    functions=[
        "post_message",
        "post_blocks",
        "post_ephemeral",
        "post_reply",
        "add_reaction",
        "upload_snippet",
        "get_channel_history",
        "send_webhook",
    ],
    author="RLM Engine",
    requires=[],  # stdlib urllib apenas
)

_SLACK_API_BASE = "https://slack.com/api"


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _get_bot_token() -> str:
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if not tok:
        raise ValueError(
            "SLACK_BOT_TOKEN não configurado. "
            "Defina no .env: SLACK_BOT_TOKEN=xoxb-..."
        )
    return tok


def _get_webhook_url() -> str:
    url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not url:
        raise ValueError(
            "SLACK_WEBHOOK_URL não configurado. "
            "Defina no .env: SLACK_WEBHOOK_URL=https://hooks.slack.com/..."
        )
    return url


def _slack_api(method: str, params: dict) -> dict:
    """
    Chama a Slack Web API via POST JSON.

    Todos os métodos da Web API retornam {"ok": true/false, ...}.
    Em caso de erro, o campo "error" contém o código de erro.
    """
    token = _get_bot_token()
    url = f"{_SLACK_API_BASE}/{method}"
    data = json.dumps(params, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    req = urllib_request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib_error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}: {body[:300]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _slack_result_to_str(result: dict, ok_msg: str) -> str:
    """Helper: converte resultado da Web API em string legível."""
    if result.get("ok"):
        return ok_msg
    return f"Erro Slack: {result.get('error', 'desconhecido')}"


# ---------------------------------------------------------------------------
# Funções REPL
# ---------------------------------------------------------------------------

def post_message(channel: str, text: str, unfurl_links: bool = False) -> str:
    """
    Posta uma mensagem de texto em um canal, grupo ou DM do Slack.

    Args:
        channel: ID ou nome do canal (ex: '#geral', 'C12345ABC', '@usuario').
        text: Corpo da mensagem. Suporta mrkdwn (ex: '*negrito*', '_itálico_').
        unfurl_links: True para expandir preview de links automaticamente.

    Returns:
        '✓ mensagem enviada ao Slack' ou descrição do erro.
    """
    result = _slack_api("chat.postMessage", {
        "channel": channel,
        "text": text,
        "unfurl_links": unfurl_links,
        "unfurl_media": unfurl_links,
    })
    return _slack_result_to_str(result, "✓ mensagem enviada ao Slack")


def post_blocks(channel: str, blocks: list[dict], text: str = "") -> str:
    """
    Posta uma mensagem com Block Kit (layout estruturado) no Slack.

    Block Kit é a forma moderna de criar mensagens ricas: seções, botões,
    imagens, campos de entrada, etc.

    Args:
        channel: ID ou nome do canal.
        blocks: Lista de blocos Block Kit.
            Exemplo de bloco seção com mrkdwn:
            [{"type": "section", "text": {"type": "mrkdwn", "text": "*Alerta* urgente"}}]
        text: Fallback de texto simples (exibido em notificações push e leitores de tela).

    Returns:
        '✓ blocks enviados' ou descrição do erro.
    """
    result = _slack_api("chat.postMessage", {
        "channel": channel,
        "blocks": blocks,
        "text": text or "Nova mensagem",
    })
    return _slack_result_to_str(result, "✓ blocks enviados")


def post_ephemeral(channel: str, user_id: str, text: str) -> str:
    """
    Posta uma mensagem efêmera (visível apenas para um usuário específico).

    Útil para confirmações, warnings e feedbacks que não devem poluir o canal.

    Args:
        channel: Canal onde a mensagem ephemeral aparece.
        user_id: ID do usuário Slack (ex: 'U12345ABC').
        text: Conteúdo da mensagem (suporta mrkdwn).

    Returns:
        '✓ ephemeral enviado' ou descrição do erro.
    """
    result = _slack_api("chat.postEphemeral", {
        "channel": channel,
        "user": user_id,
        "text": text,
    })
    return _slack_result_to_str(result, "✓ ephemeral enviado")


def post_reply(channel: str, thread_ts: str, text: str, broadcast: bool = False) -> str:
    """
    Responde em um thread existente no Slack.

    Args:
        channel: Canal onde o thread existe.
        thread_ts: Timestamp da mensagem pai (campo 'ts' do evento original).
        text: Conteúdo da resposta.
        broadcast: True para enviar a resposta também para o canal principal.

    Returns:
        'ts:...' com o timestamp da resposta em sucesso, string de erro em falha.
    """
    result = _slack_api("chat.postMessage", {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": text,
        "reply_broadcast": broadcast,
    })
    if result.get("ok"):
        return f"ts:{result.get('ts', '')}"
    return f"Erro Slack thread: {result.get('error', 'desconhecido')}"


def add_reaction(channel: str, timestamp: str, emoji: str) -> str:
    """
    Adiciona um emoji de reação a uma mensagem do Slack.

    Args:
        channel: Canal da mensagem.
        timestamp: Campo 'ts' da mensagem.
        emoji: Nome do emoji sem ':' (ex: 'thumbsup', 'white_check_mark').

    Returns:
        '✓ reação adicionada' ou descrição do erro.
    """
    result = _slack_api("reactions.add", {
        "channel": channel,
        "timestamp": timestamp,
        "name": emoji.strip(":"),
    })
    return _slack_result_to_str(result, "✓ reação adicionada")


def upload_snippet(
    channel: str,
    content: str,
    filename: str = "snippet.txt",
    title: str = "",
    filetype: str = "text",
) -> str:
    """
    Envia um snippet de código ou texto como arquivo no Slack.

    Mais limpo que colar código longo numa mensagem. Suporta syntax highlight.

    Args:
        channel: Canal de destino.
        content: Conteúdo do arquivo.
        filename: Nome do arquivo (ex: 'report.py', 'output.json').
        title: Título exibido acima do snippet.
        filetype: Tipo para syntax highlight (ex: 'python', 'json', 'text').

    Returns:
        'file_id:...' em sucesso ou descrição do erro.
    """
    # files.getUploadURLExternal → upload → files.completeUploadExternal (nova API v2)
    # Para simplicidade, usa files.upload legado (ainda suportado)
    token = _get_bot_token()
    url = f"{_SLACK_API_BASE}/files.upload"

    # Multipart form-data (stdlib)
    import mimetypes
    boundary = f"RLMSlack{os.getpid()}"
    body_parts: list[bytes] = []
    crlf = b"\r\n"

    fields = {
        "channels": channel,
        "content": content,
        "filename": filename,
        "filetype": filetype,
    }
    if title:
        fields["title"] = title

    for key, value in fields.items():
        body_parts.append(
            f"--{boundary}".encode() + crlf
            + f'Content-Disposition: form-data; name="{key}"'.encode() + crlf
            + crlf
            + value.encode("utf-8") + crlf
        )
    body_parts.append(f"--{boundary}--".encode() + crlf)
    body = b"".join(body_parts)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    req = urllib_request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return f"Erro upload_snippet: {exc}"

    if result.get("ok"):
        return f"file_id:{result.get('file', {}).get('id', '')}"
    return f"Erro Slack upload: {result.get('error', 'desconhecido')}"


def get_channel_history(channel: str, limit: int = 10) -> list[dict]:
    """
    Retorna as últimas mensagens de um canal Slack.

    Args:
        channel: ID do canal (ex: 'C12345ABC').
        limit: Número de mensagens (1-100).

    Returns:
        Lista de dicts {ts, user, text, type} ou lista vazia em erro.
    """
    result = _slack_api("conversations.history", {
        "channel": channel,
        "limit": max(1, min(100, limit)),
    })
    if not result.get("ok"):
        return []
    return [
        {
            "ts": m.get("ts"),
            "user": m.get("user"),
            "text": m.get("text"),
            "type": m.get("type"),
        }
        for m in result.get("messages", [])
    ]


def send_webhook(text: str, webhook_url: str | None = None) -> str:
    """
    Posta mensagem simples via Incoming Webhook URL do Slack.

    Não requer bot token — apenas o URL do webhook configurado no app Slack.
    Útil para notificações simples sem necessidade de API completa.

    Args:
        text: Conteúdo da mensagem (mrkdwn suportado).
        webhook_url: URL do webhook. None = usa SLACK_WEBHOOK_URL do ambiente.

    Returns:
        '✓ enviado via webhook' ou descrição do erro.
    """
    url = webhook_url or _get_webhook_url()
    payload = {"text": text}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib_request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            if body.strip() == "ok":
                return "✓ enviado via webhook"
            return f"Slack webhook retornou: {body[:100]}"
    except urllib_error.HTTPError as e:
        return f"Erro Slack webhook HTTP {e.code}"
    except Exception as exc:
        return f"Erro Slack webhook: {exc}"


# ---------------------------------------------------------------------------
# Channel Registry Hook (SlackAdapter)
# ---------------------------------------------------------------------------

try:
    from rlm.plugins.channel_registry import ChannelAdapter, ChannelRegistry

    class SlackAdapter(ChannelAdapter):
        """
        Adapter Slack para o ChannelRegistry do RLM.

        target_id pode ser:
        - "webhook"          → usa SLACK_WEBHOOK_URL (Incoming Webhook)
        - "#canal" ou "C..." → usa Web API com SLACK_BOT_TOKEN
        - "U..." (user)      → DM via Web API

        Uso via ChannelRegistry:
            ChannelRegistry.reply("slack:#alerts", "Deploy concluído!")
            ChannelRegistry.reply("slack:C12345ABC", "Relatório gerado")
        """

        def send_message(self, target_id: str, text: str) -> bool:
            if target_id.lower() == "webhook":
                res = send_webhook(text)
                return res.startswith("✓")
            res = post_message(target_id, text)
            return res.startswith("✓")

        def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
            """
            Slack não aceita upload por URL direta — envia link como mensagem.

            Para upload de conteúdo textual, use upload_snippet() diretamente.
            """
            msg = f"{caption}\n{media_url_or_path}".strip() if caption else media_url_or_path
            return self.send_message(target_id, msg)

    # Auto-registro quando bot token ou webhook URL estão presentes
    if os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_WEBHOOK_URL"):
        ChannelRegistry.register("slack", SlackAdapter())

except ImportError:
    pass  # Permite uso standalone sem o registry
