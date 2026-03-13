+++
name = "calendar"
description = "Agenda eventos, lê calendário, cria lembretes via Google Calendar API ou iCal/CalDAV. Use when: user asks to agendar evento, listar agenda do dia/semana, criar lembrete, verificar disponibilidade, ou marcar reunião. NOT for: tarefas (use reminder/todo skill), email (use email skill)."
tags = ["calendário", "agenda", "evento", "reunião", "google calendar", "agendar", "lembrete", "horário", "compromisso", "disponibilidade"]
priority = "contextual"

[sif]
signature = "calendar.create(title: str, start: str, end: str, attendees: list = []) -> dict"
prompt_hint = "Use para agendar compromisso, bloquear horário, verificar agenda ou criar evento com convidados."
short_sig = "calendar.create(title,start,end)"
compose = ["email", "notion", "slack"]
examples_min = ["agendar reunião amanhã às 15h com convidados"]

[runtime]
estimated_cost = 0.75
risk_level = "medium"
side_effects = ["calendar_read", "calendar_write"]
postconditions = ["calendar_event_created_or_checked"]
fallback_policy = "draft_event_or_ask_user"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "calendar agenda meeting event reminder schedule availability invite"
example_queries = ["agende uma reunião", "veja minha agenda de amanhã"]

[requires]
bins = []
+++

# Calendar Skill

Google Calendar API e CalDAV via Python REPL.

## Quando usar

✅ **USE quando:**
- "Agenda reunião amanhã às 15h"
- "O que tenho na agenda hoje?"
- "Cria evento recorrente toda segunda"
- "Cancela o evento X"
- "Minha próxima semana está livre?"
- "Agenda lembrete para daqui 30 minutos"

❌ **NÃO use quando:**
- Lista de tarefas (TODO) → use remindes/tasks
- Email de convite apenas → use `email` skill

## Google Calendar API

```python
import os, requests, json
from datetime import datetime, timedelta, timezone

# Service Account JSON ou OAuth token
GCAL_CALENDAR_ID = os.environ.get("GCAL_CALENDAR_ID", "primary")

def gcal_headers() -> dict:
    """Retorna headers com token OAuth (via google-auth ou token direto)."""
    token = os.environ.get("GCAL_ACCESS_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

BASE = "https://www.googleapis.com/calendar/v3"
cal_id = GCAL_CALENDAR_ID

# Listar próximos eventos
def listar_eventos(dias: int = 7) -> list[dict]:
    agora = datetime.now(timezone.utc).isoformat()
    fim = (datetime.now(timezone.utc) + timedelta(days=dias)).isoformat()
    r = requests.get(
        f"{BASE}/calendars/{cal_id}/events",
        headers=gcal_headers(),
        params={
            "timeMin": agora,
            "timeMax": fim,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 20,
        },
        timeout=15,
    )
    eventos = r.json().get("items", [])
    return [
        {
            "id": e["id"],
            "titulo": e.get("summary", "Sem título"),
            "inicio": e["start"].get("dateTime", e["start"].get("date")),
            "fim": e["end"].get("dateTime", e["end"].get("date")),
            "descricao": e.get("description", ""),
        }
        for e in eventos
    ]

agenda = listar_eventos(dias=7)
for e in agenda:
    print(f"{e['inicio']} — {e['titulo']}")
```

## Criar evento

```python
def criar_evento(
    titulo: str,
    inicio: str,           # "2026-03-07T15:00:00-03:00"
    fim: str,              # "2026-03-07T16:00:00-03:00"
    descricao: str = "",
    convidados: list[str] | None = None,
    lembrete_min: int = 30,
) -> dict:
    body = {
        "summary": titulo,
        "description": descricao,
        "start": {"dateTime": inicio, "timeZone": "America/Sao_Paulo"},
        "end": {"dateTime": fim, "timeZone": "America/Sao_Paulo"},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": lembrete_min}]},
    }
    if convidados:
        body["attendees"] = [{"email": e} for e in convidados]

    r = requests.post(f"{BASE}/calendars/{cal_id}/events",
                       headers=gcal_headers(), json=body, timeout=15)
    evento = r.json()
    return {"id": evento["id"], "link": evento.get("htmlLink", ""), "titulo": titulo}

novo = criar_evento(
    titulo="Reunião RLM",
    inicio="2026-03-10T14:00:00-03:00",
    fim="2026-03-10T15:00:00-03:00",
    descricao="Revisão de features",
    convidados=["colega@example.com"],
)
FINAL_VAR("novo")
```

## Lidar com fuso horário brasileiro

```python
from datetime import datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3))  # Brasília

def agora_brt() -> str:
    return datetime.now(BRT).isoformat()

def amanha_brt(hora: int = 9, minuto: int = 0) -> str:
    amanha = datetime.now(BRT) + timedelta(days=1)
    return amanha.replace(hour=hora, minute=minuto, second=0, microsecond=0).isoformat()

inicio = amanha_brt(hora=15)
fim = amanha_brt(hora=16)
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `GCAL_ACCESS_TOKEN` | Token OAuth2 do Google Calendar |
| `GCAL_CALENDAR_ID` | ID do calendário (`primary` ou email específico) |

Para obter token: Google Cloud Console → APIs & Services → Credenciais → OAuth 2.0.
Scopes necessários: `https://www.googleapis.com/auth/calendar`
