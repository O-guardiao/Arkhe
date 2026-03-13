+++
name = "telegram_bot"
description = "Envia e recebe mensagens Telegram via Bot API. O plugin rlm.plugins.telegram já está disponível — use diretamente no REPL. Use when: user asks to enviar mensagem Telegram, notificar via Telegram, receber atualizações, ou o agente precisa reportar progresso por Telegram. NOT for: WhatsApp (use whatsapp skill), email (use email skill), ligações (use voice skill)."
tags = ["telegram", "bot telegram", "mensagem telegram", "notificar telegram"]
priority = "contextual"

[requires]
bins = []

[sif]
signature = "telegram_send(chat_id: str, text: str, parse_mode: str = 'Markdown') -> dict"
prompt_hint = "Use para notificar, alertar ou reportar progresso por Telegram para um chat ou canal."
short_sig = "telegram_bot(cid,txt)→{}"
compose = ["whatsapp", "email", "voice"]
examples_min = ["enviar atualização de progresso para um chat Telegram"]
codex = "lambda cid,txt: __import__('json').loads(__import__('urllib.request',fromlist=['x']).urlopen(__import__('urllib.request',fromlist=['r']).Request('https://api.telegram.org/bot'+__import__('os').environ['TELEGRAM_BOT_TOKEN']+'/sendMessage',__import__('urllib.parse',fromlist=['x']).urlencode({'chat_id':cid,'text':txt}).encode()),timeout=10).read())"
impl = """
def telegram_send(chat_id, text, parse_mode="Markdown"):
    import os, urllib.request, urllib.parse, json
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN nao definido")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }).encode()
    with urllib.request.urlopen(url, data=data, timeout=10) as r:
        return json.loads(r.read())
"""

[runtime]
estimated_cost = 0.55
risk_level = "medium"
side_effects = ["remote_api_write"]
postconditions = ["telegram_message_delivered_or_attempted"]
fallback_policy = "use_email_or_whatsapp"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "telegram bot chat notification message alert progress"
example_queries = ["mande mensagem no Telegram", "notifique este chat"]
+++

# Telegram Bot Skill

Integração com Telegram Bot API via `rlm.plugins.telegram` (já disponível no REPL).

## Quando usar

✅ **USE quando:**
- "Manda mensagem para meu Telegram"
- "Notifica o chat_id X que o processo terminou"
- "Lê as últimas mensagens do bot"
- "Envia o resultado como arquivo para o Telegram"
- Agente reporta progresso de tarefas longas

❌ **NÃO use quando:**
- WhatsApp → use `whatsapp` skill
- Email → use `email` skill

## Uso básico (plugin pré-carregado)

```python
# O plugin está importado diretamente — sem imports adicionais
from rlm.plugins.telegram import send_message, get_updates, send_document

# Enviar mensagem de texto
resultado = send_message(chat_id=123456789, text="Tarefa concluída! ✅")
print(resultado)

# Com formatação Markdown
send_message(
    chat_id=123456789,
    text="*Relatório Diário*\n`data: 2026-03-06`\nTotal: **R$ 12.450,00**",
    parse_mode="Markdown",
)

# Obter chat_id (primeira vez)
updates = get_updates()
for u in updates:
    print(f"Chat ID: {u.get('message', {}).get('chat', {}).get('id')}")
    print(f"Texto: {u.get('message', {}).get('text')}")
```

## Enviar arquivo (CSV, JSON, PDF)

```python
from rlm.plugins.telegram import send_document
import json

# Exportar resultado como JSON e enviar
dados = {"total": 1250, "itens": ["A", "B", "C"]}
with open("/tmp/resultado.json", "w") as f:
    json.dump(dados, f, ensure_ascii=False, indent=2)

send_document(
    chat_id=123456789,
    file_path="/tmp/resultado.json",
    caption="Relatório gerado automaticamente",
)
```

## Enviar foto/imagem

```python
from rlm.plugins.telegram import send_photo

send_photo(
    chat_id=123456789,
    file_path="/tmp/grafico.png",
    caption="Gráfico de vendas — Março 2026",
)
```

## Receber mensagens / polling

```python
from rlm.plugins.telegram import get_updates

updates = get_updates(offset=None, limit=10, timeout=5)
mensagens = []
for u in updates:
    msg = u.get("message", {})
    if "text" in msg:
        mensagens.append({
            "chat_id": msg["chat"]["id"],
            "from": msg["from"].get("username", ""),
            "texto": msg["text"],
            "data": msg["date"],
        })

FINAL_VAR("mensagens")
```

## Notificar progresso de tarefa longa (sub_rlm)

```python
from rlm.plugins.telegram import send_message

CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

send_message(CHAT_ID, "⏳ Iniciando análise de dados...")

# ... processamento ...

send_message(CHAT_ID, f"✅ Análise concluída!\nTotal: {resultado['total']} registros")
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token do bot (BotFather: /newbot) |
| `TELEGRAM_CHAT_ID` | ID do chat padrão para notificações |

Para obter chat_id: inicia conversa com o bot → `/getUpdates` na API.
