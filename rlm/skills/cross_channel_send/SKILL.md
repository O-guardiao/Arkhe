+++
name = "cross_channel_send"
description = "Envia mensagem para qualquer canal/destino via MessageBus multichannel. cross_channel_send('telegram:12345', 'msg') entrega pelo bus de forma assíncrona via Outbox + DeliveryWorker. Suporta todos os canais registrados: telegram, discord, whatsapp, slack, webchat, mqtt."
tags = ["multichannel", "cross-channel", "enviar mensagem", "redirecionar", "broadcast", "canal"]
priority = "contextual"

[sif]
signature = "cross_channel_send(target_client_id: str, message: str) -> str"
prompt_hint = "Use para enviar mensagem a outro canal/dispositivo. Formato target: 'canal:id' (ex: 'telegram:12345', 'slack:C02:msg', 'mqtt:esp32-sala')."
short_sig = "cross_channel_send(target,msg)"
compose = ["slack", "telegram_bot", "whatsapp", "discord"]
examples_min = ["enviar mensagem para o Telegram do usuário", "notificar outro canal"]

[runtime]
estimated_cost = 0.3
risk_level = "medium"
side_effects = ["remote_api_write"]
preconditions = ["message_bus_initialized"]
postconditions = ["message_enqueued_in_outbox"]
fallback_policy = "reply_text_error"

[quality]
historical_reliability = 0.9
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "cross channel send message another device telegram discord whatsapp slack mqtt multichannel"
example_queries = ["envie isso pro meu telegram", "manda alerta no slack", "redireciona pro whatsapp"]

[requires]
bins = []
+++

# Cross-Channel Send Skill

Envia mensagem de um canal para outro via MessageBus multichannel.

## Quando usar

✅ **USE quando:**
- "Envia esse resultado pro meu Telegram"
- "Manda alerta no canal #ops do Slack"
- "Redireciona essa resposta pro WhatsApp"
- "Envia comando JSON para o ESP32 da sala"
- "Notifica todos os canais sobre esse evento"

❌ **NÃO use quando:**
- Quer responder no canal de origem (use `reply()`)
- Quer enviar áudio (use `reply_audio()`)
- Quer enviar arquivo/mídia (use `send_media()`)

## Assinatura

```python
cross_channel_send(target_client_id: str, message: str) -> str
```

### Parâmetros

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `target_client_id` | `str` | Destino no formato `canal:id`. Ex: `telegram:12345`, `slack:C02`, `mqtt:esp32-sala` |
| `message` | `str` | Texto ou JSON da mensagem |

### Retorno

- `"ok"` — mensagem enfileirada com sucesso no Outbox
- `"error: <motivo>"` — falha (bus não inicializado, formato inválido, etc.)

## Exemplos

```python
# Enviar alerta para Telegram
cross_channel_send("telegram:12345", "Alerta: temperatura acima de 30°C!")

# Enviar comando JSON para IoT
cross_channel_send("mqtt:esp32-sala", '{"command":"ac_on","temp":23}')

# Notificar canal Slack
cross_channel_send("slack:C02ABC", "Deploy completado com sucesso!")
```

## Fluxo interno

1. Cria `Envelope(direction=OUTBOUND, target_client_id=target)`
2. Chama `MessageBus.enqueue_outbound(envelope)`
3. `DeliveryWorker` drena do `Outbox` e entrega via `ChannelRegistry`
4. Entrega assíncrona — sem bloquear o pipeline

## Segurança

- Validação de formato `canal:id` obrigatória
- Mensagem sanitizada antes do enqueue
- Bus failure não bloqueia o pipeline (non-fatal)
