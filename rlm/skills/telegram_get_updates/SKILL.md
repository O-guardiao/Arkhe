+++
name = "telegram_get_updates"
description = "Inspeciona mensagens Telegram ja processadas nesta sessao do runtime. Use when: operador no TUI ou REPL precisa entender o que chegou pelo Telegram no fluxo unificado. Esta ferramenta le historico local do servidor; nao chama a Bot API e nao disputa getUpdates com o gateway. NOT for: enviar mensagens Telegram (use telegram_bot), responder o canal atual (use reply quando disponivel)."
tags = ["telegram", "historico telegram", "mensagens recebidas telegram", "telegram updates", "tui telegram"]
priority = "contextual"

[requires]
bins = []

[runtime]
estimated_cost = 0.05
risk_level = "low"
side_effects = []
postconditions = ["local_telegram_history_read"]
fallback_policy = "use_event_log_or_reply"

[quality]
historical_reliability = 0.7
success_count = 0
failure_count = 0
last_30d_utility = 0.6

[retrieval]
embedding_text = "telegram updates history inbox local runtime session event log tui repl"
example_queries = ["o que chegou pelo telegram", "historico recente do telegram", "mensagens telegram desta sessao"]
+++

# Telegram Get Updates

Ferramenta de introspecao operacional para TUI e REPL.

`telegram_get_updates(...)` retorna apenas mensagens Telegram que o runtime ja recebeu e registrou nesta sessao unificada. Ela nao faz polling na Bot API e por isso nao interfere no `TelegramGateway`.

## Quando usar

✅ USE quando:
- voce esta no TUI e quer ver o que entrou pelo Telegram
- precisa confirmar a ultima mensagem recebida antes de responder
- quer inspecionar o historico local ja processado pelo servidor

❌ NAO use quando:
- quer enviar uma mensagem Telegram nova
- quer responder o canal atual e `reply(...)` ja resolve
- quer consumir a fila viva de updates do bot

## Exemplos

```python
updates = telegram_get_updates(limit=5)
for item in updates:
    print(item["timestamp"], item["from_user"], item["text"])
```

```python
# Filtrar por chat especifico dentro da sessao unificada
updates = telegram_get_updates(limit=10, chat_id="1968290446")
FINAL_VAR("updates")
```

## Retorno

Cada item retorna um dicionario com:
- `timestamp`
- `client_id`
- `chat_id`
- `from_user`
- `text`
- `payload_size`
- `source`

Se nada tiver sido recebido pelo Telegram nesta sessao, o retorno sera uma lista vazia.