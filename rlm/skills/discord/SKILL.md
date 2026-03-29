+++
name = "discord"
description = "Send messages, reactions, embeds, and manage channels on Discord via Bot API. Use when: user asks to enviar mensagem no Discord, postar em canal, reagir, criar embed, ler histórico de canal, ou gerenciar servidor Discord. NOT for: Slack (use slack skill), Telegram (use telegram_bot skill), email (use email skill)."
tags = ["discord", "mensagem", "canal", "servidor", "bot", "embed", "reação", "chat", "gaming", "comunidade"]
priority = "lazy"

[sif]
signature = "discord_send(channel_id: str, content: str, embed: dict = None) -> dict"
prompt_hint = "Use para enviar mensagem ou embed em canal Discord. Requer DISCORD_BOT_TOKEN."
short_sig = "discord_send(channel_id,content,embed=None)→{}"
compose = ["slack", "telegram_bot", "email", "notion"]
examples_min = ["enviar mensagem em canal Discord"]
impl = """
def discord_send(channel_id, content='', embed=None):
    import urllib.request, json, os
    token = os.environ.get('DISCORD_BOT_TOKEN', '')
    if not token:
        return {"error": "DISCORD_BOT_TOKEN não configurado"}
    body = {}
    if content:
        body["content"] = content
    if embed:
        body["embeds"] = [embed]
    if not body:
        return {"error": "content ou embed obrigatório"}
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=data,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}
    return {
        "message_id": result.get("id", ""),
        "channel_id": channel_id,
        "content": result.get("content", ""),
        "timestamp": result.get("timestamp", ""),
    }
"""

[requires]
bins = []

[runtime]
estimated_cost = 0.2
risk_level = "medium"
side_effects = ["http_request", "discord_message"]
postconditions = ["message_sent_to_discord"]
fallback_policy = "use_slack_or_telegram_or_email"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "discord message channel server bot embed reaction notify community"
example_queries = ["envie mensagem no Discord", "poste no canal do Discord"]
+++

# Discord Skill

Envia mensagens, embeds e reações em canais Discord via Bot API.

## Quando usar

✅ **USE quando:**
- "Envie mensagem no canal #geral"
- "Poste atualização no Discord"
- "Crie embed com status do deploy"
- "Leia últimas mensagens do canal"
- "Reaja com ✅ na mensagem"

❌ **NÃO use quando:**
- Slack → use `slack` skill
- Telegram → use `telegram_bot` skill
- Email → use `email` skill
- WhatsApp → use `whatsapp` skill

## Configuração

```bash
export DISCORD_BOT_TOKEN="seu_token_aqui"
```

Crie bot em https://discord.com/developers/applications → Bot → Token.
Permissões mínimas: `Send Messages`, `Read Message History`, `Add Reactions`.

## Função injetada no REPL

```python
# Mensagem simples
resultado = discord_send("1234567890123456", "Deploy concluído com sucesso! 🚀")
print(resultado["message_id"])

# Mensagem com embed rico
resultado = discord_send(
    "1234567890123456",
    embed={
        "title": "📊 Status do Sistema",
        "description": "Todos os serviços operacionais",
        "color": 0x00FF00,  # verde
        "fields": [
            {"name": "API", "value": "✅ Online", "inline": True},
            {"name": "DB", "value": "✅ Online", "inline": True},
            {"name": "Latência", "value": "45ms", "inline": True},
        ],
        "footer": {"text": "Atualizado via RLM Agent"},
    },
)
```

## Ler histórico do canal

```python
import urllib.request, json, os

def discord_history(channel_id: str, limit: int = 10) -> list[dict]:
    """Lê últimas mensagens de um canal Discord."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={limit}",
        headers={"Authorization": f"Bot {token}"},
    )
    resp = urllib.request.urlopen(req, timeout=15)
    msgs = json.loads(resp.read())
    return [
        {
            "id": m["id"],
            "author": m["author"]["username"],
            "content": m["content"],
            "timestamp": m["timestamp"],
        }
        for m in msgs
    ]

mensagens = discord_history("1234567890123456", limit=5)
for m in mensagens:
    print(f"[{m['author']}] {m['content']}")
```

## Reagir a mensagem

```python
import urllib.request, os

def discord_react(channel_id: str, message_id: str, emoji: str = "✅") -> bool:
    """Adiciona reação a uma mensagem."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    encoded_emoji = urllib.parse.quote(emoji)
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me",
        method="PUT",
        headers={"Authorization": f"Bot {token}"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False

discord_react("1234567890123456", "9876543210987654", "🚀")
```

## Editar mensagem

```python
import urllib.request, json, os

def discord_edit(channel_id: str, message_id: str, content: str) -> dict:
    """Edita conteúdo de uma mensagem existente (só mensagens do bot)."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    data = json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
        data=data,
        method="PATCH",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())
```

## Enviar arquivo

```python
import urllib.request, os

def discord_upload(channel_id: str, file_path: str, message: str = "") -> dict:
    """Envia arquivo para canal Discord."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    fname = os.path.basename(file_path)
    boundary = "----RLMDiscordBoundary"
    
    parts = []
    if message:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="content"\r\n\r\n{message}'.encode()
        )
    with open(file_path, "rb") as f:
        file_data = f.read()
    file_header = f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{fname}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
    body = b"\r\n".join(parts) + b"\r\n" + file_header + file_data + f"\r\n--{boundary}--\r\n".encode()
    
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=body,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read())
```
