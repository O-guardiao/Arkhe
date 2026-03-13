+++
name = "whatsapp"
description = "Envia e recebe mensagens WhatsApp via WhatsApp Business API (Meta Cloud API). Use when: user asks to enviar mensagem WhatsApp, notificar contato por WhatsApp, ou processar webhooks de mensagens recebidas. NOT for: Telegram (use telegram_bot skill), SMS (use voice skill), email (use email skill). REQUER: conta WhatsApp Business + token Meta."
tags = ["whatsapp", "mensagem whatsapp", "zap", "wpp", "whats"]
priority = "lazy"

[requires]
bins = []

[sif]
signature = "whatsapp.send(to: str, text: str) -> dict"
prompt_hint = "Use para mandar mensagem direta, confirmação, lembrete ou notificação por WhatsApp."
short_sig = "whatsapp.send(to,txt)→{}"
compose = ["telegram_bot", "email", "voice"]
examples_min = ["enviar lembrete ou confirmação por WhatsApp"]

[runtime]
estimated_cost = 0.75
risk_level = "high"
side_effects = ["remote_api_write", "message_send"]
postconditions = ["whatsapp_message_delivered_or_attempted"]
fallback_policy = "use_telegram_or_email"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "whatsapp business message reminder notification contact meta api"
example_queries = ["mande mensagem no WhatsApp", "notifique o cliente por WhatsApp"]
+++

# WhatsApp Skill

WhatsApp Business Cloud API diretamente via `requests` no REPL.

## Quando usar

✅ **USE quando:**
- "Envia confirmação para +5511999..."
- "Notifica o cliente via WhatsApp"
- "Manda template de lembrete via WhatsApp"

❌ **NÃO use quando:**
- Conta pessoal WhatsApp (API não disponível)
- Volume alto / campanha → use plataformas BSP certificadas

## Pré-requisitos

1. Conta Meta Business → [developers.facebook.com](https://developers.facebook.com)
2. App WhatsApp Business → obter `PHONE_NUMBER_ID` e token permanente
3. Número de telefone verificado

## Configuração

```python
import os, requests

WA_TOKEN      = os.environ.get("WHATSAPP_TOKEN", "")       # token permanente
WA_PHONE_ID   = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")  # ex: "123456789"
WA_API_VER    = "v19.0"
WA_BASE       = f"https://graph.facebook.com/{WA_API_VER}/{WA_PHONE_ID}"
WA_HEADERS    = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
```

## Enviar mensagem de texto

```python
def wa_send_text(numero: str, mensagem: str) -> dict:
    """
    numero: E.164 sem '+', ex: "5511999990000"
    """
    body = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"preview_url": False, "body": mensagem},
    }
    r = requests.post(f"{WA_BASE}/messages", headers=WA_HEADERS, json=body, timeout=15)
    return r.json()

resp = wa_send_text("5511999990000", "Olá! Sua reserva foi confirmada ✅")
print(resp)
```

## Enviar template aprovado

```python
def wa_send_template(numero: str, template_name: str, lang: str = "pt_BR",
                     params: list[str] | None = None) -> dict:
    components = []
    if params:
        components = [{"type": "body",
                        "parameters": [{"type": "text", "text": p} for p in params]}]
    body = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang},
            "components": components,
        },
    }
    r = requests.post(f"{WA_BASE}/messages", headers=WA_HEADERS, json=body, timeout=15)
    return r.json()

# Template "hello_world" (padrão Meta para teste)
resp = wa_send_template("5511999990000", "hello_world", params=["João"])
```

## Enviar imagem/documento

```python
def wa_send_media(numero: str, tipo: str, url: str, caption: str = "") -> dict:
    """tipo: 'image', 'document', 'audio', 'video'"""
    body = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": tipo,
        tipo: {"link": url, "caption": caption},
    }
    r = requests.post(f"{WA_BASE}/messages", headers=WA_HEADERS, json=body, timeout=15)
    return r.json()

# Enviar PDF
wa_send_media("5511999990000", "document",
              "https://example.com/relatorio.pdf", caption="Relatório Março")
```

## Verificar status da mensagem (webhook)

```python
# Configurar webhook no Meta App Dashboard:
# URL: https://seu-servidor.com/whatsapp/webhook
# Verify Token: definido em WA_VERIFY_TOKEN

# Processar payload recebido:
def processar_webhook(payload: dict) -> str | None:
    entry = payload.get("entry", [{}])[0]
    changes = entry.get("changes", [{}])[0]
    value = changes.get("value", {})
    messages = value.get("messages", [])
    
    if not messages:
        return None
    
    msg = messages[0]
    numero = msg["from"]
    tipo = msg["type"]
    
    if tipo == "text":
        return msg["text"]["body"]
    elif tipo == "audio":
        audio_id = msg["audio"]["id"]
        return f"[Audio recebido: {audio_id}]"
    return None
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `WHATSAPP_TOKEN` | Token permanente ou de sistema |
| `WHATSAPP_PHONE_NUMBER_ID` | ID do número cadastrado na Meta |
| `WHATSAPP_VERIFY_TOKEN` | Token para verificação do webhook |
