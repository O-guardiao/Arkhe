+++
name = "voice"
description = "Fazer e receber ligações telefônicas via Twilio, Telnyx, ou ElevenLabs TTS. Use when: user asks to fazer ligação, deixar recado gravado, enviar SMS, ou sintetizar voz. NOT for: mensagens WhatsApp (use whatsapp skill), Telegram áudio (use telegram_bot skill), conferências web (Zoom/Meet)."
tags = ["ligação", "telefone", "sms", "sintetizar voz", "tts", "twilio", "voz", "ligar", "recado", "elevenlabs"]
priority = "lazy"

[requires]
bins = []

[sif]
signature = "voice.call(to: str, message: str) -> dict"
prompt_hint = "Use para ligação, SMS, recado automatizado ou síntese de voz para contato telefônico."
short_sig = "voice.call(to,msg)→{}"
compose = ["whatsapp", "email", "telegram_bot"]
examples_min = ["fazer ligação automática ou enviar SMS com mensagem"]

[runtime]
estimated_cost = 1.0
risk_level = "high"
side_effects = ["phone_call", "sms_send", "tts_generation"]
postconditions = ["voice_or_sms_action_attempted"]
fallback_policy = "use_whatsapp_or_email"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "voice call phone sms twilio tts telephone notification"
example_queries = ["faça uma ligação", "envie um SMS"]
+++

# Voice Skill

Ligações telefônicas e SMS via Twilio REST API + TTS com ElevenLabs/OpenAI.

## Quando usar

✅ **USE quando:**
- "Faz uma ligação para +55..."
- "Envia um SMS para..."
- "Liga para o restaurante e faz reserva"
- "Deixa recado gravado para..."
- "Lê este texto em voz alta para o usuário"

❌ **NÃO use quando:**
- WhatsApp → use `whatsapp` skill
- Telegram → use `telegram_bot` skill
- Conferência web → use APIs Zoom/Teams/Meet

## Configuração Twilio

```python
import os, requests
from requests.auth import HTTPBasicAuth

TWILIO_SID    = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM   = os.environ.get("TWILIO_FROM_NUMBER", "")  # ex: "+15005550006"
TWILIO_BASE   = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}"
TWILIO_AUTH   = HTTPBasicAuth(TWILIO_SID, TWILIO_TOKEN)
```

## Enviar SMS

```python
def sms(para: str, mensagem: str) -> dict:
    r = requests.post(
        f"{TWILIO_BASE}/Messages.json",
        auth=TWILIO_AUTH,
        data={"From": TWILIO_FROM, "To": para, "Body": mensagem},
        timeout=15,
    )
    return r.json()

resultado = sms("+5511999990000", "Sua reserva foi confirmada para amanhã às 20h!")
print(resultado["status"])  # queued, sent, delivered
```

## Fazer ligação com TwiML (texto lido por robô)

```python
def ligar(para: str, mensagem: str, voz: str = "Polly.Camila") -> dict:
    """
    para: número E.164 ex: "+5511999990000"
    mensagem: texto que será lido na ligação
    voz: Amazon Polly voice — Camila/Vitória para PT-BR
    """
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="{voz}" language="pt-BR">{mensagem}</Say>
  <Pause length="1"/>
  <Say voice="{voz}" language="pt-BR">Esta mensagem terminará em 3, 2, 1.</Say>
</Response>"""
    
    r = requests.post(
        f"{TWILIO_BASE}/Calls.json",
        auth=TWILIO_AUTH,
        data={
            "From": TWILIO_FROM,
            "To": para,
            "Twiml": twiml,
        },
        timeout=20,
    )
    resultado = r.json()
    return {"call_sid": resultado.get("sid"), "status": resultado.get("status")}

chamada = ligar(
    "+5511999990000",
    "Olá! Este é um lembrete automático da sua consulta amanhã às 14 horas."
)
FINAL_VAR("chamada")
```

## Verificar status de chamada

```python
def status_chamada(call_sid: str) -> dict:
    r = requests.get(
        f"{TWILIO_BASE}/Calls/{call_sid}.json",
        auth=TWILIO_AUTH,
        timeout=10,
    )
    c = r.json()
    return {"status": c["status"], "duracao_s": c.get("duration"), "para": c["to"]}

# status possíveis: queued, initiated, ringing, in-progress, completed, failed
```

## TTS com OpenAI (gerar áudio MP3)

```python
import os, requests

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

def tts_openai(texto: str, voz: str = "nova", destino: str = "/tmp/audio.mp3") -> str:
    """Gera arquivo MP3 de um texto usando OpenAI TTS."""
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={"model": "tts-1", "voice": voz, "input": texto},
        timeout=30,
    )
    r.raise_for_status()
    with open(destino, "wb") as f:
        f.write(r.content)
    return destino

audio_path = tts_openai("Bom dia! Aqui está seu resumo diário.", destino="/tmp/resumo.mp3")
print(f"Áudio gerado: {audio_path}")
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `TWILIO_ACCOUNT_SID` | SID da conta Twilio |
| `TWILIO_AUTH_TOKEN` | Token de autenticação |
| `TWILIO_FROM_NUMBER` | Número comprado na Twilio |
