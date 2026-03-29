# RLM Gateway Architecture — Documento de Referência para Recriação

> **Propósito**: Este documento cataloga TODAS as diferenças arquiteturais entre os sistemas de gateway do **RLM**, **OpenClaw** e **VS Code**. Serve como blueprint para recriar e melhorar a infraestrutura de gateway do RLM, adotando os melhores padrões de cada referência.

> **Data**: 2025-03-29  
> **Commit base RLM**: `56ef26e`  
> **Versão OpenClaw**: local (`openclaw-main/`)  
> **Versão VS Code**: local (`vscode-main/`)

---

## Índice

1. [Inventário Atual do RLM](#1-inventário-atual-do-rlm)
2. [Gap 1 — Connection State Machine](#2-gap-1--connection-state-machine)
3. [Gap 2 — Exponential Backoff com Jitter](#3-gap-2--exponential-backoff-com-jitter)
4. [Gap 3 — Health Monitor Centralizado](#4-gap-3--health-monitor-centralizado)
5. [Gap 4 — Message Normalization (Envelope Canônico)](#5-gap-4--message-normalization-envelope-canônico)
6. [Gap 5 — Graceful Drain](#6-gap-5--graceful-drain)
7. [Gap 6 — Backpressure / Flow Control](#7-gap-6--backpressure--flow-control)
8. [Gap 7 — Message Deduplication](#8-gap-7--message-deduplication)
9. [Gap 8 — Outbound Chunking Inteligente](#9-gap-8--outbound-chunking-inteligente)
10. [Gap 9 — Heartbeat / Keepalive](#10-gap-9--heartbeat--keepalive)
11. [Gap 10 — Streaming de Resposta](#11-gap-10--streaming-de-resposta)
12. [Padrões Existentes no RLM que Devem Ser Preservados](#12-padrões-existentes-no-rlm-que-devem-ser-preservados)
13. [Matriz Completa de Comparação](#13-matriz-completa-de-comparação)
14. [Plano de Implementação Priorizado](#14-plano-de-implementação-priorizado)

---

## 1. Inventário Atual do RLM

### 1.1 Arquivos de Gateway (`rlm/server/`)

| Arquivo | Linhas | Protocolo | Canal |
|---|---|---|---|
| `api.py` | ~780 | HTTP REST (FastAPI) | Orquestrador central, lifespan |
| `telegram_gateway.py` | ~600 | Long-polling (urllib) | Telegram |
| `discord_gateway.py` | ~232 | HTTP Interactions (FastAPI) | Discord |
| `slack_gateway.py` | ~245 | Events API (FastAPI) | Slack |
| `whatsapp_gateway.py` | ~290 | Meta Cloud API (FastAPI) | WhatsApp |
| `webchat.py` | ~270 | HTTP + Background tasks | Web UI |
| `ws_server.py` | ~280 | WebSocket (websockets) | Observabilidade |
| `webhook_dispatch.py` | ~280 | HTTP hooks (FastAPI) | Genérico |
| `openai_compat.py` | ~260 | HTTP (drop-in OpenAI) | Qualquer client OpenAI |
| `scheduler.py` | ~800 | Daemon + SQLite | Proativo (cron/interval) |
| `event_router.py` | ~280 | Interno (glob routing) | N/A |
| `channel_registry.py` | ~160 | ABC abstrato | Todos |

### 1.2 Módulos de Suporte (`rlm/core/`)

| Arquivo | Linhas | Papel |
|---|---|---|
| `shutdown.py` | ~165 | ShutdownManager com veto system |
| `disposable.py` | ~175 | DisposableStore (cleanup reverso) |
| `cancellation.py` | ~185 | CancellationToken/Source hierárquico |
| `comms_utils.py` | ~200 | Protocolo 4-byte length prefix + JSON |
| `security.py` | ~200 | InputThreatReport, EnvVarShield, REPLAuditor |
| `auth_helpers.py` | ~86 | hmac.compare_digest, Bearer/Header/Query |

### 1.3 Pontos Fortes Atuais (NÃO alterar)

- **Scheduler proativo** (675 linhas): cron/interval/once/condition, SQLite persistence, Telegram notify
- **OpenAI-compatible API**: Drop-in `/v1/chat/completions` com SSE streaming simulado
- **Human approval gate**: `POST /exec/approve/{id}` para operações de risco
- **Security pipeline**: 20+ regex patterns para prompt injection, env var shield, REPL sandbox
- **Auth diversificada**: Ed25519 (Discord), HMAC-SHA256 (Slack/WhatsApp), Bearer token — tudo stdlib
- **Dual rate limiting**: IP + client_id com sliding window
- **Event bus**: `RLMEventBus` com WebSocket broadcast e SSE fallback
- **CancellationToken**: Pattern VS Code portado para Python
- **DisposableStore**: Cleanup reverso com GC leak detection
- **ShutdownManager**: Veto system para shutdown cooperativo

---

## 2. Gap 1 — Connection State Machine

### 2.1 Estado Atual do RLM

O Telegram gateway usa apenas um booleano:

```python
# telegram_gateway.py
class TelegramGateway:
    def __init__(self, ...):
        self._running = False     # ← ÚNICO estado
        self._error_count = 0     # ← contador linear

    def run(self, until_stopped=True):
        self._running = True
        while self._running and until_stopped:
            try:
                self.poll_once()
                self._error_count = 0  # reset após sucesso
            except Exception as e:
                self._error_count += 1
                if self._error_count >= self.config.max_consecutive_errors:
                    break
                time.sleep(self.config.error_backoff_s)  # fixo 5s
```

**Problema**: Não há distinção entre "conectando", "degradado", "reconectando" ou "desistiu". Se o gateway está em backoff, o `/health` não sabe. Se reconectou após falha, não há evento. Não há como um operador saber o estado real.

### 2.2 Referência: VS Code — 5 Estados Explícitos

```typescript
// src/vs/platform/remote/common/remoteAgentConnection.ts

export const enum PersistentConnectionEventType {
    ConnectionLost,           // socket fechou / timeout
    ReconnectionWait,         // esperando antes de reconectar (com timer cancelável)
    ReconnectionRunning,      // tentando reconectar agora
    ReconnectionPermanentFailure,  // desistiu
    ConnectionGain            // (re)conectou com sucesso
}

// Eventos tipados com metadados:
class ConnectionLostEvent {
    constructor(
        public readonly reconnectionToken: string,
        public readonly millisSinceLastIncomingData: number
    ) { }
}

class ReconnectionWaitEvent {
    constructor(
        public readonly reconnectionToken: string,
        public readonly millisSinceLastIncomingData: number,
        public readonly durationSeconds: number,
        private readonly cancellableTimer: CancelablePromise<void>
    ) { }
    public skipWait(): void { this.cancellableTimer.cancel(); }
}

class ConnectionGainEvent {
    constructor(
        public readonly reconnectionToken: string,
        public readonly millisSinceLastIncomingData: number,
        public readonly attempt: number
    ) { }
}

class ReconnectionPermanentFailureEvent {
    constructor(
        public readonly reconnectionToken: string,
        public readonly millisSinceLastIncomingData: number,
        public readonly attempt: number,
        public readonly handled: boolean
    ) { }
}
```

**Padrões-chave do VS Code:**
- Cada transição de estado emite evento tipado com metadados
- `ReconnectionWaitEvent` carrega timer cancelável (operador pode "pular" a espera)
- `ConnectionGainEvent` reporta número da tentativa
- `PermanentFailure` distingue entre "handled" (auth error, autoridade inválida) e "unhandled" (timeout desconhecido)
- Grace time de **3 horas** antes de declarar falha permanente
- Triggers: `protocol.onSocketClose` e `protocol.onSocketTimeout`

### 2.3 Referência: OpenClaw — Health Policy com 7 Razões

```typescript
// src/gateway/channel-health-policy.ts

type ChannelHealthEvaluationReason =
    | "healthy"              // tudo OK
    | "unmanaged"            // canal não habilitado
    | "not-running"          // parado
    | "busy"                 // processando (mas ativo)
    | "stuck"                // processando há >25min sem atividade
    | "startup-connect-grace" // recém-iniciado, em período de graça
    | "disconnected"         // connected === false
    | "stale-socket";        // connected mas sem eventos há >30min

function evaluateChannelHealth(
    snapshot: ChannelHealthSnapshot,
    policy: ChannelHealthPolicy,
): ChannelHealthEvaluation {
    if (!isManagedAccount(snapshot))  return { healthy: true, reason: "unmanaged" };
    if (!snapshot.running)            return { healthy: false, reason: "not-running" };

    // Busy → check if stale
    if (isBusy) {
        if (runActivityAge < BUSY_ACTIVITY_STALE_THRESHOLD_MS) {
            return { healthy: true, reason: "busy" };
        }
        return { healthy: false, reason: "stuck" };
    }

    // Startup grace period
    if (upDuration < policy.channelConnectGraceMs) {
        return { healthy: true, reason: "startup-connect-grace" };
    }

    // Disconnected
    if (snapshot.connected === false) {
        return { healthy: false, reason: "disconnected" };
    }

    // Stale socket (connected but no events for >30min)
    if (eventAge > policy.staleEventThresholdMs) {
        return { healthy: false, reason: "stale-socket" };
    }

    return { healthy: true, reason: "healthy" };
}
```

**Padrões-chave do OpenClaw:**
- Avaliação é uma **função pura** que recebe snapshot + policy → retorna resultado tipado
- "stale-socket" é a situação mais perigosa: TCP ainda aberto mas nenhum dado flui
- Grace periods são configuráveis: `channelConnectGraceMs=120_000`, `staleEventThresholdMs=30*60_000`
- `resolveChannelRestartReason()` monta o motivo usado no log do restart

### 2.4 Proposta para o RLM

Criar `rlm/server/gateway_state.py`:

```python
# Estados do gateway (Python Enum)
class GatewayState(str, Enum):
    CONFIGURED   = "configured"      # Configurado mas não iniciado
    CONNECTING   = "connecting"      # Tentando primeira conexão
    CONNECTED    = "connected"       # Operacional
    DEGRADED     = "degraded"        # Conectado mas com erros recentes
    RECONNECTING = "reconnecting"    # Perdeu conexão, tentando reconectar
    BACKOFF_WAIT = "backoff_wait"    # Esperando antes de reconectar
    GIVEN_UP     = "given_up"        # Excedeu max tentativas
    STOPPED      = "stopped"         # Parado manualmente
```

Cada transição emite evento via `RLMEventBus`:
```
CONFIGURED → CONNECTING → CONNECTED ⇆ DEGRADED
                              ↓
                         RECONNECTING → BACKOFF_WAIT → RECONNECTING
                              ↓
                          GIVEN_UP
    qualquer → STOPPED (manual stop)
```

Expor estado no `/health`:
```json
{
    "status": "online",
    "gateways": {
        "telegram": {"state": "connected", "uptime_s": 3600, "last_error": null},
        "discord": {"state": "connected", "last_event_at": "2025-03-29T10:00:00Z"},
        "whatsapp": {"state": "degraded", "error_count": 3, "next_retry_at": "..."}
    }
}
```

---

## 3. Gap 2 — Exponential Backoff com Jitter

### 3.1 Estado Atual do RLM

```python
# telegram_gateway.py — único backoff no sistema
class GatewayConfig:
    error_backoff_s: float = 5.0          # FIXO
    max_consecutive_errors: int = 10       # FIXO

# No loop:
time.sleep(self.config.error_backoff_s)    # sempre 5s
```

**Problema**: Backoff fixo de 5s causa thundering herd quando múltiplas instâncias reconectam simultaneamente. Sem jitter, todas as instâncias reenviam no mesmo instante.

### 3.2 Referência: OpenClaw — Fórmula com Jitter

```typescript
// src/infra/backoff.ts (30 linhas, standalone)

export type BackoffPolicy = {
    initialMs: number;   // delay inicial
    maxMs: number;       // teto máximo
    factor: number;      // fator multiplicativo
    jitter: number;      // fração de randomização (0.0 - 1.0)
};

export function computeBackoff(policy: BackoffPolicy, attempt: number) {
    const base = policy.initialMs * policy.factor ** Math.max(attempt - 1, 0);
    const jitter = base * policy.jitter * Math.random();
    return Math.min(policy.maxMs, Math.round(base + jitter));
}

export async function sleepWithAbort(ms: number, abortSignal?: AbortSignal) {
    if (ms <= 0) { return; }
    try {
        await delay(ms, undefined, { signal: abortSignal });
    } catch (err) {
        if (abortSignal?.aborted) {
            throw new Error("aborted", { cause: err });
        }
        throw err;
    }
}
```

**Constantes usadas no channel restart:**
```typescript
const CHANNEL_RESTART_POLICY: BackoffPolicy = {
    initialMs: 5_000,    // 5s
    maxMs: 5 * 60_000,   // 5min teto
    factor: 2,           // dobra a cada tentativa
    jitter: 0.1,         // ±10% de randomização
};
const MAX_RESTART_ATTEMPTS = 10;

// Sequência real (sem jitter): 5s, 10s, 20s, 40s, 80s, 160s, 300s, 300s, 300s, 300s
```

### 3.3 Referência: VS Code — Sequência Tabular

```typescript
// src/vs/platform/remote/common/remoteAgentConnection.ts

private async _runReconnectingLoop(): Promise<void> {
    const TIMES = [0, 5, 5, 10, 10, 10, 10, 10, 30];
    // Tentativa 0: imediata (0s)
    // Tentativa 1-2: 5s cada
    // Tentativa 3-7: 10s cada
    // Tentativa 8+: 30s cada
    // Grace time: 3 HORAS antes de desistir
    const graceTime = this._reconnectionGraceTime;
    const loopStartTime = Date.now();
    let attempt = -1;
    do {
        attempt++;
        const waitTime = (attempt < TIMES.length
            ? TIMES[attempt]
            : TIMES[TIMES.length - 1]);
        if (waitTime > 0) {
            const sleepPromise = sleep(waitTime);
            this._onDidStateChange.fire(new ReconnectionWaitEvent(
                this.reconnectionToken,
                this.protocol.getMillisSinceLastIncomingData(),
                waitTime, sleepPromise));
            await sleepPromise;
        }
        // ... reconexão ...
        if (Date.now() - loopStartTime >= graceTime) {
            this._onReconnectionPermanentFailure(...);
            break;
        }
    } while (!this._isPermanentFailure && !this._isDisposed);
}
```

**Padrões-chave do VS Code:**
- Sequência hardcoded (não exponencial, mas escalonada)
- Grace time de 3 HORAS — muito generoso para conexões remotas
- Cada espera emite `ReconnectionWaitEvent` (com timer cancelável)
- Após grace → `PermanentFailure`

### 3.4 Proposta para o RLM

Criar `rlm/server/backoff.py`:

```python
@dataclass(frozen=True)
class BackoffPolicy:
    initial_s: float = 5.0
    max_s: float = 300.0      # 5min
    factor: float = 2.0
    jitter: float = 0.1       # ±10%
    max_attempts: int = 10

def compute_backoff(policy: BackoffPolicy, attempt: int) -> float:
    base = policy.initial_s * (policy.factor ** max(attempt - 1, 0))
    jitter = base * policy.jitter * random.random()
    return min(policy.max_s, base + jitter)

async def sleep_with_cancel(seconds: float, cancel_token: CancellationToken) -> bool:
    """Retorna True se dormiu completo, False se cancelado."""
    ...
```

Policies pré-definidas:
```python
GATEWAY_RECONNECT = BackoffPolicy(initial_s=5, max_s=300, factor=2, jitter=0.1, max_attempts=10)
HTTP_RETRY = BackoffPolicy(initial_s=1, max_s=30, factor=2, jitter=0.2, max_attempts=5)
HEALTH_CHECK = BackoffPolicy(initial_s=10, max_s=60, factor=1.5, jitter=0.05, max_attempts=3)
```

---

## 4. Gap 3 — Health Monitor Centralizado

### 4.1 Estado Atual do RLM

```python
# api.py — único health check
@app.get("/health")
async def health_check(request: Request):
    return {
        "status": "online",
        "active_sessions": len(active_sessions),
        "running_executions": len(running),
        "plugins_available": len(loader.list_available()),
        "model": os.environ.get("RLM_MODEL", "gpt-4o-mini"),
    }
```

**Problema**: `/health` retorna "online" mesmo se o Telegram está morto há 2 horas. Não verifica canais individuais. Não tem periodic check — só responde quando perguntado.

### 4.2 Referência: OpenClaw — Monitor Periódico com Throttling

```typescript
// src/gateway/channel-health-monitor.ts (~180 linhas)

const DEFAULT_CHECK_INTERVAL_MS = 5 * 60_000;        // checa cada 5min
const DEFAULT_MONITOR_STARTUP_GRACE_MS = 60_000;      // ignora primeiro minuto
const DEFAULT_COOLDOWN_CYCLES = 2;                     // 2 ciclos entre restarts
const DEFAULT_MAX_RESTARTS_PER_HOUR = 10;              // teto de restarts
const DEFAULT_STALE_EVENT_THRESHOLD_MS = 30 * 60_000;  // socket morto: 30min sem eventos
const DEFAULT_CHANNEL_CONNECT_GRACE_MS = 120_000;      // 2min para conectar

async function runCheck() {
    if (stopped || checkInFlight) return;  // não overlap
    checkInFlight = true;
    try {
        const now = Date.now();
        if (now - startedAt < timing.monitorStartupGraceMs) return;

        const snapshot = channelManager.getRuntimeSnapshot();
        for (const [channelId, accounts] of Object.entries(snapshot)) {
            for (const [accountId, status] of Object.entries(accounts)) {
                if (channelManager.isManuallyStopped(channelId, accountId)) continue;

                const health = evaluateChannelHealth(status, healthPolicy);
                if (health.healthy) continue;

                // Cooldown: não restart se recém-reiniciou
                if (now - record.lastRestartAt <= cooldownMs) continue;

                // Rate limit: max restarts por hora
                pruneOldRestarts(record, now);
                if (record.restartsThisHour.length >= maxRestartsPerHour) continue;

                // Executa restart
                if (status.running) {
                    await channelManager.stopChannel(channelId, accountId);
                }
                channelManager.resetRestartAttempts(channelId, accountId);
                await channelManager.startChannel(channelId, accountId);
                record.lastRestartAt = now;
                record.restartsThisHour.push({ at: now });
            }
        }
    } finally {
        checkInFlight = false;
    }
}
```

**Padrões-chave OpenClaw:**
- Check periódico (5min interval), não sob-demanda
- Cooldown entre restarts (evita reset loop)
- Rate limit por hora (max 10 restarts/hora)
- Lock contra overlap (`checkInFlight` booleano)
- Startup grace (ignora primeiro minuto)
- Avaliação é função pura (testável)

### 4.3 Referência: VS Code — ACK-Based Heartbeat

```typescript
// src/vs/workbench/services/extensions/common/rpcProtocol.ts

private static readonly UNRESPONSIVE_TIME = 3 * 1000; // 3s

private _onWillSendRequest(req: number): void {
    if (this._unacknowledgedCount === 0) {
        this._unresponsiveTime = Date.now() + RPCProtocol.UNRESPONSIVE_TIME;
    }
    this._unacknowledgedCount++;
    if (!this._asyncCheckUresponsive.isScheduled()) {
        this._asyncCheckUresponsive.schedule();
    }
}

private _onDidReceiveAcknowledge(req: number): void {
    this._unresponsiveTime = Date.now() + RPCProtocol.UNRESPONSIVE_TIME;
    this._unacknowledgedCount--;
    if (this._unacknowledgedCount === 0) {
        this._asyncCheckUresponsive.cancel();
    }
    this._setResponsiveState(ResponsiveState.Responsive);
}

private _checkUnresponsive(): void {
    if (this._unacknowledgedCount === 0) return;
    if (Date.now() > this._unresponsiveTime) {
        this._setResponsiveState(ResponsiveState.Unresponsive);
    } else {
        this._asyncCheckUresponsive.schedule();
    }
}
```

```typescript
// KeepAlive — ipc.net.ts
const enum ProtocolConstants {
    KeepAliveSendTime = 5000, // envia keepalive cada 5s
    TimeoutTime = 20000,       // timeout se sem resposta por 20s
}
```

**Padrões-chave VS Code:**
- Detecção passiva baseada em ACKs (não polling ativo)
- 3s sem ACK → marca como Unresponsive (UI mostra ⚠️)
- KeepAlive a cada 5s para detectar morte de conexão
- Timeout em 20s → fecha socket → inicia reconexão

### 4.4 Proposta para o RLM

Criar `rlm/server/health_monitor.py`:

```python
class GatewayHealthMonitor:
    CHECK_INTERVAL_S = 300        # 5min
    STARTUP_GRACE_S = 60          # ignore primeiro minuto
    MAX_RESTARTS_PER_HOUR = 10
    COOLDOWN_S = 600              # 10min entre restarts do mesmo gateway
    STALE_THRESHOLD_S = 1800      # 30min sem eventos = stale

    def __init__(self, registry: ChannelRegistry, event_bus: RLMEventBus):
        ...

    async def run_check(self):
        """Executado periodicamente pelo asyncio loop."""
        for gateway_id, adapter in self.registry.items():
            health = self.evaluate(adapter)
            if not health.healthy:
                self._maybe_restart(gateway_id, health.reason)

    def evaluate(self, adapter) -> HealthEvaluation:
        """Função pura — avalia saúde do gateway."""
        ...
```

API expandida:
```json
GET /health →
{
    "status": "online",
    "gateways": {
        "telegram": {
            "state": "connected",
            "health": "healthy",
            "last_event_at": "2025-03-29T10:05:00Z",
            "uptime_s": 7200,
            "restarts_last_hour": 0
        },
        "discord": {
            "state": "connected",
            "health": "healthy",
            "last_event_at": "2025-03-29T09:55:00Z"
        }
    },
    "monitor": {
        "last_check_at": "2025-03-29T10:00:00Z",
        "next_check_at": "2025-03-29T10:05:00Z"
    }
}
```

---

## 5. Gap 4 — Message Normalization (Envelope Canônico)

### 5.1 Estado Atual do RLM

Cada gateway faz parsing ad-hoc:

```python
# whatsapp_gateway.py — normalização inline
if msg_type == "text":
    rlm_text = message.get("text", {}).get("body", "")
elif msg_type in {"image", "audio", "document", "video", "sticker"}:
    media_obj = message.get(msg_type, {})
    rlm_text = f"[{msg_type.upper()} recebido] media_id={media_obj.get('id')}"
elif msg_type == "location":
    loc = message.get("location", {})
    rlm_text = f"Localização recebida: lat={loc.get('latitude')}, lon={loc.get('longitude')}"

# discord_gateway.py — extração diferente
def _extract_interaction_data(interaction: dict) -> dict:
    data = interaction.get("data", {})
    options = data.get("options", [])
    ...

# slack_gateway.py — strip mention inline
text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

# telegram_gateway.py — separação comando/texto inline
if text.startswith("/"):
    command = text.split()[0].lower()
    ...
```

**Problema**: Não há formato canônico. Cada gateway produz strings diferentes. Impossível comparar, auditar ou processar uniformemente. Metadados de mídia se perdem.

### 5.2 Referência: OpenClaw — `MsgContext` (~80+ campos)

```typescript
// src/auto-reply/templating.ts

export type MsgContext = {
    // --- Conteúdo ---
    Body?: string;                  // texto processado
    BodyForAgent?: string;          // texto pré-processado para IA
    RawBody?: string;               // texto cru original
    CommandBody?: string;           // corpo após strip do comando
    InboundHistory?: Array<{ sender: string; body: string; timestamp?: number }>;

    // --- Identificadores ---
    From?: string;                  // remetente canônico
    To?: string;                    // destinatário canônico
    SessionKey?: string;
    AccountId?: string;
    MessageSid?: string;            // ID único da mensagem
    ReplyToId?: string;             // reply chain
    RootMessageId?: string;
    ForwardedFrom?: string;

    // --- Mídia ---
    MediaPath?: string;
    MediaUrl?: string;
    MediaType?: string;
    MediaPaths?: string[];
    MediaUrls?: string[];
    MediaTypes?: string[];
    Sticker?: StickerMetadata;
    Transcript?: string;            // transcrição de áudio
    MediaUnderstanding?: MediaUnderstandingOutput[];
    LinkUnderstanding?: string[];

    // --- Contexto do Chat ---
    ChatType?: string;              // "private" | "group" | "channel"
    GroupSubject?: string;
    GroupChannel?: string;
    SenderName?: string;
    SenderId?: string;
    SenderUsername?: string;
    SenderE164?: string;            // telefone E.164
    Timestamp?: number;

    // --- Roteamento ---
    Provider?: string;              // "telegram" | "discord" | "whatsapp" | ...
    Surface?: string;
    WasMentioned?: boolean;
    CommandAuthorized?: boolean;
    MessageThreadId?: string | number;
    OriginatingChannel?: OriginatingChannelType;
    OriginatingTo?: string;

    // ... ~30+ campos adicionais
};
```

**Padrões-chave OpenClaw:**
- Tipo único para TODAS as mensagens de TODOS os canais
- Normalizers PER-CHANNEL (`normalize/telegram.ts`, `normalize/discord.ts`, etc.)
- `Body` vs `BodyForAgent` vs `RawBody` — 3 níveis de processamento
- Reply chain tracking completo (`ReplyToId`, `RootMessageId`, `ReplyToBody`)
- Mídia normalizada: `MediaUrl` + `MediaType` + `Transcript`
- `Provider` identifica o canal de origem

### 5.3 Proposta para o RLM

Criar `rlm/server/message_envelope.py`:

```python
@dataclass
class InboundMessage:
    """Envelope canônico para todas as mensagens inbound."""
    # --- Conteúdo ---
    text: str                                    # texto processado
    raw_text: str = ""                           # texto cru original
    command: str | None = None                   # /start, /help, etc.
    command_args: str = ""                       # args após o comando

    # --- Identificadores ---
    message_id: str = ""                         # ID único da mensagem
    sender_id: str = ""                          # ID do remetente
    sender_name: str = ""                        # nome display
    sender_username: str = ""                    # @username
    chat_id: str = ""                            # ID do chat/canal
    reply_to_id: str | None = None               # reply chain
    thread_id: str | None = None                 # thread (Discord/Slack)

    # --- Mídia ---
    media_type: str | None = None                # "image" | "audio" | "video" | "document" | "sticker" | "location"
    media_url: str | None = None
    media_id: str | None = None
    media_mime: str | None = None
    location: tuple[float, float] | None = None  # (lat, lon)
    transcript: str | None = None                # transcrição de áudio

    # --- Contexto ---
    channel: str = ""                            # "telegram" | "discord" | "slack" | "whatsapp" | "webchat" | "webhook"
    chat_type: str = "private"                   # "private" | "group" | "channel"
    was_mentioned: bool = False
    timestamp: float = field(default_factory=time.time)

    # --- Raw ---
    raw_payload: dict = field(default_factory=dict)
```

Normalizers per-channel em `rlm/server/normalize/`:
```
normalize/
├── __init__.py         # normalize(channel, raw_payload) -> InboundMessage
├── telegram.py         # normalize_telegram(update) -> InboundMessage
├── discord.py          # normalize_discord(interaction) -> InboundMessage
├── slack.py            # normalize_slack(event) -> InboundMessage
├── whatsapp.py         # normalize_whatsapp(message, metadata) -> InboundMessage
└── webhook.py          # normalize_webhook(body) -> InboundMessage
```

---

## 6. Gap 5 — Graceful Drain

### 6.1 Estado Atual do RLM

```python
# api.py — shutdown lifecycle
# --- Shutdown ---
gateway_log.info("Shutting down...")
app.state.scheduler.stop()              # ← scheduler tem _wait_active(30s)!
app.state.skill_loader.deactivate_all()
app.state.supervisor.shutdown()          # ← mata tudo imediatamente
app.state.session_manager.close_all()
gateway_log.info("Shutdown complete.")
```

**Problema**: Se uma mensagem está sendo processada pelo RLM quando `Ctrl+C` é pressionado, ela é abortada sem resposta. O scheduler é o ÚNICO componente que espera tasks ativas finalizarem.

### 6.2 Referência: OpenClaw — Shutdown Orquestrado em 13 Passos

```typescript
// src/gateway/server-close.ts (~120 linhas)

return async (opts) => {
    // 1. Stop Bonjour + Tailscale (discovery)
    await params.bonjourStop();
    await params.tailscaleCleanup();

    // 2. Stop Canvas host
    await params.canvasHost.close();

    // 3. Stop ALL channel plugins (graceful per-account)
    for (const plugin of listChannelPlugins()) {
        await params.stopChannel(plugin.id);
    }

    // 4. Stop plugin services
    await params.pluginServices.stop();

    // 5. Stop Gmail watcher
    await stopGmailWatcher();

    // 6. Stop cron + heartbeat
    params.cron.stop();
    params.heartbeatRunner.stop();

    // 7. Broadcast shutdown event to clients
    params.broadcast("shutdown", { reason, restartExpectedMs });

    // 8. Clear maintenance timers
    clearInterval(params.tickInterval);
    clearInterval(params.healthInterval);
    clearInterval(params.dedupeCleanup);

    // 9. Cleanup subscriptions
    params.agentUnsub?.();
    params.chatRunState.clear();

    // 10. Close all WS clients with 1012 (service restart)
    for (const c of params.clients) {
        c.socket.close(1012, "service restart");
    }

    // 11. Stop config reloader + browser control
    await params.configReloader.stop();

    // 12. Close WebSocket server
    await new Promise(resolve => params.wss.close(() => resolve()));

    // 13. Close HTTP server(s) + idle connections
    for (const server of servers) {
        httpServer.closeIdleConnections?.();
        await new Promise(resolve => httpServer.close(err => ...));
    }
};
```

**Restart deferral — espera operações ativas:**
```typescript
// src/gateway/server-reload-handlers.ts
const requestGatewayRestart = (plan, nextConfig) => {
    const active = getActiveCounts();  // queueSize + pendingReplies + embeddedRuns
    if (active.totalActive > 0) {
        restartPending = true;
        deferGatewayRestartUntilIdle(...);  // espera todas terminarem
    } else {
        emitGatewayRestart();  // SIGUSR1 imediato
    }
};
```

**Restart Sentinel — persistência cross-restart:**
```typescript
// src/infra/restart-sentinel.ts
type RestartSentinelPayload = {
    kind: "config-apply" | "config-patch" | "update" | "restart";
    status: "ok" | "error" | "skipped";
    ts: number;
    sessionKey?: string;
    deliveryContext?: { channel?: string; to?: string; accountId?: string };
    message?: string | null;
};
// Após restart: lê sentinel → resolve contexto → notifica usuário que pediu restart
```

### 6.3 Referência: VS Code — Pending Request Queues

```typescript
// PersistentProtocol — mensagens em trânsito são mantidas
public get unacknowledgedCount(): number {
    return this._outgoingMsgId - this._outgoingAckId;
}

// Disconnect gracioso:
sendDisconnect(): void {
    if (!this._didSendDisconnect) {
        this._didSendDisconnect = true;
        const msg = new ProtocolMessage(ProtocolMessageType.Disconnect, 0, 0, getEmptyBuffer());
        this._socketWriter.write(msg);
        this._socketWriter.flush();  // ← garante envio antes de fechar
    }
}
```

### 6.4 Proposta para o RLM

Expandir `rlm/server/api.py` lifespan shutdown:

```python
# Fase 1: Stop accepting new requests
gateway_log.info("Drain phase: rejecting new requests...")
app.state.draining = True  # middleware rejeita novas mensagens com 503

# Fase 2: Wait for in-flight operations
active = app.state.supervisor.get_active_sessions()
if active:
    gateway_log.info(f"Waiting for {len(active)} active operations (max 30s)...")
    deadline = time.time() + 30.0
    while app.state.supervisor.get_active_sessions() and time.time() < deadline:
        await asyncio.sleep(0.5)

# Fase 3: Notify connected clients
app.state.event_bus.emit("system.shutdown", {"reason": "graceful", "drain_s": 30})

# Fase 4: Stop subsystems (existing)
app.state.scheduler.stop()
app.state.skill_loader.deactivate_all()
app.state.supervisor.shutdown()
app.state.session_manager.close_all()

# Fase 5: Close WebSocket connections
for client in list(app.state.ws_clients):
    await client.close(code=1012, reason="service restart")
```

---

## 7. Gap 6 — Backpressure / Flow Control

### 7.1 Estado Atual do RLM

```python
# telegram_gateway.py — aceita tudo, sem limite de concorrência
def _handle_update(self, update):
    # ... extrai chat_id, text ...
    # Rate limit por chat_id (sliding window), mas não por total
    if not self.rate_limiter.allow(chat_id):
        _send_message(self.token, chat_id, "⏳ Limite de mensagens atingido.")
        return
    # Processa sem limite de concorrência global
    response = self._process_message(chat_id, text, username)
```

**Problema**: Se 100 mensagens chegam de 100 chats diferentes em 1s, todas serão processadas simultaneamente. Não há semaforo global. O processo pode ficar sem memória ou saturar a API do LLM.

### 7.2 Referência: VS Code — Pause/Resume Explícito

```typescript
// ipc.net.ts — ProtocolWriter
public pause(): void {
    this._isPaused = true;
}
public resume(): void {
    this._isPaused = false;
    this._scheduleWriting();
}

// PersistentProtocol — envia Pause/Resume como mensagem de protocolo
sendPause(): void {
    const msg = new ProtocolMessage(ProtocolMessageType.Pause, 0, 0, getEmptyBuffer());
    this._socketWriter.write(msg);
}
sendResume(): void {
    const msg = new ProtocolMessage(ProtocolMessageType.Resume, 0, 0, getEmptyBuffer());
    this._socketWriter.write(msg);
}

// Recepção: ao receber Pause, o writer para de enviar
case ProtocolMessageType.Pause:
    this._socketWriter.pause();
    break;
case ProtocolMessageType.Resume:
    this._socketWriter.resume();
    break;
```

```typescript
// LoadEstimator — mede carga do event loop
class LoadEstimator {
    private static _HISTORY_LENGTH = 10;
    public hasHighLoad(): boolean {
        return this.load() >= 0.5;  // >50% uso → load alta
    }
}
```

### 7.3 Referência: OpenClaw — Buffer Limit

```typescript
// WebSocket: se buffer > MAX → drop frame
// src/gateway/server-constants.ts
export const MAX_BUFFERED_BYTES = 50 * 1024 * 1024;  // 50MB buffer limit
```

### 7.4 Proposta para o RLM

```python
# rlm/server/backpressure.py

class ConcurrencyGate:
    """Semáforo global para limitar processamento simultâneo."""
    def __init__(self, max_concurrent: int = 10):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._waiting = 0
        self._active = 0

    async def acquire(self, timeout: float = 30.0) -> bool:
        """Retorna False se timeout expirar (rejeita com 503)."""
        self._waiting += 1
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout)
            self._active += 1
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiting -= 1

    def release(self):
        self._active -= 1
        self._semaphore.release()

    @property
    def pressure(self) -> float:
        """0.0 = idle, 1.0 = totalmente saturado."""
        return self._active / self._semaphore._value if self._semaphore._value else 1.0
```

---

## 8. Gap 7 — Message Deduplication

### 8.1 Estado Atual do RLM

Nenhuma proteção contra duplicatas. WhatsApp Meta API re-entrega webhooks se o HTTP 200 não chega a tempo.

### 8.2 Referência: OpenClaw — TTL-based Dedup

```typescript
// src/gateway/server-constants.ts
export const DEDUPE_TTL_MS = 5 * 60_000;  // 5min TTL
export const DEDUPE_MAX = 1000;             // max 1000 entries

// src/gateway/server-shared.ts
export type DedupeEntry = {
    ts: number;
    ok: boolean;
    payload?: unknown;
    error?: ErrorShape;
};
```

### 8.3 Referência: VS Code — Sequence Numbers

```typescript
// ipc.net.ts — PersistentProtocol
// Cada mensagem tem ID sequencial (u32be no header)
send(buffer: VSBuffer): void {
    const myId = ++this._outgoingMsgId;
    const msg = new ProtocolMessage(ProtocolMessageType.Regular, myId, ...);
    this._outgoingUnackMsg.push(msg);
    // ...
}

// Recepção: rejeita mensagem se ID ≤ último recebido
case ProtocolMessageType.Regular: {
    if (msg.id > this._incomingMsgId) {
        if (msg.id !== this._incomingMsgId + 1) {
            // Gap detectado → pede replay
            this._socketWriter.write(new ProtocolMessage(
                ProtocolMessageType.ReplayRequest, 0, 0, getEmptyBuffer()));
        } else {
            this._incomingMsgId = msg.id;
            // ... processa ...
        }
    }
    // msg.id <= _incomingMsgId → IGNORADA (duplicata)
    break;
}
```

### 8.4 Proposta para o RLM

```python
# rlm/server/dedup.py
from collections import OrderedDict

class MessageDedup:
    """Deduplicação baseada em TTL com LRU eviction."""
    def __init__(self, ttl_s: float = 300.0, max_entries: int = 1000):
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._ttl = ttl_s
        self._max = max_entries

    def is_duplicate(self, message_id: str) -> bool:
        now = time.monotonic()
        self._prune(now)
        if message_id in self._seen:
            return True
        self._seen[message_id] = now
        if len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return False

    def _prune(self, now: float):
        cutoff = now - self._ttl
        while self._seen:
            key, ts = next(iter(self._seen.items()))
            if ts > cutoff:
                break
            self._seen.popitem(last=False)
```

Usar nos gateways webhook:
```python
# whatsapp_gateway.py
dedup = MessageDedup(ttl_s=300, max_entries=1000)

async def whatsapp_inbound(request):
    message_id = extract_message_id(payload)
    if dedup.is_duplicate(message_id):
        return JSONResponse({"status": "duplicate"}, status_code=200)
    ...
```

---

## 9. Gap 8 — Outbound Chunking Inteligente

### 9.1 Estado Atual do RLM

```python
# telegram_gateway.py — truncagem cega
def _send_message(token, chat_id, text, parse_mode="Markdown"):
    if len(text) > 4000:
        text = text[:4000] + "\n\n[...truncado]"
```

**Problema**: Corta em 4000 chars sem respeitar markdown. Pode cortar no meio de um code block, table, ou link. Não faz multi-message split.

### 9.2 Referência: OpenClaw — Chunker com Adapter per-Channel

```typescript
// src/channels/plugins/outbound/direct-text-media.ts

async function sendTextMediaPayload(params) {
    // Mídia: enviar uma mensagem por arquivo
    if (urls.length > 0) {
        let lastResult = await params.adapter.sendMedia!({ ...params.ctx, text, mediaUrl: urls[0] });
        for (let i = 1; i < urls.length; i++) {
            lastResult = await params.adapter.sendMedia!({ ...params.ctx, text: "", mediaUrl: urls[i] });
        }
        return lastResult;
    }

    // Texto: chunk se necessário (adapter define limite e chunker)
    const limit = params.adapter.textChunkLimit;  // Telegram: 4000, Discord: 2000
    const chunks = limit && params.adapter.chunker
        ? params.adapter.chunker(text, limit)     // ← funçao PER-CHANNEL
        : [text];

    let lastResult;
    for (const chunk of chunks) {
        lastResult = await params.adapter.sendText!({ ...params.ctx, text: chunk });
    }
    return lastResult!;
}
```

Cada channel define:
- `textChunkLimit` (Telegram: 4000, Discord: 2000, SMS: 160)
- `chunker(text, limit)` que sabe quebrar respeitando formatação

### 9.3 Proposta para o RLM

```python
# rlm/server/chunker.py

CHANNEL_LIMITS = {
    "telegram": 4000,
    "discord": 2000,
    "slack": 3000,
    "whatsapp": 4096,
    "sms": 160,
}

def smart_chunk(text: str, limit: int) -> list[str]:
    """Quebra texto respeitando boundaries naturais."""
    if len(text) <= limit:
        return [text]

    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        # Tentar quebrar em ordem de preferência:
        # 1. Parágrafo (\n\n)
        # 2. Fim de code block (```)
        # 3. Nova linha (\n)
        # 4. Espaço
        # 5. Forçar no limite
        cut = _find_best_break(text, limit)
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()

    return chunks

def _find_best_break(text: str, limit: int) -> int:
    for sep in ["\n\n", "```\n", "\n", " "]:
        idx = text.rfind(sep, 0, limit)
        if idx > limit // 4:  # pelo menos 25% do chunk
            return idx + len(sep)
    return limit  # fallback: corte forçado
```

---

## 10. Gap 9 — Heartbeat / Keepalive

### 10.1 Estado Atual do RLM

```python
# telegram_gateway.py — typing thread (EXISTE, mas só no Telegram)
def keep_typing():
    while not done_event.is_set():
        _send_typing(self.token, chat_id)
        done_event.wait(timeout=4.0)
```

Nos outros gateways: **nenhum heartbeat**. Se o LLM demora 60s, o canal não recebe sinal de vida.

### 10.2 Referência: VS Code — KeepAlive de 5s

```typescript
// ipc.net.ts
const enum ProtocolConstants {
    KeepAliveSendTime = 5000,  // 5s
}

// PersistentProtocol constructor:
this._keepAliveInterval = setInterval(() => {
    this._incomingAckId = this._incomingMsgId;
    const msg = new ProtocolMessage(ProtocolMessageType.KeepAlive, 0, this._incomingAckId, getEmptyBuffer());
    this._socketWriter.write(msg);
}, ProtocolConstants.KeepAliveSendTime);
```

### 10.3 Referência: OpenClaw — RunStateMachine 60s Heartbeat

```typescript
// src/channels/run-state-machine.ts
const DEFAULT_RUN_ACTIVITY_HEARTBEAT_MS = 60_000;  // 60s

function createRunStateMachine(params) {
    return {
        onRunStart() {
            activeRuns += 1;
            publish();
            ensureHeartbeat();  // ← inicia interval de 60s
        },
        onRunEnd() {
            activeRuns = Math.max(0, activeRuns - 1);
            if (activeRuns <= 0) clearHeartbeat();
            publish();
        },
    };
}
```

### 10.4 Referência: OpenClaw — Stall Watchdog

```typescript
// src/channels/transport/stall-watchdog.ts
function createArmableStallWatchdog(params: {
    label: string;
    timeoutMs: number;           // quanto tempo sem atividade = stall
    checkIntervalMs?: number;    // default: timeoutMs/6
    onTimeout: (meta) => void;   // callback quando travou
}): ArmableStallWatchdog {
    const check = () => {
        if (!armed || stopped) return;
        const idleMs = Date.now() - lastActivityAt;
        if (idleMs >= timeoutMs) {
            disarm();
            params.onTimeout({ idleMs, timeoutMs });
        }
    };
    timer = setInterval(check, checkIntervalMs);
    return { arm, touch, disarm, stop, isArmed };
}
```

### 10.5 Proposta para o RLM

Propagar o padrão typing do Telegram para TODOS os gateways:

```python
# rlm/server/heartbeat.py

class ProcessingHeartbeat:
    """Envia sinais de vida durante processamento longo."""
    def __init__(self, channel: str, chat_id: str, send_fn: Callable, interval_s: float = 5.0):
        self._done = threading.Event()
        self._thread = None
        self._send_fn = send_fn
        self._interval = interval_s

    def start(self):
        def _loop():
            while not self._done.wait(timeout=self._interval):
                try:
                    self._send_fn()
                except Exception:
                    pass
        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._done.set()
        if self._thread:
            self._thread.join(timeout=2.0)
```

Mapa de heartbeat per-channel:
```python
HEARTBEAT_ACTIONS = {
    "telegram": lambda token, chat_id: _send_typing(token, chat_id),
    "discord": lambda token, interaction: edit_deferred("⏳ Processando..."),
    "slack": lambda token, channel: update_message("⏳ ..."),
    "whatsapp": lambda token, phone: mark_as_read(...),
    "webchat": lambda session: emit_event("typing", {}),
}
```

---

## 11. Gap 10 — Streaming de Resposta

### 11.1 Estado Atual do RLM

```python
# openai_compat.py — streaming SIMULADO (não real)
def _iter_sse_chunks(run_id, model, content, chunk_size=20):
    # Recebe o conteúdo COMPLETO, depois fatia em pedaços de 20 chars
    for i in range(0, len(content), chunk_size):
        piece = content[i:i+chunk_size]
        yield f"data: {json.dumps(...)}\n\n"
```

**Problema**: O RLM espera a resposta INTEIRA do LLM antes de enviar qualquer coisa ao usuário. O "streaming" no OpenAI compat é fake — recebe tudo, depois fatia.

### 11.2 Referência: OpenClaw — DraftStreamLoop Throttled

```typescript
// src/channels/draft-stream-loop.ts

function createDraftStreamLoop(params: {
    throttleMs: number;                                    // mínimo entre envios
    isStopped: () => boolean;
    sendOrEditStreamMessage: (text: string) => Promise<void | boolean>;
}): DraftStreamLoop {
    let lastSentAt = 0;
    let pendingText = "";
    let inFlightPromise: Promise<void | boolean> | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const flush = async () => {
        if (timer) { clearTimeout(timer); timer = undefined; }
        while (!params.isStopped()) {
            if (inFlightPromise) { await inFlightPromise; continue; }
            const text = pendingText;
            if (!text.trim()) { pendingText = ""; return; }
            pendingText = "";
            const current = params.sendOrEditStreamMessage(text).finally(() => {
                if (inFlightPromise === current) inFlightPromise = undefined;
            });
            inFlightPromise = current;
            const sent = await current;
            if (sent === false) { pendingText = text; return; }  // backpressure
            lastSentAt = Date.now();
            if (!pendingText) return;
        }
    };

    return {
        update(text) {
            pendingText = text;  // sempre o texto completo acumulado
            if (!timer && !inFlightPromise) {
                const elapsed = Date.now() - lastSentAt;
                const wait = Math.max(0, params.throttleMs - elapsed);
                timer = setTimeout(flush, wait);
            }
        },
        flush,
        stop() { /* ... */ },
        waitForInFlight() { return inFlightPromise ?? Promise.resolve(); },
    };
}
```

**Padrões-chave OpenClaw:**
- `update()` recebe o texto acumulado completo (não delta)
- Throttle evita spam (configurable por canal — Telegram: 3s, Discord: 1s)
- In-flight tracking: não envia novo enquanto anterior não confirmou
- Backpressure: `sent === false` → guarda texto, tenta de novo depois
- `sendOrEditStreamMessage` EDITA a mensagem anterior (não envia nova)

### 11.3 Proposta para o RLM

Este é o gap mais complexo. Requer mudança no `supervisor.execute()` para yield parciais.

**Fase 1 — WebSocket streaming (mais simples):**
```python
# ws_server.py — já tem broadcast
# Modificar supervisor para emitir eventos parciais:
event_bus.emit("completion.chunk", {"session_id": sid, "delta": "parcial..."})
# Clientes WS recebem em tempo real
```

**Fase 2 — OpenAI compat real streaming:**
```python
# Modificar completion() para ser async generator:
async def completion_stream(session, prompt):
    async for chunk in llm_backend.stream(prompt):
        yield chunk
```

**Fase 3 — Channel streaming (edit-based):**
```python
# Telegram: editar mensagem a cada N segundos
# Discord: editar follow-up via webhook
# WebChat: SSE events
```

---

## 12. Padrões Existentes no RLM que Devem Ser Preservados

### 12.1 Scheduler Proativo (Vantagem Única)

```python
# scheduler.py — 800 linhas, SQLite persistence, 4 tipos de trigger
# NÃO EXISTE no VS Code nem no OpenClaw
class RLMScheduler:
    def add(self, prompt, *, cron=None, once=None, interval_s=None, condition=None):
        ...
    def _tick(self):
        due = self.store.get_due(now)
        for task in due:
            if active_count >= self.max_workers:
                continue
            self._dispatch(task)
    def _wait_active(self, timeout_s=30.0):  # ← graceful drain!
        ...
```

### 12.2 Exec Approval Gate (Vantagem Única)

```python
# api.py — human-in-the-loop
POST /exec/approve/{request_id}
POST /exec/deny/{request_id}
GET /exec/pending
```

### 12.3 Security Pipeline (Superior)

```python
# security.py — 20+ patterns, env var shield, REPL sandbox
# NÃO EXISTE no VS Code (confia no host) nem no OpenClaw (foco em channels)
class InputThreatReport:
    is_suspicious: bool
    threat_level: str  # clean/low/medium/high
    patterns_found: list[str]
```

### 12.4 Event Router com Glob Matching

```python
# event_router.py — glob routing → prompt template
# OpenClaw tem routing diferente (7-tier binding)
# VS Code não tem (IPC direto)
class EventRouter:
    def route(self, client_id, payload) -> (prompt, plugins):
        for route in self.routes:
            if self._matches(route.source_pattern, client_id):
                return (self._format_prompt(route.prompt_template, ...), route.plugins)
```

### 12.5 CancellationToken + DisposableStore (Já Portado)

```python
# cancellation.py — já inspirado no VS Code
# disposable.py — já inspirado no VS Code
# shutdown.py — veto system inspirado no VS Code lifecycle
# MANTER E EXPANDIR uso para backoff + health monitor
```

---

## 13. Matriz Completa de Comparação

| Capacidade | VS Code | OpenClaw | RLM Atual | Ação |
|---|---|---|---|---|
| **Protocolo** | Binary RPC 13-byte header | JSON HTTP/WS | JSON HTTP/WS | Manter |
| **State Machine** | 5 estados + eventos tipados | 7 razões de saúde + restart reasons | `_running` booleano | **IMPLEMENTAR** (Gap 1) |
| **Exp Backoff** | `[0,5,5,10,10,10,10,10,30]s` | `5s→300s, ×2, jitter 10%` | Fixo 5s | **IMPLEMENTAR** (Gap 2) |
| **Health Monitor** | ACK-based 3s + KeepAlive 5s | Periodic 5min + stale detection 30min | Só `/health` | **IMPLEMENTAR** (Gap 3) |
| **Msg Normalization** | Custom VSBuffer types | `MsgContext` ~80+ campos | Ad-hoc por gateway | **IMPLEMENTAR** (Gap 4) |
| **Graceful Drain** | Pending queue + Disconnect msg | 13-step shutdown + restart sentinel | Só scheduler | **IMPLEMENTAR** (Gap 5) |
| **Backpressure** | Pause/Resume + LoadEstimator | Buffer limit check | Nenhuma | **IMPLEMENTAR** (Gap 6) |
| **Dedup** | Sequence IDs + replay request | TTL 5min + max 1000 entries | Nenhuma | **IMPLEMENTAR** (Gap 7) |
| **Chunking** | N/A | Per-channel chunker + adapter | Trunca 4000 chars | **IMPLEMENTAR** (Gap 8) |
| **Heartbeat** | KeepAlive 5s | RunStateMachine 60s | Só typing no Telegram | **IMPLEMENTAR** (Gap 9) |
| **Streaming** | N/A | DraftStreamLoop throttled | Fake (post-hoc split) | **IMPLEMENTAR** (Gap 10) |
| **Auth** | N/A (IPC local) | OAuth + tokens | Ed25519/HMAC/Bearer | **MANTER** ✅ |
| **Rate Limit** | N/A | Inbound debounce + flood guard | Dual IP+client sliding window | **MANTER** ✅ |
| **Scheduler** | N/A | N/A | Cron/interval/once/condition 800 linhas | **MANTER** ✅ (vantagem) |
| **OpenAI Compat** | N/A | N/A | Drop-in `/v1/chat/completions` | **MANTER** ✅ (vantagem) |
| **Approval Gate** | N/A | N/A | Human-in-the-loop approve/deny | **MANTER** ✅ (vantagem) |
| **Security** | Sandbox host | Config-based | 20+ regex, REPL sandbox, env shield | **MANTER** ✅ (vantagem) |
| **Cancellation** | CancellationToken (nativo TS) | AbortController | CancellationToken/Source | **MANTER** ✅ (portado) |
| **Disposable** | DisposableStore (nativo TS) | N/A | DisposableStore portado | **MANTER** ✅ (portado) |
| **Shutdown** | Lifecycle veto | Restart sentinel | ShutdownManager veto | **EXPANDIR** (usar em drain) |
| **Multi-account** | N/A | N accounts/channel + per-account lifecycle | 1 bot/processo | **FUTURA** |
| **Session routing** | N/A | 7-tier binding + WeakMap cache | `prefix:id` split | **FUTURA** |
| **Config reload** | N/A | Hot reload + restart deferral | Nenhum (precisa restart) | **FUTURA** |
| **Stall Watchdog** | Timeout 20s → reconnect | Armable watchdog (configurable) | Nenhum | **FUTURA** |
| **Connection Pool** | N/A | Per-account connection reuse | Nenhum (urllib fresh) | **FUTURA** |
| **Event FRP** | `map|filter|debounce|throttle|split|latch|buffer` | WS broadcaster + scope guards | `RLMEventBus.emit()` básico | **FUTURA** |

---

## 14. Plano de Implementação Priorizado

### Tier 1 — Quick Wins (~150 linhas total, 3 gaps eliminados)

| Prioridade | Gap | Arquivo a Criar | Complexidade | Dependências |
|---|---|---|---|---|
| **P1.1** | G2: Backoff | `rlm/server/backoff.py` | ~40 linhas | Nenhuma |
| **P1.2** | G7: Dedup | `rlm/server/dedup.py` | ~30 linhas | Nenhuma |
| **P1.3** | G9: Heartbeat | `rlm/server/heartbeat.py` | ~50 linhas | Nenhuma |
| **P1.4** | Integrar nos gateways | Modif. `telegram_gateway.py`, `whatsapp_gateway.py` | ~30 linhas de diff | P1.1, P1.2 |

### Tier 2 — Infraestrutura Core (~500 linhas total, 4 gaps essenciais)

| Prioridade | Gap | Arquivo a Criar | Complexidade | Dependências |
|---|---|---|---|---|
| **P2.1** | G1: State Machine | `rlm/server/gateway_state.py` | ~120 linhas | P1.1 (backoff) |
| **P2.2** | G4: Normalization | `rlm/server/message_envelope.py` + `normalize/` | ~200 linhas | Nenhuma |
| **P2.3** | G3: Health Monitor | `rlm/server/health_monitor.py` | ~130 linhas | P2.1 (state machine) |
| **P2.4** | G5: Graceful Drain | Modif. `api.py` lifespan | ~80 linhas de diff | Nenhuma |

### Tier 3 — Sofisticação (~300 linhas total, 3 gaps de polimento)

| Prioridade | Gap | Arquivo a Criar | Complexidade | Dependências |
|---|---|---|---|---|
| **P3.1** | G6: Backpressure | `rlm/server/backpressure.py` | ~60 linhas | Nenhuma |
| **P3.2** | G8: Chunking | `rlm/server/chunker.py` | ~80 linhas | Nenhuma |
| **P3.3** | G10: Streaming | Modif. `supervisor`, `openai_compat.py` | ~150+ linhas | Requer refactor `completion()` |

### Estimativa de Impacto Total

- **Tier 1**: Elimina thundering herd, duplicatas, e timeout silencioso em 3 gateways
- **Tier 2**: O sistema passa a saber seu próprio estado, normalizar mensagens, e fazer shutdown limpo
- **Tier 3**: Experiência de usuário melhora drasticamente (streaming, chunking inteligente, proteção contra sobrecarga)

### Ordem de Implementação Recomendada

```
P1.1 (backoff) → P1.2 (dedup) → P1.3 (heartbeat) → P1.4 (integrar)
    ↓
P2.1 (state machine) → P2.3 (health monitor)
P2.2 (normalization) → adaptar gateways
P2.4 (graceful drain)
    ↓
P3.1 (backpressure) → P3.2 (chunking) → P3.3 (streaming)
```

---

## Apêndice A — Constantes de Referência

### OpenClaw

| Constante | Valor | Arquivo |
|---|---|---|
| Backoff initial | 5,000ms | `server-channels.ts` |
| Backoff max | 300,000ms (5min) | `server-channels.ts` |
| Backoff factor | 2 | `server-channels.ts` |
| Backoff jitter | 0.1 (10%) | `server-channels.ts` |
| Max restart attempts | 10 | `server-channels.ts` |
| Health check interval | 300,000ms (5min) | `channel-health-monitor.ts` |
| Monitor startup grace | 60,000ms (1min) | `channel-health-monitor.ts` |
| Channel connect grace | 120,000ms (2min) | `channel-health-monitor.ts` |
| Stale event threshold | 1,800,000ms (30min) | `channel-health-monitor.ts` |
| Max restarts/hour | 10 | `channel-health-monitor.ts` |
| Busy stale threshold | 25min | `channel-health-policy.ts` |
| Dedup TTL | 300,000ms (5min) | `server-constants.ts` |
| Dedup max entries | 1,000 | `server-constants.ts` |
| Tick interval | 30,000ms (30s) | `server-constants.ts` |
| Health refresh | 60,000ms (1min) | `server-constants.ts` |
| Max payload | 25MB | `server-constants.ts` |
| Max buffered | 50MB | `server-constants.ts` |
| Auth rate limit window | 60,000ms (1min) | `auth-rate-limit.ts` |
| Auth lockout | 300,000ms (5min) | `auth-rate-limit.ts` |
| Auth max attempts | 10 | `auth-rate-limit.ts` |
| Control plane rate | 3 req/60s | `control-plane-rate-limit.ts` |
| Flood guard close-after | 10 unauthorized | `unauthorized-flood-guard.ts` |
| Run heartbeat | 60,000ms (60s) | `run-state-machine.ts` |

### VS Code

| Constante | Valor | Arquivo |
|---|---|---|
| Protocol header | 13 bytes | `ipc.net.ts` |
| ACK time | 2,000ms (2s) | `ipc.net.ts` |
| Socket timeout | 20,000ms (20s) | `ipc.net.ts` |
| Reconnection grace | 10,800,000ms (3h) | `ipc.net.ts` |
| Short grace | 300,000ms (5min) | `ipc.net.ts` |
| KeepAlive interval | 5,000ms (5s) | `ipc.net.ts` |
| Unresponsive threshold | 3,000ms (3s) | `rpcProtocol.ts` |
| Reconnect timeout | 30,000ms (30s) | `remoteAgentConnection.ts` |
| Backoff sequence | [0,5,5,10,10,10,10,10,30]s | `remoteAgentConnection.ts` |
| Crash limit | 3 | `abstractExtensionService.ts` |
| Crash window | 300,000ms (5min) | `abstractExtensionService.ts` |
| Initial connect attempts | 5 | `remoteAgentConnection.ts` |
| ReplayRequest throttle | 10,000ms (10s) | `ipc.net.ts` |
| Load estimator history | 10 samples | `ipc.net.ts` |
| High load threshold | 0.5 (50%) | `ipc.net.ts` |

### RLM (Atual)

| Constante | Valor | Arquivo |
|---|---|---|
| Error backoff | 5.0s (fixo) | `telegram_gateway.py` |
| Max consecutive errors | 10 | `telegram_gateway.py` |
| Rate limit (Telegram) | 30 req/60s per chat_id | `telegram_gateway.py` |
| Rate limit (Webhook) | configurable via env | `webhook_dispatch.py` |
| Long-poll timeout | 30s | `telegram_gateway.py` |
| Typing interval | 4s | `telegram_gateway.py` |
| Scheduler poll | 15s | `scheduler.py` |
| Scheduler max workers | 4 | `scheduler.py` |
| Scheduler drain timeout | 30s | `scheduler.py` |
| WS history buffer | 500 events | `ws_server.py` |
| Truncation limit | 4000 chars | `telegram_gateway.py` |
| Replay protection | 300s (Slack timestamp) | `slack_gateway.py` |

---

## Apêndice B — Arquivos Fonte Consultados

### RLM (`rlm/server/`)
- `api.py` (~780 linhas)
- `telegram_gateway.py` (~600 linhas)
- `scheduler.py` (~800 linhas)
- `webhook_dispatch.py` (~280 linhas)
- `ws_server.py` (~280 linhas)
- `event_router.py` (~280 linhas)
- `openai_compat.py` (~260 linhas)
- `whatsapp_gateway.py` (~290 linhas)
- `slack_gateway.py` (~245 linhas)
- `discord_gateway.py` (~232 linhas)
- `webchat.py` (~270 linhas)
- `channel_registry.py` (~160 linhas)
- `runtime_pipeline.py` (~620 linhas)
- `auth_helpers.py` (~86 linhas)

### RLM (`rlm/core/`)
- `shutdown.py` (~165 linhas)
- `disposable.py` (~175 linhas)
- `cancellation.py` (~185 linhas)
- `comms_utils.py` (~200 linhas)
- `security.py` (~200 linhas)

### OpenClaw
- `src/infra/backoff.ts` (~30 linhas)
- `src/gateway/server-channels.ts` (~450 linhas)
- `src/gateway/channel-health-monitor.ts` (~180 linhas)
- `src/gateway/channel-health-policy.ts` (~120 linhas)
- `src/channels/transport/stall-watchdog.ts` (~100 linhas)
- `src/channels/run-state-machine.ts` (~90 linhas)
- `src/auto-reply/templating.ts` (MsgContext ~170 linhas)
- `src/channels/draft-stream-loop.ts` (~100 linhas)
- `src/channels/draft-stream-controls.ts` (~130 linhas)
- `src/channels/plugins/outbound/direct-text-media.ts` (~180 linhas)
- `src/channels/session.ts` (~80 linhas)
- `src/routing/resolve-route.ts` (~100 linhas)
- `src/routing/bindings.ts` (~100 linhas)
- `src/gateway/auth-rate-limit.ts` (~200 linhas)
- `src/gateway/server-constants.ts` (~35 linhas)
- `src/channels/inbound-debounce-policy.ts` (~55 linhas)
- `src/gateway/server-close.ts` (~120 linhas)
- `src/infra/restart-sentinel.ts` (~150 linhas)
- `src/gateway/config-reload.ts` (~200 linhas)
- `src/gateway/server-reload-handlers.ts` (~200 linhas)
- `src/channels/plugins/normalize/*.ts` (7 arquivos)

### VS Code
- `src/vs/base/parts/ipc/common/ipc.net.ts` (~1200 linhas)
- `src/vs/platform/remote/common/remoteAgentConnection.ts` (~880 linhas)
- `src/vs/workbench/services/extensions/common/rpcProtocol.ts` (~1000 linhas)
- `src/vs/workbench/services/extensions/common/abstractExtensionService.ts` (~1600 linhas)
- `src/vs/base/parts/ipc/common/ipc.ts` (~1130 linhas)
- `src/vs/base/common/event.ts` (~1850 linhas)
- `src/vs/base/common/lifecycle.ts` (~100 linhas)
