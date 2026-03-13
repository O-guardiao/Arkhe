+++
name = "slack"
description = "Interage com Slack: envia mensagens, lista canais, faz upload de arquivos, lê histórico, reage a mensagens, cria canais, busca mensagens. Use when: user asks to enviar mensagem no Slack, notificar equipe, postar relatório em canal, monitorar canal, enviar arquivo para Slack. Requer Slack Bot Token com permissões adequadas."
tags = ["slack", "mensagem slack", "canal slack", "workspace slack", "notificar equipe", "slack bot"]
priority = "contextual"

[sif]
signature = "slack.send(channel: str, text: str, token: str = '') -> dict"
prompt_hint = "Use para avisar equipe, publicar relatório, enviar arquivo ou interagir com canal no Slack."
short_sig = "slack.send(channel,text)"
compose = ["shell", "github", "email"]
examples_min = ["enviar notificação para um canal Slack"]

[runtime]
estimated_cost = 0.8
risk_level = "medium"
side_effects = ["remote_api_write"]
preconditions = ["env:SLACK_BOT_TOKEN"]
postconditions = ["slack_message_or_file_delivered"]
fallback_policy = "use_email_or_reply_text"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "slack workspace channel notify team message upload file"
example_queries = ["avise a equipe no Slack", "poste este relatório no canal"]

[requires]
bins = []
+++

# Slack Skill

Automação completa do Slack via Web API: mensagens, canais, arquivos e mais.

## Quando usar

✅ **USE quando:**
- "Envia relatório de vendas no canal #reports"
- "Lista todos os canais do workspace"
- "Posta um alerta no #alerts: servidor caiu"
- "Faz upload do arquivo análise.csv no canal #data"
- "Busca mensagens sobre 'deploy' nos últimos 30 dias"
- "Reage com ✅ na última mensagem do canal #releases"

❌ **NÃO use quando:**
- Videochamadas → Slack Huddles API (diferente)
- Administrar usuários → Slack Admin API (requer workspace admin)

## Setup

```python
import requests, os, json

SLACK_TOKEN      = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL    = os.environ.get("SLACK_CHANNEL_ID", "")

BASE_URL = "https://slack.com/api"

def slack_get(endpoint: str, params: dict = {}) -> dict:
    r = requests.get(
        f"{BASE_URL}/{endpoint}",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        params=params,
        timeout=15,
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error')}")
    return data

def slack_post(endpoint: str, payload: dict) -> dict:
    r = requests.post(
        f"{BASE_URL}/{endpoint}",
        headers={
            "Authorization": f"Bearer {SLACK_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload),
        timeout=15,
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error')}")
    return data
```

## Enviar mensagem

```python
def enviar_mensagem(
    canal: str,          # "#geral" ou "C1234567890" (channel ID)
    texto: str,
    username: str | None = None,
    emoji_icon: str | None = None,  # ":robot_face:"
    thread_ts: str | None = None,   # timestamp para responder em thread
) -> dict:
    """Envia mensagem para canal ou usuário."""
    payload = {"channel": canal, "text": texto}
    if username:
        payload["username"] = username
    if emoji_icon:
        payload["icon_emoji"] = emoji_icon
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return slack_post("chat.postMessage", payload)

# Exemplos:
enviar_mensagem("#geral", "✅ Deploy em produção concluído com sucesso!")
enviar_mensagem(SLACK_CHANNEL, "⚠️ Alerta: uso de CPU acima de 90%", emoji_icon=":warning:")
```

## Mensagem formatada com blocos (Block Kit)

```python
def enviar_bloco(canal: str, titulo: str, corpo: str, cor: str = "#36a64f") -> dict:
    """Envia mensagem formatada com attachment colorido."""
    payload = {
        "channel": canal,
        "attachments": [
            {
                "color": cor,  # "#36a64f"=verde, "#ff0000"=vermelho, "#ffa500"=laranja
                "blocks": [
                    {"type": "header", "text": {"type": "plain_text", "text": titulo}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": corpo}},
                ],
            }
        ],
    }
    return slack_post("chat.postMessage", payload)

enviar_bloco(
    "#reports",
    "📊 Relatório Diário — 15/01/2026",
    "*Vendas:* R$ 42.500\n*Pedidos:* 183\n*Conversão:* 3,2%",
    cor="#2eb886",
)
```

