# Integração de Grande Escala: Python + TypeScript + Rust

> **Referência técnica completa** — comportamento, ligações entre linguagens,
> mecanismos de conexão, padrões da indústria e exemplos reais do projeto RLM.

---

## Sumário

1. [Visão Geral da Arquitetura](#1-visão-geral-da-arquitetura)
2. [Bridge 1 — Rust → Python via PyO3 + Maturin](#2-bridge-1--rust--python-via-pyo3--maturin)
3. [Bridge 2 — Rust → TypeScript via NAPI-RS](#3-bridge-2--rust--typescript-via-napi-rs)
4. [Bridge 3 — TypeScript ↔ Python via WebSocket + JSON Schema](#4-bridge-3--typescript--python-via-websocket--json-schema)
5. [Decisões de Design Fundamentais](#5-decisões-de-design-fundamentais)
6. [Padrões de Integração da Indústria](#6-padrões-de-integração-da-indústria)
7. [Build & Deploy das Pontes](#7-build--deploy-das-pontes)
8. [Roadmap: Crates Rust Pendentes](#8-roadmap-crates-rust-pendentes)
9. [Comparativo: Alternativas Rejeitadas](#9-comparativo-alternativas-rejeitadas)
10. [Referências](#10-referências)

---

## 1. Visão Geral da Arquitetura

O projeto usa uma arquitetura de **três camadas** onde cada linguagem domina um
domínio específico, e duas classes de pontes conectam as camadas:

```
┌────────────────────────────────────────────────────────┐
│               CAMADA TYPESCRIPT                        │
│   Hono Gateway · oclif CLI · Ink TUI · WsBridge        │
│   packages/gateway/  packages/cli/  packages/tui/      │
└────────────────┬───────────────────────────────────────┘
                 │  Bridge 3 → WebSocket + JSON Schema
                 │  schemas/envelope.v1.json (contrato único)
                 │  ws (npm) ↔ fastapi.WebSocket
┌────────────────▼───────────────────────────────────────┐
│               CAMADA PYTHON                            │
│   FastAPI Brain · Orchestrador · LLM · Skills · RAG    │
│   rlm/server/  rlm/core/  rlm/skills/                  │
└────────────────┬───────────────────────────────────────┘
                 │  Bridge 1 → PyO3 FFI (cdylib wheel)
                 │  maturin build --release → .whl → pip install
                 │  Python chama arkhe_memory / arkhe_wire
┌────────────────▼───────────────────────────────────────┐
│               CAMADA RUST                              │
│   arkhe-memory (HNSW · ANN) · arkhe-wire (protocol)   │
│   arkhe-vault (enc) · arkhe-policy-core (roadmap)      │
│   native/  (cargo workspace)                           │
└────────────────────────────────────────────────────────┘
```

**Regra de ouro:** cada bridge existe para um motivo concreto e medido:
- **Rust ↔ Python** → desempenho em hot paths (busca vetorial, serialização)
- **TypeScript ↔ Python** → isolamento de processo, escalabilidade independente
- **Rust ↔ TypeScript** → **não usado** — optamos por IPC sobre NAPI-RS direto

---

## 2. Bridge 1 — Rust → Python via PyO3 + Maturin

### 2.1 Mecanismo

PyO3 compila código Rust como uma **biblioteca dinâmica C** (`cdylib`) que o
interpretador CPython carrega diretamente — sem subprocess, sem socket, sem
overhead de serialização de rede. A chamada é uma chamada de função nativa.

```
CPython Runtime
      │
      │  import arkhe_memory
      ▼
arkhe_memory.cpython-313-win_amd64.pyd  ← DLL compilada em Rust
      │
      │  chama Rust via Foreign Function Interface (FFI)
      ▼
HnswIndex::search()  ← Rust puro, thread-safe via Arc<T>
```

### 2.2 Macros Essenciais do PyO3

| Macro | O que faz | Onde aparece no projeto |
|---|---|---|
| `#[pyclass]` | Expõe uma struct Rust como classe Python | `ArkheVectorIndex` |
| `#[pymethods]` | Expõe métodos da classe ao Python | `impl ArkheVectorIndex` |
| `#[pyfunction]` | Expõe função livre ao Python | `wire_json_dumps`, `wire_frame_encode` |
| `#[pymodule]` | Registra e exporta o módulo `.pyd` | `fn arkhe_wire(m: ...)` |

### 2.3 Código Real — `native/arkhe-memory/src/pybridge.rs`

```rust
use pyo3::prelude::*;
use std::sync::Arc;
use crate::HnswIndex;

#[pyclass]
pub struct ArkheVectorIndex {
    inner: Arc<HnswIndex>,  // Arc permite compartilhamento seguro entre threads Python
}

#[pymethods]
impl ArkheVectorIndex {
    #[new]
    #[pyo3(signature = (dim, m=16, ef_construction=200, ef_search=50))]
    fn new(dim: usize, m: usize, ef_construction: usize, ef_search: usize)
        -> PyResult<Self>
    {
        Ok(Self { inner: Arc::new(HnswIndex::new(dim, m, ef_construction, ef_search)) })
    }

    fn add(&self, id: String, embedding: Vec<f32>) -> PyResult<()> {
        self.inner.add(&id, &embedding)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
    }

    fn search(&self, query: Vec<f32>, top_k: Option<usize>)
        -> PyResult<Vec<(String, f32)>>
    {
        // Retorna Vec de (id, score) direto para Python
        self.inner.search(&query, top_k.unwrap_or(10))
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
    }
}

#[pymodule]
fn arkhe_memory(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ArkheVectorIndex>()?;
    Ok(())
}
```

### 2.4 Código Real — `native/arkhe-wire/src/pybridge.rs`

```rust
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString};

/// Substitui: _normalize_json_value(obj) → orjson.dumps(normalized)
/// Speedup medido: 7.4x sobre stdlib json
#[pyfunction]
fn wire_json_dumps<'py>(py: Python<'py>, obj: &Bound<'py, PyAny>)
    -> PyResult<Bound<'py, PyBytes>>
{
    // normaliza + serializa em um único passo Rust
    let bytes = serialize_to_wire_bytes(obj)?;
    Ok(PyBytes::new(py, &bytes))
}

/// Produz: [4 bytes big-endian length][JSON payload]
#[pyfunction]
fn wire_frame_encode<'py>(py: Python<'py>, obj: &Bound<'py, PyAny>)
    -> PyResult<Bound<'py, PyBytes>>

/// Substitui lone surrogates D800-DFFF por U+FFFD
#[pyfunction]
fn sanitize_surrogates(s: &Bound<'_, PyString>) -> PyResult<String>

#[pymodule]
fn arkhe_wire(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(wire_json_dumps, m)?)?;
    m.add_function(wrap_pyfunction!(wire_frame_encode, m)?)?;
    m.add_function(wrap_pyfunction!(sanitize_surrogates, m)?)?;
    Ok(())
}
```

### 2.5 Mapeamento de Tipos Python ↔ Rust

| Python | Rust | Notas |
|---|---|---|
| `list[float]` | `Vec<f32>` | conversão automática |
| `list[list[float]]` | `Vec<Vec<f32>>` | para `bulk_add` |
| `dict` / `Any` | `serde_json::Value` | via feature `serde-json` |
| `bytes` | `&[u8]` / `Vec<u8>` | `PyBytes` |
| `str` | `String` / `&str` | UTF-8 garantido |
| `int` | `i32` / `i64` / `u32` | conforme assinatura |
| `float` | `f64` | padrão Python float |
| `None` | `Option<T>` | `None` → `None` |
| exceção Python | `PyResult<T>` + `Err(...)` | `PyValueError::new_err(msg)` |

### 2.6 GIL — Global Interpreter Lock

O CPython tem um lock global (GIL) que impede execução paralela de threads
Python. Toda interação Rust↔Python deve respeitar o GIL:

```rust
// Adquire o GIL antes de interagir com objetos Python
Python::with_gil(|py| {
    let obj = py.eval("{'key': 'value'}", None, None)?;
    Ok(())
});

// Libera o GIL durante operações Rust puras (permite outros threads rodarem)
py.allow_threads(|| {
    // código Rust heavy — busca HNSW, compressão, IO, etc.
    hnsw_index.search(&query, top_k)
})
```

**Dica de desempenho:** Para operações Rust computacionalmente intensas (como
busca vetorial), libere o GIL com `py.allow_threads()` — isso permite que outras
threads Python rodem enquanto o Rust trabalha.

### 2.7 Thread Safety: `Arc<T>` + `parking_lot`

```rust
use std::sync::Arc;
use parking_lot::RwLock;  // preferível a std::sync::RwLock (sem poison)

#[pyclass]
pub struct ArkheVectorIndex {
    inner: Arc<RwLock<HnswIndex>>,
}
// Arc permite que múltiplas threads Python segurem referências ao mesmo índice
// RwLock permite leituras paralelas, escritas exclusivas
```

### 2.8 Padrão de Fallback Gracioso

Sempre que um módulo Rust não está disponível (sem wheel instalado), o código
Python deve ter fallback:

```python
# rlm/core/memory_index.py  (padrão real do projeto)
try:
    from arkhe_memory import ArkheVectorIndex
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    ArkheVectorIndex = None  # fallback para implementação Python pura

def build_index(dim: int):
    if _RUST_AVAILABLE:
        return ArkheVectorIndex(dim=dim)
    return PythonFallbackIndex(dim=dim)  # ~10x mais lento mas funcional
```

### 2.9 Build Workflow Completo

```bash
# 1. Compilar e gerar wheel
cd native/arkhe-memory
maturin build --release
# → Produz: target/wheels/arkhe_memory-0.1.0-cp313-cp313-win_amd64.whl

# 2. Instalar no ambiente virtual
pip install target/wheels/arkhe_memory-0.1.0-*.whl --force-reinstall

# 3. Desenvolvimento local (link direto, sem wheel)
maturin develop --release
# → instala diretamente no .venv ativo (sem gerar .whl)

# 4. Cross-compile (ex: Linux em Windows)
maturin build --release --target x86_64-unknown-linux-gnu
```

### 2.10 `Cargo.toml` de um Módulo PyO3

```toml
[package]
name = "arkhe-memory"
version = "0.1.0"
edition = "2021"

[lib]
name = "arkhe_memory"      # nome do módulo Python: import arkhe_memory
crate-type = ["cdylib"]    # obrigatório: gera DLL (não executável)

[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
parking_lot = "0.12"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### 2.11 Quem Usa PyO3 na Indústria

| Projeto | Speedup | O que acelerou |
|---|---|---|
| **orjson** | 10–20x vs stdlib | Serialização JSON |
| **polars** | 5–50x vs pandas | DataFrames |
| **pydantic-core** | 5x vs pydantic v1 | Validação de modelos |
| **tiktoken** (OpenAI) | 3–5x | Tokenização BPE |
| **tokenizers** (HuggingFace) | 10x | Tokenização NLP |
| **cryptography** | N/A | Primitivos criptográficos |

---

## 3. Bridge 2 — Rust → TypeScript via NAPI-RS

> **Status no projeto RLM:** **NÃO UTILIZADO** — escolhemos WebSocket IPC
> (Bridge 3) para comunicação TS↔Python, o que isola os processos.
> Esta seção documenta o padrão para uso futuro.

### 3.1 O que é NAPI-RS

NAPI-RS compila código Rust como um **addon nativo Node.js** (`.node` — um DLL
carregado via `process.dlopen()`). Usa a **N-API** (Node-API), uma API
ABI-estável que funciona em todas as versões do Node.js ≥ 10 sem recompilar.

```
Node.js Runtime (V8)
      │
      │  require('./core.win32-x64-msvc.node')
      ▼
core.win32-x64-msvc.node  ← DLL compilada em Rust
      │
      │  chama código Rust via N-API
      ▼
fn fibonacci(n: u32) -> u32  ← Rust puro
```

### 3.2 Sintaxe Básica NAPI-RS

```rust
use napi::bindgen_prelude::*;
use napi_derive::napi;

// Função simples
#[napi]
pub fn fibonacci(n: u32) -> u32 {
    match n {
        1 | 2 => 1,
        _ => fibonacci(n - 1) + fibonacci(n - 2),
    }
}

// Função assíncrona (retorna Promise em JS)
#[napi]
pub async fn read_file_async(path: String) -> Result<Buffer> {
    Ok(tokio::fs::read(path).await?.into())
}

// Classe (equivalente ao #[pyclass] do PyO3)
#[napi]
pub struct VectorIndex {
    inner: Arc<HnswIndex>,
}

#[napi]
impl VectorIndex {
    #[napi(constructor)]
    pub fn new(dim: u32) -> Self {
        Self { inner: Arc::new(HnswIndex::new(dim as usize)) }
    }

    #[napi]
    pub fn search(&self, query: Vec<f64>, top_k: u32) -> Vec<(String, f64)> {
        self.inner.search(&query.iter().map(|&x| x as f32).collect(), top_k as usize)
    }
}
```

### 3.3 Mapeamento de Tipos TypeScript ↔ Rust (NAPI-RS)

| TypeScript | Rust | N-API Version |
|---|---|---|
| `number` | `u32` / `i32` / `i64` / `f64` | v1 |
| `boolean` | `bool` | v1 |
| `string` | `String` / `&str` | v1 |
| `object` | `serde_json::Map` | v1 (feature `serde-json`) |
| `any` | `serde_json::Value` | v1 |
| `T[]` / `Array` | `Vec<T>` | v1 |
| `Buffer` | `Vec<u8>` | v1 |
| `Promise<T>` | `async fn` / `AsyncTask` | v4 |
| `bigint` | `BigInt` | v6 |
| TypedArray | `Int8Array` / `Uint8Array` / etc | v1 |
| Callback `(err, val) => void` | `ThreadsafeFunction` | v4 |

### 3.4 Distribuição Multi-Plataforma

O NAPI-RS usa um padrão de `optionalDependencies` para distribuir binários
pré-compilados por plataforma:

```json
// package.json do pacote principal
{
  "name": "@myorg/core",
  "optionalDependencies": {
    "@myorg/core-darwin-x64":       "1.0.0",
    "@myorg/core-darwin-arm64":     "1.0.0",
    "@myorg/core-linux-x64-gnu":    "1.0.0",
    "@myorg/core-linux-x64-musl":   "1.0.0",
    "@myorg/core-linux-arm64-gnu":  "1.0.0",
    "@myorg/core-win32-x64-msvc":   "1.0.0"
  }
}
```

```javascript
// index.js gerado pelo napi-rs/cli
const { platform, arch } = process;
// Tenta carregar o binário correto para a plataforma atual
try {
  module.exports = require(`@myorg/core-${platform}-${arch}`);
} catch {
  // fallback para implementação JS pura (se existir)
}
```

### 3.5 Por que NÃO usamos NAPI-RS neste projeto

| Critério | NAPI-RS | WebSocket IPC (nossa escolha) |
|---|---|---|
| Isolamento de processo | Sem isolamento — crash Rust derruba processo Node | Isolamento total — crash Python não afeta Gateway |
| Escalabilidade independente | Não — TS e Rust rodam no mesmo processo | Sim — Python e TS escalam separadamente |
| Deploy independente | Não | Sim — deploy de novas versões sem parar o outro |
| Depuração | Hard — mistura stacks TS + Rust | Fácil — logs separados por processo |
| Latência | Muito baixa (chamada direta) | ~1ms overhead de socket (aceitável) |
| Tipagem automática | Sim (`.d.ts` gerado) | Manual (mas `envelope.v1.json` serve de schema) |

---

## 4. Bridge 3 — TypeScript ↔ Python via WebSocket + JSON Schema

### 4.1 Mecanismo

Os dois processos (Gateway TS e Brain Python) se comunicam via **WebSocket
bidirecional persistente**, com um **JSON Schema único** como contrato de
mensagens. Não há geração de código — ambos os lados consomem e validam o mesmo
`schemas/envelope.v1.json`.

```
Gateway (TypeScript)              Brain (Python/FastAPI)
packages/gateway/                 rlm/server/ws_gateway_endpoint.py
        │                                  │
        │  ws://brain:8765/ws/gateway      │
        │  ?token=<HMAC-token>             │
        │◄────── WebSocket ───────────────►│
        │                                  │
        │  { "type": "envelope",           │
        │    "data": { ...envelope } }     │
        │                                  │
        │  { "type": "ack",                │
        │    "data": { "id": "..." } }     │
        │◄─────────────────────────────────│
```

### 4.2 Protocolo de Framing

Todas as mensagens seguem o envelope de **dois níveis**:

```json
// Nível 1 — frame de transporte (tipo do frame)
{
  "type": "envelope",
  "data": { ... }
}

// Tipos de frame suportados:
// "envelope"     — mensagem de negócio real
// "ack"          — confirmação de recebimento
// "ping"         — keepalive (TS → Python)
// "pong"         — resposta keepalive (Python → TS)
// "health_report"— telemetria de saúde
```

```json
// Nível 2 — data.envelope (schemas/envelope.v1.json)
{
  "id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "source_channel": "telegram",
  "source_id": "bot:12345678",
  "source_client_id": "telegram:987654321",
  "direction": "inbound",
  "message_type": "text",
  "text": "Olá, agente!",
  "timestamp": "2025-01-01T00:00:00Z"
}
```

### 4.3 Campos do Envelope (`schemas/envelope.v1.json`)

**Campos obrigatórios:**

| Campo | Tipo / Formato | Descrição |
|---|---|---|
| `id` | `string` 32-char hex | UUID sem hífens |
| `source_channel` | enum (abaixo) | Origem do canal |
| `source_id` | `string` | ID do bot/worker na origem |
| `source_client_id` | `string` `^[a-z]+:.+$` | ID do cliente (`telegram:123`) |
| `direction` | `"inbound"` \| `"outbound"` | Direção da mensagem |
| `message_type` | `string` | `"text"`, `"image"`, `"command"`, ... |
| `text` | `string` | Conteúdo textual |
| `timestamp` | `string` ISO 8601 | Momento do evento |

**`source_channel` enum:**
`telegram | discord | slack | whatsapp | webchat | api | internal`

**Campos opcionais:**
`correlation_id`, `reply_to_id`, `target_channel`, `target_id`, `metadata`, `priority`

### 4.4 Implementação TypeScript — `packages/gateway/src/ws-bridge.ts`

```typescript
export class WsBridge extends EventEmitter {
  private status: BridgeStatus = "disconnected";
  private pendingQueue: string[] = [];  // até 512 mensagens em buffer
  private backoff: ExponentialBackoff;  // 500ms → 30s

  constructor(
    private readonly brainWsUrl: string,
    private readonly brainWsToken?: string,  // opcional: Bearer token
  ) {
    super();
    this.backoff = new ExponentialBackoff({ initialMs: 500, maxMs: 30_000 });
  }

  start(): void { /* inicia connect loop */ }
  stop(): Promise<void> { /* shutdown gracioso, drena fila */ }

  sendEnvelope(envelope: Envelope): boolean {
    const frame = JSON.stringify({ type: "envelope", data: envelope });
    if (this.status === "connected") {
      this.ws?.send(frame);
      return true;
    }
    // Não conectado — enfileira para envio quando reconectar
    if (this.pendingQueue.length < 512) {
      this.pendingQueue.push(frame);
    }
    return false;
  }

  onReply(handler: BrainReplyHandler): void { /* registra listener */ }

  healthSnapshot(): BridgeHealthSnapshot {
    return {
      status: this.status,
      pendingCount: this.pendingQueue.length,
      reconnectAttempts: this.backoff.attempts,
    };
  }
}

type BridgeStatus = "disconnected" | "connecting" | "connected" | "draining";
```

### 4.5 Implementação Python — `rlm/server/ws_gateway_endpoint.py`

```python
from fastapi import WebSocket, WebSocketDisconnect
import hmac, asyncio, json

PING_INTERVAL = int(os.getenv("RLM_WS_PING_INTERVAL", "20"))
DISPATCH_TIMEOUT = int(os.getenv("RLM_WS_DISPATCH_TIMEOUT", "120"))

@router.websocket("/ws/gateway")
async def ws_gateway_endpoint(websocket: WebSocket):
    # 1. Autenticação via token (query param ou Bearer header)
    token = websocket.query_params.get("token") \
         or _extract_bearer(websocket.headers.get("authorization", ""))

    expected = _get_expected_token()  # RLM_GATEWAY_TOKEN ou RLM_WS_TOKEN
    if not hmac.compare_digest(token or "", expected):
        await websocket.close(code=4401)  # 4401 = auth failure customizado
        return

    await websocket.accept()

    # 2. Loop principal — ping keepalive + recebimento de frames
    async def _send_ping():
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await websocket.send_json({"type": "ping"})

    ping_task = asyncio.create_task(_send_ping())
    try:
        async for raw in websocket.iter_text():
            frame = json.loads(raw)
            match frame["type"]:
                case "envelope":
                    asyncio.create_task(
                        _dispatch_envelope(frame["data"], websocket)
                    )
                case "pong": pass  # resposta ao nosso ping
                case "ack":  pass  # confirmação de resposta anterior
    except WebSocketDisconnect:
        pass
    finally:
        ping_task.cancel()
```

### 4.6 Modelos de Async: TypeScript vs Python

| Aspecto | TypeScript | Python |
|---|---|---|
| Runtime async | V8 Event Loop (single-thread) | asyncio (single-thread) + uvicorn workers |
| Primitiva | `async/await` + `EventEmitter` + Promises | `async def` + `await` + `asyncio.create_task` |
| Biblioteca WS | `ws` (npm) | `fastapi.WebSocket` via `starlette` |
| Paralelismo | Worker Threads / Cluster | `ProcessPoolExecutor` / uvicorn multi-worker |
| Backpressure | Fila manual de 512 msgs | `asyncio.Queue` |
| Keepalive | Ping interval no WsBridge | Ping a cada 20s no servidor FastAPI |

### 4.7 Segurança da Bridge

```
Autenticação:
  TS side: brainWsToken passado na URL como ?token=...
           ou header Authorization: Bearer <token>

Python side:
  expected = os.getenv("RLM_GATEWAY_TOKEN") or os.getenv("RLM_WS_TOKEN")
  if not hmac.compare_digest(received_token, expected):
      websocket.close(code=4401)
  # hmac.compare_digest previne timing attacks

Transporte:
  Desenvolvimento: ws://  (localhost)
  Produção:        wss:// + TLS (obrigatório)
```

---

## 5. Decisões de Design Fundamentais

### 5.1 PyO3 vs Alternativas para Rust↔Python

| Abordagem | Vantagens | Desvantagens |
|---|---|---|
| **PyO3** ✅ | Type-safe, ergonômico, sem memória manual, `Arc<T>` built-in | Requer recompilação para cada versão CPython |
| `ctypes` | Zero-dep, nativo Python | Sem type-safety, `ctypes.Structure` verboso |
| `cffi` | Melhor que ctypes | Ainda manual, sem ergonomia |
| `subprocess + pickle` | Zero setup | Overhead de serialização + processo |
| `gRPC (tonic)` | Agnóstico à linguagem | Overhead proto + codegen |

**Escolha:** PyO3 — a indústria converge para PyO3 (polars, pydantic-core,
tiktoken já provaram em produção).

### 5.2 WebSocket IPC vs NAPI-RS para TS↔Python

Ver tabela completa na [Seção 3.5](#35-por-que-não-usamos-napi-rs-neste-projeto).

**Escolha:** WebSocket IPC — isolamento de processo é fundamental para um agente
de longa duração onde os dois runtimes têm ciclos de vida independentes.

### 5.3 JSON Schema vs gRPC/Protobuf vs MessagePack

| Abordagem | Vantagens | Desvantagens |
|---|---|---|
| **JSON Schema** ✅ | Human-readable, sem codegen, fácil debug | ~3x maior que binário |
| `gRPC + Protobuf` | Binário eficiente, contrato forte | Codegen obrigatório, harder debug |
| `MessagePack` | Compacto, sem schema | Sem validação de schema nativa |
| `Cap'n Proto` | Zero-copy, muito rápido | Complexidade alta |

**Escolha:** JSON Schema — para um agente conversacional, o overhead de JSON
(~few KB por mensagem) é irrelevante comparado à complexidade de gRPC.

### 5.4 Hot Path: Quando Migrar para Rust

**Regra:** Medir ANTES de migrar. Só migre quando tiver evidência:

```
Processo de decisão:
  1. Profile com cProfile / py-spy (Python) ou chrome:trace (TS)
  2. Identifica hot path (> 5% do CPU total)
  3. Estima speedup esperado (tipicamente 10–100x para código de loop)
  4. Compara custo de manutenção Rust vs ganho de desempenho
  5. Implementa PyO3 se ROI positivo
  6. Valida com benchmark: pytest-benchmark / criterion
```

**Exemplos reais deste projeto:**
- `arkhe-memory` → busca HNSW: Python seria O(n) linear scan, Rust faz HNSW (sublinear)
- `arkhe-wire` → 7.4x mais rápido que `orjson` + normalização Python separados

---

## 6. Padrões de Integração da Indústria

### 6.1 Strangler Fig Pattern (Migração Incremental)

O projeto usa o **Strangler Pattern** para migrar de Python monolítico para
arquitetura TS+Rust+Python:

```
Antes:  [Python monolito (CLI + Gateway + Brain)]

Durante (estado atual):
        [TS Gateway] → WebSocket → [Python Brain + Rust FFI]

Depois (target):
        [TS Gateway] → WebSocket → [Python Brain]
                                         ↓ PyO3
                                   [Rust: memory, wire, vault, policy]
```

O gateway TypeScript "estrangula" gradualmente a camada de canal Python,
sem big-bang rewrite.

### 6.2 Schema-First Contract (Contrato por Schema)

O `schemas/envelope.v1.json` é o **contrato vivo** entre TypeScript e Python.
Cada lado valida independentemente:

```typescript
// TypeScript valida antes de enviar
import Ajv from "ajv";
import schema from "../../../schemas/envelope.v1.json";
const validate = new Ajv().compile(schema);
if (!validate(envelope)) throw new Error(validate.errors);
```

```python
# Python valida ao receber
import jsonschema
with open("schemas/envelope.v1.json") as f:
    schema = json.load(f)
jsonschema.validate(instance=data, schema=schema)
```

### 6.3 Domain Isolation por Linguagem

```
TypeScript  →  I/O, Channels, CLI, TUI, Eventos, Timers
Python      →  Orchestração, LLM, RAG, Skills, Estado de sessão
Rust        →  Computação numérica, Protocol encoding, Criptografia
```

Esta separação não é arbitrária — reflete os pontos fortes de cada linguagem:
- TS: ecosystem assíncrono rico (npm), ergonômico para event-driven
- Python: ecosystem ML/AI imbatível (transformers, langchain, etc.)
- Rust: performance + safety para código de baixo nível crítico

### 6.4 Graceful Degradation

Nunca deixe o módulo Rust ser um **single point of failure**:

```python
# Padrão: try/except com fallback funcional
try:
    from arkhe_wire import wire_json_dumps
    _encode = wire_json_dumps
except ImportError:
    import json
    _encode = lambda obj: json.dumps(obj).encode()
    logger.warning("arkhe_wire not available, using stdlib json (slower)")
```

### 6.5 Versionamento de API Entre Processos

Quando TS e Python são deployados independentemente, versionamento é essencial:

```json
// schemas/envelope.v1.json — v1 no nome do arquivo
// Mudanças breaking → schemas/envelope.v2.json
// Mudanças aditivas → campos opcionais no v1 existente

// Header da bridge para negociação de versão (padrão)
{
  "type": "hello",
  "data": {
    "schema_version": "1",
    "client": "gateway-ts",
    "version": "0.3.1"
  }
}
```

---

## 7. Build & Deploy das Pontes

### 7.1 Rust (Cargo Workspace)

```toml
# native/Cargo.toml — workspace que agrega todos os crates nativos
[workspace]
members = [
    "arkhe-memory",
    "arkhe-wire",
    "arkhe-vault",          # roadmap
    "arkhe-policy-core",    # roadmap
]
resolver = "2"

[profile.release]
opt-level = 3
lto = "fat"            # Link-Time Optimization — reduz tamanho e aumenta perf
codegen-units = 1      # melhor otimização (mais lento para compilar)
```

### 7.2 Maturin — Build de Wheels Python

```bash
# Build de todos os módulos nativos
cd native/arkhe-memory && maturin build --release
cd native/arkhe-wire   && maturin build --release

# Instalar wheels no ambiente virtual
pip install native/arkhe-memory/target/wheels/*.whl --force-reinstall
pip install native/arkhe-wire/target/wheels/*.whl   --force-reinstall

# Verificar instalação
python -c "import arkhe_memory; print('OK')"
python -c "import arkhe_wire; print('OK')"

# Dev-mode (sem gerar wheel, instala direto no .venv)
cd native/arkhe-memory && maturin develop --release
```

### 7.3 pnpm Workspace — Gateway TypeScript

```json
// pnpm-workspace.yaml (raiz do projeto)
// packages/gateway/package.json
{
  "name": "@rlm/gateway",
  "scripts": {
    "build": "tsc --project tsconfig.build.json",
    "typecheck": "tsc --noEmit",
    "dev": "tsx watch src/index.ts"
  },
  "dependencies": {
    "ws": "^8.18.0",
    "hono": "^4"
  }
}
```

### 7.4 Verificação de Saúde end-to-end

```bash
# 1. TypeScript compila limpo (zero erros)
cd packages/gateway && npx tsc --noEmit

# 2. Python importa os módulos Rust sem erro
python -c "from arkhe_memory import ArkheVectorIndex; from arkhe_wire import wire_json_dumps; print('Rust modules OK')"

# 3. Bridge WebSocket conecta
python -m rlm.server &   # inicia o Brain
npx tsx packages/gateway/src/index.ts  # inicia Gateway
# Observar nos logs: "WsBridge connected to brain"
```

---

## 8. Roadmap: Crates Rust Pendentes

| Crate | Prioridade | Propósito | Status |
|---|---|---|---|
| `arkhe-memory` | ✅ Completo | HNSW ANN vector index | Produção |
| `arkhe-wire` | ✅ Completo | Wire protocol encoding (7.4x speedup) | Produção |
| `arkhe-policy-core` | Priority 3 | Regras de política, contratos de acesso | Planejado |
| `arkhe-mcts` | Priority 4 | Monte Carlo Tree Search para planejamento | Planejado |
| `arkhe-vault` | Priority 5 | Criptografia, armazenamento seguro de segredos | Em progresso |
| `arkhe-audit` | Priority 6 | Logs de auditoria imutáveis | Planejado |

Para cada novo crate:
1. Criar em `native/<nome-crate>/`
2. Adicionar ao `[workspace]` em `native/Cargo.toml`
3. Criar `src/pybridge.rs` com PyO3 bindings
4. Criar `src/lib.rs` com re-exports públicos
5. `maturin build` + instalar wheel
6. Adicionar `try/except ImportError` fallback no código Python

---

## 9. Comparativo: Alternativas Rejeitadas

### 9.1 Por que não gRPC entre TS e Python?

- gRPC exige codegen de `.proto` → sincronizar arquivos `.proto` entre repos
- Overhead de setup (protoc, grpc-tools, grpc, python grpc) vs `ws` (npm i ws)
- Mensagens de texto (chat) são legíveis em JSON — não há ganho prático de binário
- Dificuldade de debug: ter que usar `grpc_cli` vs simplesmente ler os logs JSON

### 9.2 Por que não REST HTTP entre TS e Python?

- REST é request-response — não suporta nativamente **server-push** (Python → TS)
- Para streaming de respostas LLM, precisaríamos de SSE ou long-polling
- WebSocket dá full-duplex com menos overhead de conexão para mensagens frequentes

### 9.3 Por que não usar linguagem única?

| Opção | Problema |
|---|---|
| Só Python | Sem performance nativa para HNSW; CLI/TUI em Python são mais lentos |
| Só TypeScript | Ecosystem ML/LLM é Python-first; frameworks como `transformers` não existem em TS |
| Só Rust | Desenvolvimento extremamente lento; ecosystem ML/AI quase inexistente; sem integração trivial com LLM APIs |

**A combinação** Python+TypeScript+Rust captura:
- Python: ecosystem AI/ML imbatível
- TypeScript: ecosystem frontend/CLI/TUI + type-safety
- Rust: performance + safety para módulos críticos

---

## 10. Referências

### Documentação Oficial

- **PyO3**: https://pyo3.rs/v0.22.0/
- **Maturin**: https://www.maturin.rs/
- **NAPI-RS**: https://napi.rs/
- **NAPI-RS GitHub**: https://github.com/napi-rs/napi-rs
- **FastAPI WebSockets**: https://fastapi.tiangolo.com/advanced/websockets/
- **ws (npm)**: https://github.com/websockets/ws

### Projetos de Referência que Usam PyO3

- **orjson**: https://github.com/ijl/orjson
- **polars**: https://github.com/pola-rs/polars
- **pydantic-core**: https://github.com/pydantic/pydantic-core
- **tiktoken (OpenAI)**: https://github.com/openai/tiktoken
- **tokenizers (HuggingFace)**: https://github.com/huggingface/tokenizers

### Arquivos Chave neste Projeto

| Arquivo | Propósito |
|---|---|
| [native/arkhe-memory/src/pybridge.rs](../native/arkhe-memory/src/pybridge.rs) | PyO3 bridge HNSW → Python |
| [native/arkhe-wire/src/pybridge.rs](../native/arkhe-wire/src/pybridge.rs) | PyO3 bridge protocolo |
| [packages/gateway/src/ws-bridge.ts](../packages/gateway/src/ws-bridge.ts) | WebSocket client TS→Python |
| [rlm/server/ws_gateway_endpoint.py](../rlm/server/ws_gateway_endpoint.py) | WebSocket server Python |
| [schemas/envelope.v1.json](../schemas/envelope.v1.json) | Contrato JSON Schema |
| [plano-migração-python-rust-typescript.md](../../plano-migração-python-rust-typescript.md) | Plano de migração detalhado |

---

*Documento gerado em: 2025 | Versão: 1.0 | Escopo: RLM OpenClaw Engine*
