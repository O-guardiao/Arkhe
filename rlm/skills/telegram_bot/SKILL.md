+++
name = "telegram_bot"
description = "Envia mensagens Telegram via Bot API. No runtime bridge ao vivo, telegram_bot e apenas envio. Use when: user asks to enviar mensagem Telegram, notificar via Telegram, ou o agente precisa reportar progresso por Telegram. Para inspecionar mensagens ja recebidas nesta sessao, use telegram_get_updates. NOT for: WhatsApp (use whatsapp skill), email (use email skill), ligações (use voice skill)."
tags = ["telegram", "bot telegram", "mensagem telegram", "notificar telegram", "alerta telegram"]
priority = "contextual"

[requires]
bins = []

[sif]
signature = "telegram_send(chat_id: str, text: str, parse_mode: str = 'Markdown') -> dict"
prompt_hint = "Use para notificar, alertar ou reportar progresso por Telegram para um chat ou canal. Ferramenta apenas de envio."
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

Envio de mensagens Telegram via Bot API.

No runtime bridge ao vivo, `telegram_bot` e uma ferramenta de envio apenas.
Ela nao deve ser usada para descobrir mensagens recebidas, ler inbox nem fazer polling.
O gateway ja consome `getUpdates` em long-polling; duplicar essa leitura no REPL disputa a fila e cria resultados enganosos.

## Quando usar

✅ **USE quando:**
- "Manda mensagem para meu Telegram"
- "Notifica o chat_id X que o processo terminou"
- "Envia o resultado como arquivo para o Telegram"
- Agente reporta progresso de tarefas longas
- Precisa notificar outro chat explicitamente, fora do canal atual

❌ **NÃO use quando:**
- Você quer descobrir o que chegou pelo Telegram nesta sessao
- Você quer responder a mensagem atual do canal de origem e `reply(...)` ja existe
- WhatsApp → use `whatsapp` skill
- Email → use `email` skill

Para introspecao segura do historico ja processado pelo runtime, use `telegram_get_updates(...)`.
Para responder ao canal atual quando a origem e replyable, prefira `reply(text)`.

## Uso básico

```python
# Ferramenta SIF pre-injetada para envio direto
resultado = telegram_bot("123456789", "Tarefa concluida! ✅")
print(resultado)

# Tambem funciona bem para avisos curtos de progresso
telegram_bot("123456789", "⏳ Analise iniciada")
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

## Limites operacionais

```python
# Correto: usar reply para responder a mensagem atual do canal replyable
reply("Recebido. Vou continuar e te atualizo por aqui.")

# Correto: usar telegram_get_updates para ver o que ja entrou no runtime
historico = telegram_get_updates(limit=5)

# Incorreto: tentar usar telegram_bot como leitura de inbox
# telegram_bot("get_updates")
```

## Notificar progresso de tarefa longa (sub_rlm)

```python
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

telegram_bot(str(CHAT_ID), "⏳ Iniciando analise de dados...")

# ... processamento ...

telegram_bot(str(CHAT_ID), f"✅ Analise concluida!\nTotal: {resultado['total']} registros")
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token do bot (BotFather: /newbot) |
| `TELEGRAM_CHAT_ID` | ID do chat padrão para notificações |

Para obter `chat_id` pela primeira vez, use um script isolado ou consulte o historico ja processado no runtime. Nao rode polling cru no mesmo processo que o TelegramGateway ativo.