## Listar canais

```python
def listar_canais(tipos: str = "public_channel,private_channel") -> list[dict]:
    """Lista todos os canais do workspace."""
    resultado = slack_get("conversations.list", {
        "types": tipos,
        "limit": 200,
        "exclude_archived": "true",
    })
    return [
        {
            "id":      c["id"],
            "nome":    c["name"],
            "privado": c.get("is_private", False),
            "membros": c.get("num_members", 0),
        }
        for c in resultado.get("channels", [])
    ]

canais = listar_canais()
for c in canais[:10]:
    print(f"{'🔒' if c['privado'] else '#'} {c['nome']} ({c['membros']} membros) — {c['id']}")
```

## Ler histórico de canal

```python
def ler_historico(canal: str, limite: int = 20) -> list[dict]:
    """Lê mensagens recentes de um canal."""
    resultado = slack_get("conversations.history", {
        "channel": canal,
        "limit":   limite,
    })
    return [
        {
            "ts":    m["ts"],
            "user":  m.get("user", "bot"),
            "texto": m.get("text", ""),
        }
        for m in resultado.get("messages", [])
        if m.get("type") == "message"
    ]

from datetime import datetime
msgs = ler_historico(SLACK_CHANNEL, limite=10)
for m in msgs:
    dt = datetime.fromtimestamp(float(m["ts"])).strftime("%d/%m %H:%M")
    print(f"[{dt}] {m['user']}: {m['texto'][:100]}")
```

## Buscar mensagens

```python
def buscar_mensagens(query: str, count: int = 10) -> list[dict]:
    """Busca full-text em mensagens do workspace. Requer scope search:read."""
    resultado = slack_get("search.messages", {"query": query, "count": count})
    matches = resultado.get("messages", {}).get("matches", [])
    return [
        {
            "canal":   m["channel"]["name"],
            "texto":   m["text"],
            "ts":      m["ts"],
            "usuario": m.get("username", ""),
        }
        for m in matches
    ]

resultados = buscar_mensagens("deploy falhou")
for r in resultados:
    print(f"#{r['canal']}: {r['texto'][:100]}")
```

## Upload de arquivo

```python
def upload_arquivo(canal: str, caminho_local: str, titulo: str = "") -> dict:
    """Faz upload de arquivo para canal."""
    with open(caminho_local, "rb") as f:
        conteudo = f.read()
    nome_arquivo = caminho_local.split("/")[-1].split("\\")[-1]
    r = requests.post(
        f"{BASE_URL}/files.upload",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        data={
            "channels": canal,
            "filename": nome_arquivo,
            "title": titulo or nome_arquivo,
        },
        files={"file": (nome_arquivo, conteudo)},
        timeout=60,
    )
    return r.json()

upload_arquivo("#data", "/tmp/relatorio_mensal.csv", titulo="Relatório Janeiro 2026")
```

## Reagir a mensagem

```python
def reagir(canal: str, timestamp: str, emoji: str) -> dict:
    """Adiciona reação emoji a uma mensagem."""
    return slack_post("reactions.add", {
        "channel": canal,
        "timestamp": timestamp,
        "name": emoji.strip(":"),
    })

# Obtém última mensagem e reage
msgs = ler_historico(SLACK_CHANNEL, limite=1)
if msgs:
    reagir(SLACK_CHANNEL, msgs[0]["ts"], "white_check_mark")
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `SLACK_BOT_TOKEN` | Token do Bot (começa com `xoxb-`) |
| `SLACK_CHANNEL_ID` | Canal padrão (ex: `C1234567890`) |

**Permissões mínimas necessárias no Bot Token Scopes:**
`chat:write`, `channels:read`, `channels:history`, `files:write`, `reactions:write`, `search:read`

Criar em: [api.slack.com/apps](https://api.slack.com/apps) → Create New App → OAuth & Permissions.
