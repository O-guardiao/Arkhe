+++
name = "channels"
description = "Retorna snapshot ao vivo de todos canais de comunicação (Telegram, Discord, Slack, WhatsApp, WebChat) com identidade do bot, status running/healthy, tempo do último probe e chat_ids conhecidos. Chame ANTES de enviar mensagens cross-channel para descobrir quais canais existem e seus IDs."
tags = ["channels", "multichannel", "status", "service discovery", "canais", "identidade bot", "probe"]
priority = "contextual"

[sif]
signature = "channels() -> dict[str, Any]"
prompt_hint = "Chame channels() para descobrir canais disponíveis e seus status antes de enviar mensagens. Retorna total, running, healthy e detalhes por canal."
short_sig = "channels()→{}"
compose = ["cross_channel_send", "telegram_bot"]
examples_min = ["quais canais estão online", "status dos canais", "que canais tenho", "o telegram está funcionando"]

[runtime]
estimated_cost = 0.01
risk_level = "low"
side_effects = []
preconditions = ["channel_status_registry_initialized"]
postconditions = ["channel_snapshot_returned"]
fallback_policy = "return_error_dict"

[quality]
historical_reliability = 0.95
success_count = 0
failure_count = 0
last_30d_utility = 0.7

[retrieval]
embedding_text = "channels status discovery identity bot telegram discord slack whatsapp webchat running healthy probe service discovery multichannel"
example_queries = ["quais canais estão ativos", "o telegram está online", "identidade do bot", "status dos canais de comunicação"]

[requires]
bins = []
+++

# Channels — Service Discovery

Retorna snapshot ao vivo de todos canais registrados no ChannelStatusRegistry.

## Quando usar

✅ **USE quando:**
- Precisa saber quais canais existem e estão funcionando
- Quer o chat_id ou username do bot antes de enviar mensagem
- Diagnóstico: "o Telegram está online?"
- Antes de `cross_channel_send()` para validar destino
- Precisa do `last_chat_id` para enviar notificação

❌ **NÃO use quando:**
- Quer enviar mensagem (use `telegram_bot()` ou `cross_channel_send()`)
- Quer ler mensagens recebidas (use `telegram_get_updates()`)
- Quer responder no canal de origem (use `reply()`)

## Exemplo

```python
status = channels()
print(status)
```

**Retorno típico:**
```json
{
  "total": 5,
  "running": 3,
  "healthy": 3,
  "channels": {
    "telegram": {
      "default": {
        "channel_id": "telegram",
        "account_id": "default",
        "enabled": true,
        "configured": true,
        "running": true,
        "healthy": true,
        "identity": {
          "bot_id": 123456789,
          "username": "meu_bot",
          "display_name": "Meu Bot"
        },
        "last_probe_ms": 120.5,
        "meta": {
          "last_chat_id": "987654321",
          "last_username": "dono"
        }
      }
    },
    "discord": { ... },
    "whatsapp": { ... }
  }
}
```

## Fluxo de descoberta recomendado

```python
# 1. Descubra canais ativos
info = channels()

# 2. Verifique se Telegram está rodando
tg = info.get("channels", {}).get("telegram", {}).get("default", {})
if tg.get("running"):
    # 3. Extraia chat_id do meta
    chat_id = tg.get("meta", {}).get("last_chat_id")
    if chat_id:
        telegram_bot(chat_id, "Notificação!")
    else:
        # fallback: tente telegram_get_updates
        updates = telegram_get_updates(limit=1)
        if updates:
            telegram_bot(updates[0]["chat_id"], "Notificação!")
```

## Dados incluídos por canal

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `channel_id` | str | Nome do canal (telegram, discord, etc.) |
| `enabled` | bool | Habilitado no config |
| `configured` | bool | Tem credenciais/tokens necessárias |
| `running` | bool | Gateway ativo e conectado |
| `healthy` | bool | Último probe retornou ok |
| `identity` | dict/null | Bot ID, username, display_name (se probe ativo) |
| `last_probe_ms` | float | Latência do último probe |
| `meta.last_chat_id` | str | Último chat_id que interagiu (Telegram) |
