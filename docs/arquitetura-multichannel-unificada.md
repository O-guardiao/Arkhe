# Arquitetura Multichannel Unificada — RLM-Main

> **Data:** Julho 2025  
> **Status:** Plano arquitetural completo — referência para implementação  
> **Contexto:** O RLM precisa de roteamento cross-channel (WhatsApp→Telegram), controle IoT (AC, câmeras) via qualquer canal, e entrega assíncrona confiável  
> **Pré-requisito:** Documento `arquitetura-config-multidevice.md` (perfis, JWT, rlm.toml) — NÃO implementado ainda

---

## Índice

1. [Problema que resolve](#1-problema-que-resolve)
2. [Estado atual — inventário técnico](#2-estado-atual--inventário-técnico)
3. [Gaps críticos identificados](#3-gaps-críticos-identificados)
4. [Padrões de mercado absorvidos](#4-padrões-de-mercado-absorvidos)
5. [Arquitetura proposta — MessageBus](#5-arquitetura-proposta--messagebus)
6. [Modelo de dados — Envelope universal](#6-modelo-de-dados--envelope-universal)
7. [Router cross-channel](#7-router-cross-channel)
8. [Fila de entrega assíncrona (Outbox)](#8-fila-de-entrega-assíncrona-outbox)
9. [Adapter IoT — MQTT Bridge](#9-adapter-iot--mqtt-bridge)
10. [Adapter Media — processamento de mídia inbound](#10-adapter-media--processamento-de-mídia-inbound)
11. [Linguagem e stack — decisão](#11-linguagem-e-stack--decisão)
12. [O que precisa ser refatorado](#12-o-que-precisa-ser-refatorado)
13. [O que precisa ser criado do zero](#13-o-que-precisa-ser-criado-do-zero)
14. [Plano de migração em 6 fases](#14-plano-de-migração-em-6-fases)
15. [Diagramas de fluxo](#15-diagramas-de-fluxo)
16. [Riscos e mitigações](#16-riscos-e-mitigações)
17. [Glossário](#17-glossário)

---

## 1. Problema que Resolve

### Cenários que hoje são impossíveis

| Cenário | Por que não funciona hoje |
|---|---|
| Mandar msg pelo WhatsApp → RLM responde no Telegram | `reply()` despacha de volta ao canal de origem. Sem API cross-channel. |
| ESP32 envia temperatura → RLM decide ligar AC via MQTT | Não existe adapter MQTT. ChannelRegistry só tem canais de mensagem. |
| Câmera detecta movimento → RLM avisa no Telegram | Não existe ingest de eventos IoT. Câmera não tem client_id. |
| Mensagem chega no WebChat → `reply("webchat:...")` funciona | WebChat não registra adapter no ChannelRegistry. reply() falha silenciosamente. |
| Retry automático quando Telegram cai | ChannelRegistry.reply() é fire-and-forget. Sem fila, sem retry, sem DLQ. |
| Enviar imagem recebida no WhatsApp para processamento | Gateway descreve mídia como texto (`[IMAGE recebido] media_id=...`). Não baixa. |

### O que o usuário quer

```
                           ┌─────────────────────────────────┐
  WhatsApp ──────────────→│                                   │──────────→ Telegram
  Telegram ──────────────→│         RLM MessageBus            │──────────→ WhatsApp
  Discord  ──────────────→│    (sabe origem, sabe destino,    │──────────→ MQTT (ESP32)
  ESP32    ──MQTT────────→│     decide roteamento)            │──────────→ Slack
  Câmera   ──webhook─────→│                                   │──────────→ WebChat
                           └─────────────────────────────────┘
```

---

## 2. Estado Atual — Inventário Técnico

### Canais operacionais (7)

| Canal | Arquivo | Tipo | client_id | Registro no ChannelRegistry |
|---|---|---|---|---|
| Telegram | `telegram_gateway.py` (576 linhas) | Bridge (daemon thread, long-poll → HTTP POST) | `telegram:{chat_id}` | ✅ via `TelegramAdapter` em `plugins/telegram.py` |
| WhatsApp | `whatsapp_gateway.py` (260 linhas) | Router (FastAPI sub-app, webhook) | `whatsapp:{wa_id}` | ✅ via `WhatsAppAdapter` |
| Discord | `discord_gateway.py` (247 linhas) | Router (FastAPI, interactions) | `discord:{guild}:{user}` | ✅ via `DiscordAdapter` |
| Slack | `slack_gateway.py` (246 linhas) | Router (FastAPI, Events API) | `slack:{team}:{channel}` | ✅ via `SlackAdapter` |
| WebChat | `webchat.py` (224 linhas) | Router (FastAPI, WebSocket) | `webchat:{uuid}` | ❌ **NÃO registrado** |
| TUI | `tui.py` | Direto (processo local) | `tui:local` | N/A (local) |
| Webhook | `webhook_dispatch.py` (316 linhas) | Router (FastAPI, external) | `hook:{custom_id}` | ❌ **NÃO registrado** |

### Infraestrutura de suporte

| Componente | Arquivo | Status |
|---|---|---|
| `ChannelAdapter` (ABC) | `plugins/channel_registry.py` | ✅ Funcional, 5 adapters |
| `ChannelRegistry` (singleton) | `plugins/channel_registry.py` | ✅ Funcional, síncrono |
| `SessionManager` | `core/session/_impl.py` | ✅ 3 scopes (main/per-user/per-channel) |
| `delivery_context` | campo em `SessionRecord` | ✅ Persistido no SQLite |
| `EventBus` | `RLMEventBus` em `api.py` lifespan | ✅ Instanciado, pouco usado |
| `HookSystem` | Injetado no `SessionManager` | ✅ Hooks de ciclo de vida |
| `AgentContext.channel` | `runtime_workbench.py` | ✅ Propaga canal em metadata de memória |

### Fluxo atual de mensagem (Telegram como exemplo)

```
Telegram API
     │ long-poll
     ▼
TelegramGateway._get_updates()
     │ rate limit + ACL
     ▼
_bridge_post(api_base_url, "telegram:12345", payload)
     │ HTTP POST /webhook/telegram:12345 + auth headers
     ▼
api.py → dispatch_runtime_prompt_sync(session, prompt)
     │ run_in_executor (thread pool)
     ▼
RLM.completion() → resultado
     │
     ▼
HTTP Response ← JSON {response, tokens, ...}
     │
     ▼
TelegramGateway._send_message(token, chat_id, texto)
     │ Telegram Bot API
     ▼
Usuário vê resposta no Telegram
```

**Problema:** A resposta volta pelo **mesmo caminho HTTP** que a request. Não existe mecanismo para o RLM decidir: "esta resposta deve ir para um canal diferente".

---

## 3. Gaps Críticos Identificados

### Gap 1 — Sem roteamento cross-channel

O `ChannelRegistry.reply(client_id, msg)` aceita qualquer `client_id` e despacha para o adapter correto. **Em teoria**, chamar `reply("telegram:999", "msg")` de dentro de uma sessão que veio do WhatsApp já funciona. Mas:

- O agente não tem uma API para decidir "responda em outro canal"
- O `delivery_context` guarda apenas o canal de origem
- Não existe conceito de "destinos múltiplos" ou "canal preferido do usuário"

### Gap 2 — Sem fila de entrega

`reply()` é síncrono e fire-and-forget. Se o Telegram estiver fora, a mensagem se perde. Não há:
- Retry com backoff exponencial
- Dead Letter Queue (DLQ) para mensagens que falharam N vezes
- Confirmação de entrega (delivery receipt)
- Ordering guarantees para mensagens longas quebradas em chunks

### Gap 3 — Sem adapter IoT/MQTT

O `ChannelRegistry` foi projetado para **canais de mensagem humanos**. IoT devices comunicam via MQTT com tópicos hierárquicos (`casa/sala/temperatura`, `casa/sala/ac/comando`). Não existe:
- Adapter MQTT que publique/subscreva em tópicos
- Mapeamento `device_id → client_id` para sessão
- Canal bidirecional para comandos (RLM → dispositivo) e telemetria (dispositivo → RLM)

### Gap 4 — WebChat desconectado

O `webchat.py` é um dos canais mais completos (acesso direto ao SessionManager, comandos de operador), mas **não registra adapter** no ChannelRegistry. Resultado: se o agente executa `reply("webchat:abc123", msg)`, falha silenciosamente.

### Gap 5 — Mídia inbound não processada

WhatsApp recebe imagens, áudio, vídeos — mas o gateway converte tudo em texto descritivo:
```
"[IMAGE recebido] media_id=123456"
```
O arquivo não é baixado, não é armazenado, não é processado.

### Gap 6 — Sem observabilidade de entrega

Não existe métrica ou log estruturado de:
- Mensagens enviadas vs. entregues vs. falhadas
- Latência por canal
- Taxa de retry por adapter

---

## 4. Padrões de Mercado Absorvidos

### 4.1 Microsoft Bot Framework — Activity/Adapter/Turn

**O que absorvemos:** O conceito de **Activity** como envelope universal com campos de roteamento (`channelId`, `from`, `recipient`, `replyToId`). O padrão **Adapter** que normaliza cada plataforma em um contrato comum. O **TurnContext** que carrega toda a informação necessária para processar e responder.

**O que não absorvemos:** A complexidade do middleware pipeline. Para o RLM, o HookSystem + EventBus já cumprem esse papel.

### 4.2 Matrix Protocol — Eventos e Bridging

**O que absorvemos:** O conceito de **Application Service (AS)** como bridge bidirecional entre protocolos. Matrix trata tudo como **evento tipado** em um DAG — isso inspira o envelope universal do MessageBus. O conceito de **rooms** como espaço de conversação que abstrai canais.

**O que não absorvemos:** Federation entre servidores. O RLM é single-server.

### 4.3 NATS — Pub/Sub para IoT

**O que absorvemos:** O modelo **publish/subscribe com tópicos hierárquicos** para IoT. NATS demonstra que devices edge podem subscrever a tópicos granulares (`casa.sala.ac.comando`) e o servidor publica neles. Latência sub-milissegundo.

**Decisão:** Não usar NATS como dependência. Implementar um **MQTT bridge adapter** que conecta ao broker MQTT existente (Mosquitto). O RLM publica/subscreve em tópicos MQTT, o broker distribui para devices.

### 4.4 Enterprise Integration Patterns (EIP)

**O que absorvemos:**
- **Message Router:** O MessageBus roteia com base no `delivery_target` do envelope
- **Content-Based Router:** Regras que inspecionam o conteúdo e decidem destino (ex: `"ligar ac"` → MQTT)
- **Channel Adapter:** O ChannelAdapter existente já implementa este padrão
- **Dead Letter Channel:** Mensagens que falharam N vezes vão para DLQ para inspeção manual

---

## 5. Arquitetura Proposta — MessageBus

### Princípio central

> **Todo fluxo de mensagem passa por um ponto único de roteamento (MessageBus) que sabe a origem, decide o destino, e garante entrega.**

### Diagrama da arquitetura

```
     ┌──────────────────────────────────────────────────────────────────────┐
     │                          INBOUND (Normalização)                      │
     │                                                                      │
     │  Telegram ──→ TelegramIngest ──┐                                    │
     │  WhatsApp ──→ WhatsAppIngest ──┤                                    │
     │  Discord  ──→ DiscordIngest  ──┤                                    │
     │  Slack    ──→ SlackIngest    ──┤──→  MessageBus.ingest(Envelope)    │
     │  WebChat  ──→ WebChatIngest  ──┤                                    │
     │  Webhook  ──→ WebhookIngest  ──┤                                    │
     │  MQTT     ──→ MQTTIngest     ──┘                                    │
     │                                                                      │
     └────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
     ┌──────────────────────────────────────────────────────────────────────┐
     │                         MessageBus (Core)                            │
     │                                                                      │
     │  1. Normaliza Envelope                                              │
     │  2. Resolve sessão (SessionManager)                                 │
     │  3. Executa RLM se necessário                                       │
     │  4. Aplica RoutingPolicy (decide destino)                           │
     │  5. Enfileira no Outbox                                             │
     │  6. Emite evento no EventBus                                        │
     │                                                                      │
     └────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
     ┌──────────────────────────────────────────────────────────────────────┐
     │                         OUTBOUND (Entrega)                           │
     │                                                                      │
     │  Outbox (SQLite) ──→ DeliveryWorker (asyncio task)                  │
     │                          │                                          │
     │                          ├──→ TelegramAdapter.send()                │
     │                          ├──→ WhatsAppAdapter.send()                │
     │                          ├──→ DiscordAdapter.send()                 │
     │                          ├──→ SlackAdapter.send()                   │
     │                          ├──→ WebChatAdapter.send()                 │
     │                          ├──→ WebhookAdapter.send()                 │
     │                          ├──→ MQTTAdapter.publish()                 │
     │                          └──→ DLQ (após N falhas)                   │
     │                                                                      │
     └──────────────────────────────────────────────────────────────────────┘
```

### Componentes novos

| Componente | Responsabilidade | Tipo |
|---|---|---|
| `MessageBus` | Ponto central de ingest + routing + enqueue | Classe singleton |
| `Envelope` | Modelo de dados universal para toda mensagem | Dataclass |
| `RoutingPolicy` | Decide destino(s) da resposta | Strategy pattern |
| `Outbox` | Fila persistente de mensagens a entregar | SQLite tabela |
| `DeliveryWorker` | Loop assíncrono que drena o Outbox | asyncio.Task |
| `MQTTAdapter` | Bridge bidirecional com broker MQTT | ChannelAdapter impl |
| `WebChatAdapter` | Adapter ausente para webchat | ChannelAdapter impl |
| `MediaProcessor` | Baixa e armazena mídia inbound | Serviço auxiliar |

---

## 6. Modelo de Dados — Envelope Universal

### Por que um envelope

Hoje cada gateway monta seu payload de forma diferente. O Telegram manda `{text, from_user, chat_id}`, o WhatsApp manda `{type, text, wa_id, media_id}`, o Discord manda interaction data. O RLM precisa de um **formato canônico** que carregue toda informação necessária para roteamento e processamento.

### Schema do Envelope

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
import uuid


class MessageType(Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    LOCATION = "location"
    COMMAND = "command"          # /help, /status, etc.
    EVENT = "event"             # IoT telemetry, sensor readings
    ACTION = "action"           # IoT command (ligar AC, abrir porta)
    SYSTEM = "system"           # internal routing, heartbeats


class Direction(Enum):
    INBOUND = "inbound"         # canal → RLM
    OUTBOUND = "outbound"       # RLM → canal
    INTERNAL = "internal"       # RLM → RLM (cross-channel routing)


@dataclass
class Envelope:
    """
    Unidade atômica do MessageBus.
    Toda mensagem — de qualquer canal, em qualquer direção — é um Envelope.
    """
    # ── Identificação ────────────────────────────
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str | None = None       # liga request→response
    reply_to_id: str | None = None          # para threads/replies

    # ── Roteamento ───────────────────────────────
    source_channel: str = ""                # "telegram", "whatsapp", "mqtt"
    source_id: str = ""                     # chat_id, wa_id, device_id
    source_client_id: str = ""              # "telegram:12345" (formato ChannelRegistry)

    target_channel: str | None = None       # canal destino (se cross-channel)
    target_id: str | None = None            # id no canal destino
    target_client_id: str | None = None     # "telegram:999" (formato ChannelRegistry)

    # ── Conteúdo ─────────────────────────────────
    direction: Direction = Direction.INBOUND
    message_type: MessageType = MessageType.TEXT
    text: str = ""
    media_url: str | None = None            # URL ou path local do arquivo
    media_mime: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    #   metadata pode conter:
    #     - "from_user": nome do remetente
    #     - "location": {"lat": -23.5, "lon": -46.6}
    #     - "sensor_data": {"temperature": 27.3, "humidity": 65}
    #     - "device_type": "esp32"
    #     - "media_id": "whatsapp_media_abc123"

    # ── Temporal ─────────────────────────────────
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # ── Entrega ──────────────────────────────────
    delivery_attempts: int = 0
    max_retries: int = 3
    priority: int = 0                       # 0=normal, 1=alta, -1=baixa

    # ── Conveniência ─────────────────────────────
    @property
    def client_id(self) -> str:
        """Formato ChannelRegistry: 'canal:id'"""
        return self.source_client_id or f"{self.source_channel}:{self.source_id}"

    @property
    def delivery_target(self) -> str:
        """client_id do destino (cross-channel ou echo-back)."""
        return self.target_client_id or self.source_client_id

    def reply(self, text: str, **overrides) -> "Envelope":
        """Cria envelope de resposta invertendo source↔target."""
        return Envelope(
            correlation_id=self.id,
            source_channel="rlm",
            source_id="system",
            source_client_id="rlm:system",
            target_channel=self.source_channel,
            target_id=self.source_id,
            target_client_id=self.source_client_id,
            direction=Direction.OUTBOUND,
            message_type=MessageType.TEXT,
            text=text,
            priority=self.priority,
            **overrides,
        )
```

### Exemplos de Envelopes

**WhatsApp inbound (texto normal):**
```python
Envelope(
    source_channel="whatsapp",
    source_id="5511999887766",
    source_client_id="whatsapp:5511999887766",
    direction=Direction.INBOUND,
    message_type=MessageType.TEXT,
    text="Qual a temperatura da sala?",
    metadata={"from_user": "Demet"},
)
```

**RLM responde no Telegram (cross-channel):**
```python
Envelope(
    correlation_id="<id do envelope acima>",
    source_channel="rlm",
    source_id="system",
    target_channel="telegram",
    target_id="12345",
    target_client_id="telegram:12345",
    direction=Direction.OUTBOUND,
    message_type=MessageType.TEXT,
    text="Sala: 27.3°C, umidade 65%",
)
```

**ESP32 envia telemetria (IoT event):**
```python
Envelope(
    source_channel="mqtt",
    source_id="esp32-sala",
    source_client_id="mqtt:esp32-sala",
    direction=Direction.INBOUND,
    message_type=MessageType.EVENT,
    text="",
    metadata={
        "sensor_data": {"temperature": 27.3, "humidity": 65},
        "device_type": "esp32",
        "location": "sala",
    },
)
```

**RLM comanda AC (IoT action):**
```python
Envelope(
    source_channel="rlm",
    source_id="system",
    target_channel="mqtt",
    target_id="esp32-sala",
    target_client_id="mqtt:esp32-sala",
    direction=Direction.OUTBOUND,
    message_type=MessageType.ACTION,
    text="",
    metadata={
        "command": "ac_set",
        "payload": {"temperature": 23, "mode": "cool"},
        "mqtt_topic": "casa/sala/ac/comando",
    },
)
```

---

## 7. Router Cross-Channel

### Como o RLM decide o destino

O `RoutingPolicy` é uma chain of responsibility que, dado um envelope de resposta, decide os destinos:

```python
from typing import Protocol


class RoutingRule(Protocol):
    def evaluate(self, inbound: Envelope, response_text: str, session: "SessionRecord") -> list[Envelope]:
        """Retorna envelopes de saída (0 ou mais)."""
        ...


class EchoBackRule:
    """Default: responde no mesmo canal que perguntou."""

    def evaluate(self, inbound, response_text, session):
        return [inbound.reply(response_text)]


class UserPreferenceRule:
    """
    Se o usuário configurou canal preferido, redireciona.
    Lê de session.metadata["preferred_channel"] ou clients.metadata.
    """

    def evaluate(self, inbound, response_text, session):
        pref = (session.metadata or {}).get("preferred_channel")
        if not pref:
            return []  # sem preferência, próxima regra decide

        # pref = "telegram:12345"
        target_channel, target_id = pref.split(":", 1)
        return [Envelope(
            correlation_id=inbound.id,
            source_channel="rlm",
            source_id="system",
            target_channel=target_channel,
            target_id=target_id,
            target_client_id=pref,
            direction=Direction.OUTBOUND,
            message_type=MessageType.TEXT,
            text=response_text,
        )]


class BroadcastRule:
    """
    Se session.metadata["broadcast_channels"] existe, envia para todos.
    Útil para alertas IoT: temperatura alta → avisa Telegram + Slack.
    """

    def evaluate(self, inbound, response_text, session):
        channels = (session.metadata or {}).get("broadcast_channels", [])
        envelopes = []
        for client_id in channels:
            ch, tid = client_id.split(":", 1)
            envelopes.append(Envelope(
                correlation_id=inbound.id,
                source_channel="rlm",
                source_id="system",
                target_channel=ch,
                target_id=tid,
                target_client_id=client_id,
                direction=Direction.OUTBOUND,
                text=response_text,
            ))
        return envelopes


class AgentDirectiveRule:
    """
    Se a resposta do agente contém diretiva de roteamento explícita.
    O agente pode emitir um bloco especial:

        @@route:telegram:12345@@
        Mensagem a enviar

    Ou chamar skill: cross_channel_send("telegram:12345", "msg")
    """

    DIRECTIVE_PATTERN = r"@@route:(\w+:\S+)@@\s*"

    def evaluate(self, inbound, response_text, session):
        import re
        match = re.search(self.DIRECTIVE_PATTERN, response_text)
        if not match:
            return []
        target = match.group(1)
        clean_text = re.sub(self.DIRECTIVE_PATTERN, "", response_text).strip()
        ch, tid = target.split(":", 1)
        return [Envelope(
            correlation_id=inbound.id,
            source_channel="rlm",
            source_id="system",
            target_channel=ch,
            target_id=tid,
            target_client_id=target,
            direction=Direction.OUTBOUND,
            text=clean_text,
        )]


class RoutingPolicy:
    """
    Executa regras em ordem de prioridade.
    A primeira regra que produz envelopes vence.
    Se nenhuma produz, EchoBack é o fallback.
    """

    def __init__(self):
        self.rules: list[RoutingRule] = [
            AgentDirectiveRule(),    # 1. Agente mandou explicitamente
            BroadcastRule(),         # 2. Broadcast configurado
            UserPreferenceRule(),    # 3. Preferência do usuário
            EchoBackRule(),          # 4. Fallback: volta ao canal de origem
        ]

    def route(self, inbound: Envelope, response_text: str, session) -> list[Envelope]:
        for rule in self.rules:
            envelopes = rule.evaluate(inbound, response_text, session)
            if envelopes:
                return envelopes
        # Nunca deveria chegar aqui (EchoBack sempre retorna), mas defensivo:
        return [inbound.reply(response_text)]
```

### Exposição para o agente via skill

O agente ganha uma nova skill `cross_channel_send` que o RLM torna disponível no ambiente de execução:

```python
# rlm/skills/cross_channel_send.py

def cross_channel_send(target_client_id: str, message: str) -> str:
    """
    Envia uma mensagem para um canal/destino específico.

    Args:
        target_client_id: Formato "canal:id". Ex: "telegram:12345", "mqtt:esp32-sala", "slack:T01:C02"
        message: Texto ou JSON da mensagem.

    Returns:
        "ok" se enfileirado com sucesso, "error: <motivo>" caso contrário.

    Exemplos:
        cross_channel_send("telegram:12345", "Alerta: temperatura acima de 30°C!")
        cross_channel_send("mqtt:esp32-sala", '{"command":"ac_on","temp":23}')
    """
    from rlm.comms.message_bus import get_message_bus
    bus = get_message_bus()
    envelope = Envelope(
        source_channel="rlm",
        source_id="agent",
        target_client_id=target_client_id,
        direction=Direction.OUTBOUND,
        message_type=MessageType.TEXT,
        text=message,
    )
    bus.enqueue_outbound(envelope)
    return "ok"
```

---

## 8. Fila de Entrega Assíncrona (Outbox)

### Por que Outbox pattern

O **Transactional Outbox** é um padrão bem estabelecido: em vez de enviar a mensagem diretamente (e perder se falhar), persiste no SQLite na mesma transação que o processamento. Um worker assíncrono drena a fila depois.

### Schema SQLite

```sql
-- Adicionar ao rlm_sessions.db (migração incremental)
CREATE TABLE IF NOT EXISTS outbox (
    id              TEXT PRIMARY KEY,       -- UUID do envelope
    correlation_id  TEXT,                   -- liga request↔response
    target_channel  TEXT NOT NULL,          -- "telegram", "mqtt", "slack"
    target_id       TEXT NOT NULL,          -- chat_id, device_id, etc.
    target_client_id TEXT NOT NULL,         -- "telegram:12345"
    message_type    TEXT DEFAULT 'text',    -- text, image, action, etc.
    payload         TEXT NOT NULL,          -- JSON: {text, media_url, metadata, ...}
    priority        INTEGER DEFAULT 0,      -- -1=low, 0=normal, 1=high
    status          TEXT DEFAULT 'pending', -- pending | delivering | delivered | failed | dlq
    attempts        INTEGER DEFAULT 0,
    max_retries     INTEGER DEFAULT 3,
    next_attempt_at TEXT,                   -- datetime para backoff exponencial
    created_at      TEXT NOT NULL,
    delivered_at    TEXT,
    last_error      TEXT DEFAULT '',
    session_id      TEXT                    -- referência à sessão de origem
);

CREATE INDEX IF NOT EXISTS idx_outbox_status_next ON outbox(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_outbox_target ON outbox(target_client_id);
CREATE INDEX IF NOT EXISTS idx_outbox_correlation ON outbox(correlation_id);
```

### DeliveryWorker

```python
# rlm/comms/delivery_worker.py (pseudocódigo de design)

import asyncio
from datetime import datetime, timedelta


class DeliveryWorker:
    """
    Loop assíncrono que drena o outbox e entrega via ChannelRegistry.
    Roda como asyncio.Task no lifespan do FastAPI.
    """

    POLL_INTERVAL = 0.5       # segundos entre polls
    BACKOFF_BASE = 2          # segundos, exponencial: 2, 4, 8, 16...
    MAX_BACKOFF = 300          # 5 minutos máximo entre retries

    def __init__(self, db_path: str, channel_registry, event_bus=None):
        self.db_path = db_path
        self.registry = channel_registry
        self.event_bus = event_bus
        self._running = False

    async def start(self):
        """Inicia o loop de entrega como task assíncrono."""
        self._running = True
        while self._running:
            delivered = await self._drain_batch()
            if not delivered:
                await asyncio.sleep(self.POLL_INTERVAL)

    async def _drain_batch(self, batch_size: int = 20) -> int:
        """
        1. SELECT outbox WHERE status='pending' AND next_attempt_at <= now
           ORDER BY priority DESC, created_at ASC LIMIT batch_size
        2. Para cada: UPDATE status='delivering', tenta enviar via adapter
        3. Se sucesso: UPDATE status='delivered', delivered_at=now
        4. Se falha:
           a. attempts += 1
           b. Se attempts >= max_retries: UPDATE status='dlq'
           c. Senão: UPDATE status='pending', next_attempt_at = now + backoff
        """
        ...  # implementação futura

    def stop(self):
        self._running = False
```

### Métricas emitidas

O DeliveryWorker emite no EventBus:

| Evento | Payload |
|---|---|
| `delivery.sent` | `{envelope_id, target, channel, latency_ms}` |
| `delivery.failed` | `{envelope_id, target, channel, error, attempts}` |
| `delivery.dlq` | `{envelope_id, target, channel, total_attempts, last_error}` |
| `delivery.batch` | `{count, delivered, failed, avg_latency_ms}` |

---

## 9. Adapter IoT — MQTT Bridge

### Arquitetura do MQTT Adapter

```
     ┌─────────────┐          ┌──────────────┐         ┌────────────┐
     │  ESP32 sala  │──MQTT──→│  Mosquitto   │──MQTT──→│ MQTTAdapter │
     │  ESP32 jardim│←─MQTT──│  (broker)     │←─MQTT──│ (no RLM)   │
     │  Câmera IP   │         │  porta 1883  │         │            │
     └─────────────┘          └──────────────┘         └─────┬──────┘
                                                             │
                                                    MessageBus.ingest()
                                                             │
                                                             ▼
                                                     RLM processa
```

### Implementação

```python
# rlm/comms/mqtt_adapter.py (design)

import asyncio
import json
from aiomqtt import Client as MQTTClient    # pip install aiomqtt


class MQTTAdapter(ChannelAdapter):
    """
    Bridge bidirecional MQTT ↔ MessageBus.

    Subscreve a tópicos de telemetria (inbound):
      casa/+/sensores    → device publica leituras
      casa/+/eventos     → device publica alertas

    Publica em tópicos de comando (outbound):
      casa/{device}/comando  → RLM envia ações
      casa/{device}/config   → RLM envia configuração
    """

    def __init__(self, broker_host: str, broker_port: int = 1883,
                 subscribe_topics: list[str] | None = None):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.subscribe_topics = subscribe_topics or [
            "casa/+/sensores",
            "casa/+/eventos",
            "casa/+/status",
        ]

    async def connect_and_listen(self, message_bus):
        """
        Conecta ao broker MQTT e converte mensagens em Envelopes.
        Roda como asyncio.Task no lifespan.
        """
        async with MQTTClient(self.broker_host, self.broker_port) as client:
            for topic in self.subscribe_topics:
                await client.subscribe(topic)

            async for msg in client.messages:
                # Tópico: casa/sala/sensores → device_id="sala"
                parts = msg.topic.value.split("/")
                device_id = parts[1] if len(parts) >= 2 else "unknown"
                topic_type = parts[2] if len(parts) >= 3 else "data"

                try:
                    payload = json.loads(msg.payload.decode())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = {"raw": msg.payload.decode(errors="replace")}

                envelope = Envelope(
                    source_channel="mqtt",
                    source_id=device_id,
                    source_client_id=f"mqtt:{device_id}",
                    direction=Direction.INBOUND,
                    message_type=(
                        MessageType.EVENT if topic_type in ("sensores", "status")
                        else MessageType.TEXT
                    ),
                    text=json.dumps(payload) if isinstance(payload, dict) else str(payload),
                    metadata={
                        "mqtt_topic": msg.topic.value,
                        "device_id": device_id,
                        "sensor_data": payload if topic_type == "sensores" else None,
                    },
                )
                await message_bus.ingest(envelope)

    def send_message(self, target_id: str, text: str) -> bool:
        """Publica comando no tópico MQTT do device."""
        import asyncio
        topic = f"casa/{target_id}/comando"
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self._publish(topic, text))
            return True
        except Exception:
            return False

    def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
        """IoT devices geralmente não recebem mídia. Log e ignora."""
        return False

    async def _publish(self, topic: str, payload: str):
        async with MQTTClient(self.broker_host, self.broker_port) as client:
            await client.publish(topic, payload.encode())
```

### Tópicos MQTT propostos

| Tópico | Direção | Conteúdo | Exemplo |
|---|---|---|---|
| `casa/{device}/sensores` | Device → RLM | JSON com leituras | `{"temperature": 27.3, "humidity": 65}` |
| `casa/{device}/eventos` | Device → RLM | Alertas, mudanças de estado | `{"event": "motion_detected", "camera": "sala"}` |
| `casa/{device}/status` | Device → RLM | Heartbeat, online/offline | `{"status": "online", "uptime_s": 3600}` |
| `casa/{device}/comando` | RLM → Device | Ação a executar | `{"command": "ac_set", "temperature": 23}` |
| `casa/{device}/config` | RLM → Device | Configuração remota | `{"report_interval_s": 30}` |

### Cenário completo: "Qual a temperatura da sala?"

```
1. Usuário manda "Qual a temperatura da sala?" no WhatsApp

2. WhatsApp Gateway → Envelope inbound:
   source_channel="whatsapp", text="Qual a temperatura da sala?"

3. MessageBus.ingest() → SessionManager → RLM.completion()

4. Agente reconhece que precisa de dados IoT.
   Executa skill: mqtt_query("esp32-sala", "sensores")

5. RLM consulta telemetria recente em memória/cache (último Envelope EVENT do esp32-sala)
   Ou publica request no tópico: casa/sala/sensores/request

6. Resposta montada: "Sala: 27.3°C, umidade 65%"

7. RoutingPolicy → EchoBackRule → target_client_id="whatsapp:5511999887766"

8. Outbox enfileira → DeliveryWorker → WhatsAppAdapter.send_message()

9. Usuário recebe no WhatsApp: "Sala: 27.3°C, umidade 65%"
```

### Cenário completo: "Liga o ar-condicionado da sala em 23°C"

```
1. Usuário manda no Telegram: "Liga o ar da sala em 23"

2. Telegram Gateway → Envelope inbound → MessageBus → RLM.completion()

3. Agente interpreta intenção "ligar AC" e executa:
   cross_channel_send("mqtt:esp32-sala", '{"command":"ac_set","temperature":23}')

4. MessageBus enfileira no Outbox:
   target_channel="mqtt", target_id="esp32-sala"

5. DeliveryWorker → MQTTAdapter.send_message("esp32-sala", '{"command":"ac_set","temperature":23}')
   Publica em tópico: casa/sala/comando

6. ESP32 recebe, executa comando IR para o AC

7. ESP32 publica confirmação: casa/sala/eventos → {"event":"ac_set_ok","temperature":23}

8. MQTTAdapter subscritos → Envelope EVENT → MessageBus

9. Agente decide notificar usuário (ou RoutingPolicy com EchoBack para telegram):
   "AC da sala ligado em 23°C ✓"

10. DeliveryWorker → TelegramAdapter.send_message()
```

### Cenário completo: Câmera detecta movimento → alerta no Telegram

```
1. Câmera IP publica via webhook ou MQTT:
   casa/camera-sala/eventos → {"event":"motion_detected","confidence":0.92}

2. MQTTAdapter → Envelope EVENT → MessageBus.ingest()

3. MessageBus verifica: é um evento IoT, não uma pergunta.
   Consulta RoutingPolicy → BroadcastRule
   session.metadata["broadcast_channels"] = ["telegram:12345"]

4. Envelope outbound:
   target_client_id="telegram:12345",
   text="⚡ Movimento detectado na câmera da sala (confiança: 92%)"

5. Outbox → DeliveryWorker → TelegramAdapter → Telegram

6. Usuário recebe alerta instantâneo no Telegram
```

---

## 10. Adapter Media — Processamento de Mídia Inbound

### Problema atual

WhatsApp Gateway recebe `media_id` de imagens/áudio/vídeo mas não baixa:
```python
# whatsapp_gateway.py (estado atual)
rlm_text = f"[IMAGE recebido] media_id={media_id}"
```

### Solução: MediaProcessor

```python
# rlm/comms/media_processor.py (design)

import os
import httpx
from pathlib import Path


class MediaProcessor:
    """
    Baixa e armazena mídia inbound de qualquer canal.
    Armazena em ./rlm_media/{channel}/{date}/{uuid}.{ext}
    """

    MEDIA_ROOT = os.getenv("RLM_MEDIA_ROOT", "./rlm_media")
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

    async def process_whatsapp_media(self, media_id: str, mime_type: str) -> str | None:
        """
        Baixa mídia da Meta Cloud API.
        Retorna path local do arquivo, ou None se falhar.
        """
        token = os.environ.get("WHATSAPP_API_TOKEN")
        if not token:
            return None

        # 1. Obter URL do media
        url_resp = await self._get(
            f"https://graph.facebook.com/v19.0/{media_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if not url_resp or "url" not in url_resp:
            return None

        # 2. Baixar o arquivo
        media_url = url_resp["url"]
        return await self._download(media_url, mime_type, "whatsapp", token)

    async def process_telegram_media(self, file_id: str) -> str | None:
        """Baixa mídia da Telegram Bot API."""
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            return None

        # 1. getFile → file_path
        resp = await self._get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
        )
        if not resp or not resp.get("ok"):
            return None
        file_path = resp["result"]["file_path"]

        # 2. Baixar
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        ext = Path(file_path).suffix or ".bin"
        return await self._download(url, f"application/octet-stream", "telegram")

    async def _download(self, url: str, mime_type: str, channel: str,
                        auth_token: str | None = None) -> str | None:
        """Download genérico com limite de tamanho."""
        from datetime import date
        import uuid

        ext = self._mime_to_ext(mime_type)
        today = date.today().isoformat()
        dest_dir = Path(self.MEDIA_ROOT) / channel / today
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"{uuid.uuid4().hex}{ext}"

        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code != 200:
                    return None
                total = 0
                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(8192):
                        total += len(chunk)
                        if total > self.MAX_FILE_SIZE:
                            f.close()
                            dest_path.unlink(missing_ok=True)
                            return None
                        f.write(chunk)

        return str(dest_path)

    @staticmethod
    def _mime_to_ext(mime: str) -> str:
        mapping = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
            "video/mp4": ".mp4", "application/pdf": ".pdf",
        }
        return mapping.get(mime, ".bin")
```

---

## 11. Linguagem e Stack — Decisão

### Pergunta do usuário: "qual linguagem usar, o que vai precisar ser refatorado e migrado para outra linguagem?"

### Resposta objetiva

| Componente | Linguagem | Justificativa |
|---|---|---|
| **MessageBus, Envelope, RoutingPolicy** | Python | Core do RLM é Python. Não faz sentido reescrever. Performance não é gargalo — o bottleneck é I/O de rede (chamadas LLM, APIs de canal). |
| **Outbox + DeliveryWorker** | Python (asyncio) | SQLite + asyncio é mais que suficiente para a escala (dezenas de mensagens/segundo). |
| **MQTTAdapter** | Python (`aiomqtt`) | Lib madura, async-native, zero overhead de FFI. |
| **MediaProcessor** | Python (`httpx`) | Download de arquivos é I/O bound, não CPU bound. |
| **Gateways existentes** | Python | Já funcionam. Refatorar para injetar Envelope na entrada, não reescrever. |
| **Wire protocol (se escala IoT massiva)** | Rust (existente: `arkhe-wire`) | Já existe crate PyO3 para serialização rápida. Usar para encodar Envelopes em wire format se tráfego IoT justificar. |
| **Broker MQTT** | Mosquitto (C) | **Não escrever broker MQTT.** Usar Mosquitto (ou EMQX) como serviço externo. É um problema resolvido. |

### O que NÃO migrar para outra linguagem

O ecossistema Python do RLM (1700+ testes, skills, plugins, memory, MCTS, session) é estável demais para justificar reescrita. A adição do MessageBus é uma **camada sobre** o que existe, não uma substituição.

### Quando considerar Rust

Se no futuro o RLM precisar processar **>1000 mensagens/segundo** de IoT (centenas de ESP32 reportando a cada segundo), o hot path seria:

1. Parsear JSON do MQTT → Rust (`arkhe-wire`)
2. Encodar Envelope → Rust
3. Inserir no Outbox → Python (SQLite é o bottleneck real)

Isso é otimização para o futuro, não para agora. A prioridade é **funcionar correto** em Python puro, depois **medir**, depois otimizar se necessário.

---

## 12. O que Precisa Ser Refatorado

### 12.1 — channel_registry.py → Manter, Estender

**Não reescrever.** O ChannelAdapter ABC e o ChannelRegistry são corretos. Mudanças:

| Mudança | Impacto |
|---|---|
| Adicionar `WebChatAdapter` que conecta ao webchat.py | 1 classe (~30 linhas) |
| Adicionar `MQTTAdapter` | 1 classe (~100 linhas) |
| Adicionar `WebhookOutboundAdapter` | 1 classe (~40 linhas) |
| Tornar `reply()` async (ou aceitar coroutine) | Refactor de assinatura, ~20 linhas |

### 12.2 — Gateways → Injetar Envelope na entrada

Cada gateway hoje monta seu próprio payload ad-hoc. Refatorar para produzir um `Envelope` padronizado:

**telegram_gateway.py** — Mudar `_process_via_bridge()`:
```python
# ANTES (atual)
payload = {"text": text, "from_user": username, "chat_id": chat_id}
_bridge_post(api_base_url, f"telegram:{chat_id}", payload)

# DEPOIS (com Envelope)
envelope = Envelope(
    source_channel="telegram",
    source_id=str(chat_id),
    source_client_id=f"telegram:{chat_id}",
    message_type=MessageType.TEXT,
    text=text,
    metadata={"from_user": username},
)
message_bus.ingest(envelope)
```

**Impacto:** ~30 linhas por gateway × 5 gateways = ~150 linhas total.

### 12.3 — api.py `/webhook/{client_id}` → Delegação para MessageBus

O endpoint `/webhook/{client_id}` em api.py atualmente faz:
1. Extrai client_id da URL
2. Monta prompt
3. Chama `dispatch_runtime_prompt_sync()`
4. Retorna JSON

Refatorar para:
1. Recebe Envelope (deserializado do JSON do gateway)
2. Chama `message_bus.ingest(envelope)`
3. MessageBus resolve sessão + executa RLM + roteia resposta + enfileira no Outbox
4. Retorna ack imediato + ID do envelope

**Impacto:** ~50 linhas no endpoint, +200 linhas no MessageBus.

### 12.4 — SessionManager → Adicionar delivery_preferences

O `SessionRecord` já tem `delivery_context: dict`. Adicionar campos:

```python
# Novos campos em delivery_context (sem mudar schema SQLite — é JSON blob)
{
    "channel": "whatsapp",
    "target_id": "5511999887766",
    "preferred_channel": "telegram:12345",       # NOVO: canal preferido para respostas
    "broadcast_channels": ["telegram:12345"],     # NOVO: canais de broadcast
    "iot_subscriptions": ["mqtt:esp32-sala"],      # NOVO: devices IoT monitorados
}
```

**Impacto:** 0 linhas de schema SQL (tudo dentro do JSON blob existente). ~20 linhas para helpers de acesso.

### 12.5 — webchat.py → Registrar Adapter

```python
# Adicionar ao final de webchat.py

class WebChatAdapter(ChannelAdapter):
    """Adapter que envia mensagens via WebSocket para clientes webchat conectados."""

    def __init__(self, ws_connections: dict):
        self._connections = ws_connections   # {client_id: WebSocket}

    def send_message(self, target_id: str, text: str) -> bool:
        ws = self._connections.get(target_id)
        if not ws:
            return False
        import asyncio
        try:
            asyncio.get_event_loop().create_task(ws.send_text(text))
            return True
        except Exception:
            return False

    def send_media(self, target_id: str, media_url_or_path: str, caption: str = "") -> bool:
        # WebChat pode receber media como URL
        return self.send_message(target_id, f"[media] {media_url_or_path} {caption}")


# Na inicialização do webchat router:
from rlm.plugins.channel_registry import ChannelRegistry
ChannelRegistry.register("webchat", WebChatAdapter(_active_ws_connections))
```

**Impacto:** ~30 linhas.

---

## 13. O que Precisa Ser Criado do Zero

| Arquivo | Linhas estimadas | Prioridade |
|---|---|---|
| `rlm/comms/__init__.py` | 5 | P0 |
| `rlm/comms/envelope.py` | 120 | P0 |
| `rlm/comms/message_bus.py` | 250 | P0 |
| `rlm/comms/routing_policy.py` | 200 | P0 |
| `rlm/comms/outbox.py` | 180 | P1 |
| `rlm/comms/delivery_worker.py` | 150 | P1 |
| `rlm/comms/mqtt_adapter.py` | 130 | P2 |
| `rlm/comms/media_processor.py` | 100 | P2 |
| `rlm/skills/cross_channel_send.py` | 40 | P1 |
| `rlm/skills/mqtt_query.py` | 60 | P2 |
| `rlm/skills/iot_control.py` | 80 | P2 |
| Testes: `tests/test_envelope.py` | 100 | P0 |
| Testes: `tests/test_message_bus.py` | 150 | P0 |
| Testes: `tests/test_routing_policy.py` | 120 | P0 |
| Testes: `tests/test_outbox.py` | 100 | P1 |
| Testes: `tests/test_mqtt_adapter.py` | 80 | P2 |
| **Total** | **~1865** | |

---

## 14. Plano de Migração em 6 Fases

> Regra: cada fase é independente, não quebra testes existentes, e pode ser commitada isoladamente.

### Fase 0 — Prerequisitos (1-2 dias)

**Implementar `arquitetura-config-multidevice.md` Etapas 2-3:**
- `rlm.toml` com perfis de dispositivo
- `load_config()` substituindo `os.getenv()` espalhados
- Tabela `clients` no SQLite
- Auth por device token

**Por quê primeiro:** O MessageBus precisa saber qual device está falando para aplicar perfil correto e permissões.

### Fase 1 — Envelope + MessageBus Core (3-5 dias)

```
Criar:
  rlm/comms/__init__.py
  rlm/comms/envelope.py           ← Envelope dataclass + serialização
  rlm/comms/message_bus.py        ← MessageBus.ingest() + route() + enqueue()
  rlm/comms/routing_policy.py     ← EchoBackRule (primeiro, suficiente)
  tests/test_envelope.py
  tests/test_message_bus.py
  tests/test_routing_policy.py

Não tocar:
  Nenhum gateway existente. MessageBus funciona em paralelo à rota atual.
  Testes existentes continuam passando.

Validação:
  - Envelope serializa/deserializa JSON corretamente
  - MessageBus.ingest() → resolve sessão → executa → route() → retorna envelopes outbound
  - RoutingPolicy com EchoBack funciona
  - 1700+ testes existentes: 0 regressions
```

### Fase 2 — Outbox + DeliveryWorker (3-4 dias)

```
Criar:
  rlm/comms/outbox.py             ← CRUD SQLite + tabela
  rlm/comms/delivery_worker.py    ← asyncio task, retry, DLQ
  tests/test_outbox.py

Modificar:
  rlm/server/api.py               ← Iniciar DeliveryWorker no lifespan
  rlm/comms/message_bus.py        ← Conectar enqueue() ao Outbox

Validação:
  - Envelope enfileirado no Outbox aparece no SQLite
  - DeliveryWorker drena e chama ChannelRegistry.reply() corretamente
  - Retry funciona (simular falha do adapter)
  - DLQ funciona (simular N falhas)
  - Métricas emitidas no EventBus
```

### Fase 3 — Migrar Gateways para Envelope (3-5 dias)

```
Modificar:
  rlm/server/telegram_gateway.py   ← _process_via_bridge() produz Envelope → MessageBus
  rlm/server/whatsapp_gateway.py   ← _handle_message() produz Envelope → MessageBus
  rlm/server/discord_gateway.py    ← handler produz Envelope → MessageBus
  rlm/server/slack_gateway.py      ← handler produz Envelope → MessageBus
  rlm/server/webchat.py            ← handler produz Envelope + WebChatAdapter registrado
  rlm/server/webhook_dispatch.py   ← handler produz Envelope → MessageBus

Importante:
  - Manter fallback para rota antiga durante migração (feature flag)
  - Feature flag: RLM_USE_MESSAGE_BUS=true (default false até testes em produção)

Validação:
  - Cada gateway produz Envelope correto (testes unitários)
  - Fluxo end-to-end: gateway → Envelope → MessageBus → Outbox → DeliveryWorker → adapter
  - Idempotência: WhatsApp dedup continua funcionando
  - Rate limiting do Telegram continua funcionando
  - 1700+ testes: 0 regressions
```

### Fase 4 — Cross-Channel + Skills (2-3 dias)

```
Criar:
  rlm/skills/cross_channel_send.py  ← Skill para o agente enviar cross-channel
  rlm/comms/routing_policy.py       ← Adicionar UserPreferenceRule, BroadcastRule, AgentDirectiveRule

Modificar:
  rlm/core/session/_impl.py         ← Helpers para preferred_channel, broadcast_channels

Validação:
  - Agente pode chamar cross_channel_send("telegram:12345", "msg")
  - Mensagem vai para Telegram mesmo vindo de sessão WhatsApp
  - BroadcastRule envia para múltiplos destinos
  - UserPreferenceRule respeita configuração
```

### Fase 5 — MQTT Adapter + IoT (4-7 dias)

```
Criar:
  rlm/comms/mqtt_adapter.py        ← Bridge bidirecional MQTT ↔ MessageBus
  rlm/skills/mqtt_query.py         ← Skill: consultar último dado de sensor
  rlm/skills/iot_control.py        ← Skill: enviar comando para device
  tests/test_mqtt_adapter.py

Configurar:
  rlm.toml → [mqtt] section com broker_host, subscribe_topics
  Instalar Mosquitto no servidor (apt install mosquitto)
  Configurar ESP32 para publicar em tópicos padronizados

Dependência pip:
  aiomqtt >= 2.0

Validação:
  - ESP32 publica sensor data → MQTTAdapter → Envelope → MessageBus
  - Agente consulta sensor data via mqtt_query()
  - Agente envia comando via iot_control() → MQTTAdapter → MQTT → ESP32
  - Cenário end-to-end: "Qual a temperatura?" no Telegram → resposta com dados IoT
  - Cenário end-to-end: "Liga o AC" no WhatsApp → MQTT → ESP32 → confirmação
```

### Fase 6 — Media Processing (2-3 dias)

```
Criar:
  rlm/comms/media_processor.py     ← Download de mídia (WhatsApp, Telegram)

Modificar:
  rlm/server/whatsapp_gateway.py   ← Chamar MediaProcessor em vez de texto descritivo
  rlm/server/telegram_gateway.py   ← Processar fotos, áudio, documentos (hoje ignora)

Validação:
  - Imagem enviada no WhatsApp → download → path local no Envelope.media_url
  - Áudio enviado no Telegram → download → path local
  - Limite de 20MB respeitado
  - Cleanup de arquivos antigos
```

---

## 15. Diagramas de Fluxo

### Fluxo Completo — Cross-Channel (WhatsApp → Telegram)

```
  WhatsApp                WhatsApp           api.py            MessageBus
    User                  Gateway          /webhook
     │                      │                │                    │
     │──"Qual temp sala?"──→│                │                    │
     │                      │──Envelope──────→│                    │
     │                      │                │──ingest()──────────→│
     │                      │                │                    │
     │                      │                │  ┌─────────────────┤
     │                      │                │  │ SessionManager  │
     │                      │                │  │ resolve session │
     │                      │                │  │ RLM.completion()│
     │                      │                │  │ resultado       │
     │                      │                │  └────────┬────────┤
     │                      │                │           │        │
     │                      │                │  ┌────────┴────────┤
     │                      │                │  │ RoutingPolicy   │
     │                      │                │  │ preferred_ch=   │
     │                      │                │  │ "telegram:123"  │
     │                      │                │  └────────┬────────┤
     │                      │                │           │        │
     │                      │                │  ┌────────┴────────┤
     │                      │                │  │ Outbox.enqueue()│
     │                      │                │  │ target=telegram │
     │                      │                │  └─────────────────┤
     │                      │                │                    │
     │                      │                │                    │
  Telegram                                              DeliveryWorker
    User                                                     │
     │                                                       │
     │←─────────"Sala: 27.3°C, 65%"─────────────────────────│
     │                                       TelegramAdapter │
     │                                       send_message()  │
```

### Fluxo IoT — Comando + Confirmação

```
  Telegram        RLM           MQTTAdapter      Mosquitto       ESP32
    User        (agente)                          (broker)
     │             │                │                │              │
     │─"Liga AC"──→│                │                │              │
     │             │                │                │              │
     │             │ cross_channel_send("mqtt:esp32-sala", cmd)    │
     │             │───Envelope─────→│                │              │
     │             │                │──PUBLISH────────→│              │
     │             │                │  casa/sala/cmd  │──PUBLISH────→│
     │             │                │                │              │
     │             │                │                │  ┌───────────┤
     │             │                │                │  │ Executa IR│
     │             │                │                │  │ Liga AC   │
     │             │                │                │  └─────┬─────┤
     │             │                │                │        │     │
     │             │                │                │←PUBLISH─┘    │
     │             │                │  casa/sala/evt │              │
     │             │                │←─SUBSCRIBE─────│              │
     │             │←──Envelope─────│                │              │
     │             │ EVENT: ac_ok   │                │              │
     │             │                │                │              │
     │←"AC ligado ✓"│               │                │              │
     │  (Telegram)  │               │                │              │
```

---

## 16. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Outbox cresce demais (mensagens não entregues) | Média | Alto (disco) | Cleanup job: DELETE WHERE status='delivered' AND delivered_at < now-7d. DLQ alertas. |
| MQTT broker offline | Baixa | Alto (IoT para) | Outbox buffering. ESP32 tem buffer local de 10 leituras. Reconnect automático no aiomqtt. |
| Migração quebra testes | Baixa | Alto | Feature flag `RLM_USE_MESSAGE_BUS`. Cada fase é isolada. Rollback é desligar flag. |
| Latência adicional do Outbox (SQLite poll) | Baixa | Médio | Poll interval 500ms. Para urgente: bypass direto do adapter (priority=1, skip outbox). |
| Segurança: cross_channel_send permite enviar para qualquer canal | Média | Alto | Validar que target_client_id está em `allowed_targets` do perfil do device. Admin via rlm.toml. |
| Envelope muito grande (mídia embedada) | Baixa | Médio | Envelope.media_url aponta para path local. Não embeda bytes no JSON. |
| Race condition no DeliveryWorker | Baixa | Médio | `SELECT ... FOR UPDATE` ou advisory lock no SQLite. Worker single-instance no lifespan. |

---

## 17. Glossário

| Termo | Definição |
|---|---|
| **Envelope** | Unidade atômica de mensagem no MessageBus. Contém origem, destino, conteúdo, e metadados. |
| **MessageBus** | Ponto central de roteamento. Recebe Envelopes inbound, executa processamento, produz Envelopes outbound. |
| **RoutingPolicy** | Chain of responsibility que decide para onde vai a resposta. |
| **Outbox** | Tabela SQLite que funciona como fila persistente de mensagens a entregar. |
| **DeliveryWorker** | Loop assíncrono que drena o Outbox e entrega via adapters. |
| **DLQ (Dead Letter Queue)** | Status no Outbox para mensagens que falharam N vezes. Requerem intervenção manual. |
| **Adapter** | Implementação de `ChannelAdapter` para um canal específico (Telegram, MQTT, etc.). |
| **Ingest** | Processo de normalizar uma mensagem recebida em Envelope e injetar no MessageBus. |
| **Cross-channel** | Capacidade de receber de um canal e responder em outro. |
| **Bridge** | Gateway que opera como processo externo (ex: Telegram long-poll) e se comunica com api.py via HTTP. |
| **Router** | Gateway que opera como sub-app FastAPI dentro do mesmo processo. |
| **EchoBack** | Regra de roteamento padrão: responde no mesmo canal que originou a mensagem. |
| **MQTT** | Message Queuing Telemetry Transport — protocolo leve para IoT. |
| **Mosquitto** | Broker MQTT open-source (Eclipse Foundation). |
| **aiomqtt** | Biblioteca Python async para MQTT (`pip install aiomqtt`). |
| **Transactional Outbox** | Padrão onde mensagens são persistidas na mesma transação do processamento, garantindo at-least-once delivery. |

---

## Resumo Executivo

### O que existe e funciona
- 7 canais operacionais com adapter pattern
- SessionManager com 3 scopes de sessão
- delivery_context persistido
- EventBus instanciado e pronto para uso

### O que falta
- Envelope universal (modelo de dados canônico)
- MessageBus (ponto central de roteamento)
- Outbox + DeliveryWorker (entrega confiável)
- RoutingPolicy (cross-channel, broadcast, preferências)
- MQTTAdapter (IoT)
- MediaProcessor (mídia inbound)

### Linguagem
- **Python para tudo.** Rust só se medir bottleneck real no futuro.
- Dependência nova: `aiomqtt >= 2.0` (MQTT), `httpx` (já existe como dep).
- Infraestrutura nova: Mosquitto broker no servidor (para IoT).

### Esforço estimado
- **6 fases**, cada uma independente e reversível
- Total estimado: **~1865 linhas novas** + **~300 linhas refatoradas** em arquivos existentes
- **0 linhas migradas para outra linguagem**

### Filosofia
> Não reescrever o que funciona. Adicionar camada sobre o existente. Cada mudança é incremental e testável.
