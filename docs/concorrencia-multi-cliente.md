# Concorrência e Capacidade Multi-Cliente — RLM-Main

> **Data:** Março 2026  
> **Status:** Análise técnica do estado atual + limitações + roadmap  
> **Motivação:** Entender se o RLM suporta escritório + ESP32 + celular + carro simultâneos

---

## Índice

1. [Resposta direta](#1-resposta-direta)
2. [Arquitetura de execução](#2-arquitetura-de-execução)
3. [Fluxo completo de dois pedidos simultâneos](#3-fluxo-completo-de-dois-pedidos-simultâneos)
4. [Garantias que o código oferece hoje](#4-garantias-que-o-código-oferece-hoje)
5. [Limite 1 — Pool de threads](#5-limite-1--pool-de-threads)
6. [Limite 2 — Lock síncrono no event loop](#6-limite-2--lock-síncrono-no-event-loop)
7. [Capacidades ausentes relevantes](#7-capacidades-ausentes-relevantes)
8. [Diagnóstico por cenário de uso](#8-diagnóstico-por-cenário-de-uso)
9. [Melhorias priorizadas](#9-melhorias-priorizadas)
10. [Rastreabilidade ao código](#10-rastreabilidade-ao-código)

---

## 1. Resposta Direta

**Sim.** Para o cenário de 4 dispositivos simultâneos (escritório, ESP32, celular, carro), o RLM suporta concorrência real sem ajustes.

O limite atual de threads paralelas (~8–12 num servidor típico) é confortável para
esse caso de uso. Os gargalos existentes só se manifestariam com dezenas de clientes
simultâneos fazendo execuções longas ao mesmo tempo.

**O que funciona hoje sem mudança:**
- Dois pedidos de `client_id` diferentes executam em threads paralelas reais
- Sessões são completamente isoladas entre si
- O event loop do servidor não bloqueia durante processamento do agente

**O que não funciona ainda:**
- Memória compartilhada entre dispositivos do mesmo usuário
- Streaming de resposta parcial (cliente espera a resposta completa)
- Prioridade entre pedidos (urgente vs. longo)

---

## 2. Arquitetura de Execução

### Stack do servidor

```
Internet / dispositivos
         │
         ▼
   Uvicorn (1 worker, 1 processo)
         │
         ▼
   FastAPI + asyncio event loop         ← coroutines leves, não bloqueiam
         │
         ├─ Handler A (await run_in_executor) ──→ Thread-1: RLM_A.completion()
         ├─ Handler B (await run_in_executor) ──→ Thread-2: RLM_B.completion()
         └─ Handler C (await run_in_executor) ──→ Thread-3: RLM_C.completion()
                                                   (paralelos reais, sem interferência)
```

O ponto central é o `run_in_executor`. O FastAPI é async, mas o motor `RLM.completion()`
é síncrono (função `def`, não `async def`). O `run_in_executor` resolve isso: envia
o processamento pesado para uma thread separada, liberando o event loop imediatamente
para receber novos pedidos.

### Código relevante

**Despacho em `api.py` (linha ~408):**
```python
result: ExecutionResult = await loop.run_in_executor(
    None,                                       # pool padrão do asyncio
    lambda: supervisor.execute(session, prompt),
)
```

**Despacho em `webhook_dispatch.py` (linha ~270):**
```python
result = await loop.run_in_executor(
    None,
    lambda: supervisor.execute(session, prompt),
)
```

`run_in_executor(None, ...)` usa o `ThreadPoolExecutor` padrão do asyncio.
Em Python 3.10, o tamanho padrão é `min(32, os.cpu_count() + 4)`.
Num servidor com 4 cores: **8 threads disponíveis**.

### Motor RLM — completamente síncrono

**`rlm/core/rlm.py` — assinatura da função principal:**
```python
def completion(
    self,
    prompt: str | dict[str, Any],
    root_prompt: str | None = None,
    mcts_branches: int = 0,
) -> RLMChatCompletion:
    ...
    for i in range(self.max_iterations):       # loop síncrono
        if self._abort_event.is_set():
            break
        iteration = self._completion_turn(...)  # chamada síncrona
```

**Não há:**
- `asyncio.run()` dentro de `completion()` (que bloquearia o event loop)
- `time.sleep()` dentro do loop principal
- Estado global mutável compartilhado entre instâncias `RLM`

### Isolamento entre sessões

```python
# session.py linha ~130
def get_or_create(self, client_id: str, ...) -> RLMSession:
    with self._lock:
        # Busca sessão DESTE client_id — outra sessão nunca é retornada
        for session in self._active_sessions.values():
            if session.client_id == client_id and session.status in ("idle", "running"):
                return session
        # Se não existe: cria novo RLMSession com novo rlm_instance
        new_session = RLMSession(
            session_id=str(uuid4()),
            client_id=client_id,
            rlm_instance=RLM(**self._default_rlm_kwargs),  # instância independente
            state_dir=...,
        )
```

`client_id="escritorio"` → `RLM_A` (instância própria, state_dir próprio)  
`client_id="celular"` → `RLM_B` (instância própria, state_dir próprio)  
As duas instâncias não compartilham nenhum atributo mutável.

### Guard contra dupla execução da mesma sessão

**`rlm/core/supervisor.py` (linha ~98):**
```python
def execute(self, session: RLMSession, prompt: str) -> ExecutionResult:
    if self.is_running(session.session_id):
        return ExecutionResult(
            session_id=session.session_id,
            status="error",
            error_detail="Session is already running a completion.",
        )
```

Se o escritório mandar dois pedidos ao mesmo tempo (mesmo `client_id`), o segundo
retorna erro imediatamente. Não há risco de corrida no estado da sessão.

---

## 3. Fluxo Completo de Dois Pedidos Simultâneos

**Cenário:** escritório analisa relatório longo + celular consulta clima e agenda

```
t=0ms    POST /webhook/escritorio → body: "analise este relatório de 40 páginas"
t=1ms    POST /webhook/celular    → body: "clima amanhã e agende consulta 15h"

── asyncio event loop (thread principal) ─────────────────────────────────────

t=2ms    Handler A: sm.get_or_create("escritorio")
         → adquire threading.Lock
         → SELECT no SQLite: nenhuma sessão ativa
         → cria RLMSession_A + RLM_A (nova instância)
         → libera lock

t=3ms    Handler B: sm.get_or_create("celular")
         → adquire threading.Lock [Handler A já liberou]
         → SELECT no SQLite: nenhuma sessão ativa
         → cria RLMSession_B + RLM_B (nova instância)
         → libera lock

t=4ms    Handler A: supervisor.is_running("session_A") → False
t=4ms    Handler B: supervisor.is_running("session_B") → False

t=5ms    Handler A: await loop.run_in_executor(None, supervisor.execute(session_A, prompt_A))
         → Thread-1 inicia: RLM_A.completion("analise relatório...")
         → event loop LIVRE (handler A aguarda sem bloquear)

t=5ms    Handler B: await loop.run_in_executor(None, supervisor.execute(session_B, prompt_B))
         → Thread-2 inicia: RLM_B.completion("clima amanhã e agende...")
         → event loop LIVRE (handler B aguarda sem bloquear)

── threads paralelas (independentes) ─────────────────────────────────────────

t=5ms - t=12s:
    Thread-1: RLM_A acessa LLM, 30 iterações, tools de análise...  ← trabalhando
    Thread-2: RLM_B acessa LLM, busca clima, acessa calendar...    ← trabalhando em paralelo

t=12s    Thread-2 termina →
         Handler B acorda (await resolvido) →
         Resposta JSON enviada ao celular: "Amanhã 22°C, consulta agendada 15h" ✓

t=87s    Thread-1 termina →
         Handler A acorda (await resolvido) →
         Resposta JSON enviada ao escritório: "Análise concluída: ..." ✓

── event loop disponível durante todo o processo para novos pedidos ──────────
```

**Resultado:** o celular recebe resposta em ~12s. O escritório em ~87s.
Nenhum interfere no outro. O servidor estava disponível durante todo o processo
para receber pedidos de ESP32, carro, ou outros clientes.

---

## 4. Garantias que o Código Oferece Hoje

| Situação | Comportamento | Código |
|---|---|---|
| 2 `client_id` diferentes simultâneos | ✅ Executam em threads paralelas | `run_in_executor` em `api.py:408` |
| Mesmo `client_id`, 2 pedidos ao mesmo tempo | ✅ Segundo rejeitado com erro claro | `supervisor.py:98` |
| Sessão A acessa estado da sessão B? | ✅ Impossível — instâncias isoladas | `session.py:134` |
| SQLite thread-safe? | ✅ `check_same_thread=False` | `session.py:116` |
| Event loop bloqueia durante execução do RLM? | ✅ Não — `run_in_executor` isola | `api.py:408` |
| `asyncio.run()` dentro de handler async? | ✅ Não encontrado | — |
| `time.sleep()` dentro de handlers async? | ✅ Não encontrado | — |

---

## 5. Limite 1 — Pool de Threads

### O problema

Cada execução longa ocupa **1 thread** pelo tempo completo de execução (até 120s por
sessão, configurável em `RLM_TIMEOUT`). O pool padrão do asyncio tem tamanho finito.

```
Pool padrão asyncio (Python 3.10, servidor 4 cores):
Tamanho = min(32, os.cpu_count() + 4) = min(32, 8) = 8 threads

Cenário de saturação:
  Thread-1: escritório  — análise longa   (90s)
  Thread-2: celular     — consulta rápida (12s) ✓ libera
  Thread-3: carro       — navegação       (8s)  ✓ libera
  Thread-4: esp32-sala  — leitura sensor  (2s)  ✓ libera
  Thread-5: esp32-jardim— irrigação       (5s)  ✓ libera
  Thread-6: livre
  Thread-7: livre
  Thread-8: livre

→ Para 4 dispositivos típicos: sempre há threads livres. Sem starvation.
```

### Quando se torna problema

Com **8+ execuções longas simultâneas** (8+ usuários fazendo análises de 90s),
o 9º usuário fica enfileirado até uma thread liberar. Não é o caso de uso do
sistema descrito (pessoal/doméstico).

### Solução se necessário

```python
# api.py — configurar executor dedicado com tamanho maior
from concurrent.futures import ThreadPoolExecutor

EXECUTOR = ThreadPoolExecutor(max_workers=20, thread_name_prefix="rlm-agent")

# No handler:
result = await loop.run_in_executor(EXECUTOR, lambda: supervisor.execute(...))
```

Ou escalar horizontalmente: `uvicorn --workers 4` (4 processos independentes = 4×
throughput, requer SQLite WAL mode ou migração para PostgreSQL).

---

## 6. Limite 2 — Lock Síncrono no Event Loop

### O problema

```python
# session.py linha 81
self._lock = threading.Lock()   # lock síncrono, NÃO asyncio.Lock

def get_or_create(self, client_id: str, ...) -> RLMSession:
    with self._lock:             # ← adquirido no event loop thread
        ...
        with self._get_conn() as conn:
            row = conn.execute("SELECT ...").fetchone()  # I/O SQLite aqui
        ...
```

`get_or_create()` é chamado diretamente no handler async (antes do `run_in_executor`),
portanto roda na thread principal do event loop. O `threading.Lock()` bloqueante impede
que **qualquer outra coroutine** execute durante o lock.

### Impacto prático

| Cenário | Stall no event loop | Perceptível? |
|---|---|---|
| 2 clientes chegando ao mesmo instante | ~1–5ms | Não (humano) |
| 10 clientes chegando ao mesmo instante | ~5–50ms | Talvez |
| 50 clientes chegando ao mesmo instante | ~50–250ms | Sim |

Para uso pessoal (4 dispositivos): **não perceptível**.

### Correção

Mover o lookup de sessão para dentro do `run_in_executor`, ou usar
`asyncio.Lock` + `loop.run_in_executor` apenas para o SQLite:

```python
# Versão corrigida — não bloqueia o event loop
async def get_or_create_async(self, client_id: str, ...) -> RLMSession:
    async with self._async_lock:                    # asyncio.Lock
        # Verifica memória (sem I/O — ok no event loop)
        for session in self._active_sessions.values():
            if session.client_id == client_id and session.status in ("idle", "running"):
                return session
        # SQLite I/O → fora do event loop
        loop = asyncio.get_event_loop()
        row = await loop.run_in_executor(None, self._query_db, client_id)
        ...
```

---

## 7. Capacidades Ausentes Relevantes

### 7.1 Memória compartilhada entre dispositivos

**Estado atual:** cada `client_id` tem `state_dir` próprio em `./rlm_states/<session_id>/`.
Sessões são hermeticamente isoladas.

**Cenário problemático:**
```
Escritório: "analise o relatório Q1 e sugira ações"
             → RLM_A processa, cria análise interna

Celular (1h depois): "me resume o que o agente analisou hoje"
             → RLM_B: não sabe nada — nova sessão vazia
```

**O módulo existe mas precisa ser ativado:**

`rlm/tools/memory.py` e `rlm/core/memory_manager.py` existem no projeto. A memória
persistente transversal a sessões é a solução, mas requer configuração explícita
de qual `user_id` agrupa múltiplos `client_id`.

**Arquitetura necessária:**
```
client_id: "escritorio"   ┐
client_id: "celular"      ├── user_id: "demet" ── memory store compartilhada
client_id: "esp32-sala"   ┘
client_id: "carro"        
```

### 7.2 Streaming de resposta

**Estado atual:** o cliente HTTP espera a resposta completa antes de receber qualquer
dado. Para uma análise de 90 segundos, o cliente fica 90s sem feedback.

```
Cliente                     Servidor
  │──── POST /webhook ───────→│
  │                           │  (90 segundos de silêncio)
  │←── HTTP 200 + JSON ───────│  
```

**Com streaming (Server-Sent Events ou WebSocket):**
```
Cliente                     Servidor
  │──── WS connect ──────────→│
  │←── "Analisando seção 1..." │  (t=8s)
  │←── "Identificando padrões" │  (t=23s)
  │←── "Conclusão: ..."        │  (t=87s)
```

O `rlm/server/webchat.py` já implementa SSE para o WebChat. A extensão para outros
canais é possível mas requer mudança na interface de resposta do `RLM.completion()`.

### 7.3 Prioridade entre pedidos

**Estado atual:** FIFO por threads disponíveis. ESP32 com alerta urgente e escritório
com análise longa competem pelo mesmo pool de threads.

**Para o caso de uso descrito (4 dispositivos pessoais):** irrelevante.
**Para produção com múltiplos usuários:** prioridade por perfil seria necessária.

### 7.4 Cancelamento via API

**Estado atual:** `_abort_event` existe no `RLM` e é verificado a cada iteração,
mas não está exposto via endpoint HTTP.

```python
# rlm/core/rlm.py — verificação de abort dentro do loop
for i in range(self.max_iterations):
    if getattr(self, '_abort_event', None) and self._abort_event.is_set():
        break
```

Não há `DELETE /sessions/{id}/execution` ou similar. Para cancelar, é necessário
`DELETE /sessions/{id}` que aborta e descarta a sessão.

---

## 8. Diagnóstico por Cenário de Uso

### Cenário A — Escritório analisa relatório + Celular consulta clima

```
Resultado: FUNCIONA ✅
Paralelo: sim (threads separadas)
Interferência: nenhuma
Tempo de resposta:
  - Celular: ~10-15s (consulta simples)
  - Escritório: ~60-120s (análise longa)
Limitação: escritório fica sem feedback durante a análise (sem streaming)
```

### Cenário B — ESP32 envia leitura de sensor a cada 30s

```
Resultado: FUNCIONA ✅
Padrão: pedido → resposta em <5s → ESP32 aguarda 30s → próximo pedido
Sem concorrência real necessária (sequential por natureza)
Limitação: sem perfil "iot" hoje → resposta pode vir formatada para humano
           (texto longo onde ESP32 esperaria JSON simples)
```

### Cenário C — Carro pede rota + Celular pede agenda ao mesmo tempo

```
Resultado: FUNCIONA ✅
Paralelo: sim
Interferência: nenhuma
Limitação: "carro" e "celular" têm mesmo client_id? → PROBLEMA
  Se ambos chegam como client_id="mobile", será a MESMA sessão.
  O segundo pedido recebe: "Session is already running a completion."
  Solução: client_id deve ser único por dispositivo, não por tipo.
  "carro", "celular-demet" → correto
  "mobile", "mobile"       → incorreto
```

### Cenário D — Escritório pede dois itens distintos em rápida sequência

```
Usuário: "analise este relatório" (enviado)
Usuário: "espera, adiciona esta tabela também" (enviado 2s depois)
```

```
Resultado: PARCIALMENTE PROBLEMÁTICO ⚠️
Se o primeiro pedido ainda está em execução:
  → Segundo pedido: HTTP 200 com body {"status": "error", "error_detail": "Session is already running a completion."}
  → Usuário precisa re-enviar depois que o primeiro terminar
Sem fila de pedidos por sessão
```

### Cenário E — 4 dispositivos com execuções longas simultâneas

```
Escritório: análise 90s    → Thread-1
Celular:    pesquisa 20s   → Thread-2
ESP32:      leitura 3s     → Thread-3 (libera rapidamente)
Carro:      rota 15s       → Thread-4

Pool disponível: 8 threads
Threads usadas: 4
Threads livres: 4

Resultado: FUNCIONA ✅ com margem confortável
```

---

## 9. Melhorias Priorizadas

### Prioridade Alta — impacta usabilidade direta

**A. `client_id` baseado em dispositivo (não em canal)**

Hoje os canais atribuem `client_id` derivado do identificador do canal
(ex: `telegram:123456`). Com multi-dispositivo, o mesmo usuário vai ter
`telegram:123456` no celular e uma conexão WebSocket no escritório — são
`client_id` diferentes, impedindo memória compartilhada.

Solução: adicionar camada `user_id` que agrupa múltiplos `client_id`:
```sql
-- Adicionar à tabela clients (documentado em arquitetura-config-multidevice.md)
ALTER TABLE clients ADD COLUMN user_id TEXT;
```

**B. Ativar `memory_manager.py` com escopo por `user_id`**

Permitir que o escritório e o celular compartilhem memória de longo prazo
(notas, preferências, contexto de projetos) mas mantenham contexto de
conversa separado.

### Prioridade Média — melhora experiência

**C. Streaming SSE para canais HTTP**

O `webchat.py` já tem a implementação. Estender para o webhook principal via
endpoint `GET /sessions/{id}/stream` que emite eventos SSE durante a execução.

**D. Configurar `RLM_EXECUTOR_THREADS` via env**

```python
# api.py — ao invés do pool padrão
_executor = ThreadPoolExecutor(
    max_workers=int(os.getenv("RLM_EXECUTOR_THREADS", "16")),
    thread_name_prefix="rlm-agent",
)
```

**E. Perfil por `client_id` (documentado em `arquitetura-config-multidevice.md`)**

ESP32 recebe resposta em JSON estruturado. Carro recebe respostas de 1-2 frases.
Ativado via tabela `clients` + `rlm.toml` com `[[profiles]]`.

### Prioridade Baixa — escala > necessidade atual

**F. Corrigir `threading.Lock()` no event loop**

Converter `get_or_create` para versão async com `asyncio.Lock`. Relevante
apenas com 50+ clientes simultâneos.

**G. SQLite WAL mode**

```python
with self._get_conn() as conn:
    conn.execute("PRAGMA journal_mode=WAL")  # habilitar uma vez
```

Elimina serialização de writes concorrentes no SQLite. Relevante com 10+
escritas simultâneas por segundo.

---

## 10. Rastreabilidade ao Código

| Componente | Arquivo | Linhas relevantes |
|---|---|---|
| Despacho para thread | `rlm/server/api.py` | ~408–415 |
| Despacho webhook | `rlm/server/webhook_dispatch.py` | ~270–278 |
| Motor síncrono | `rlm/core/rlm.py` | ~290 (assinatura), ~383 (loop) |
| SessionManager `_lock` | `rlm/core/session.py` | 81–83 |
| `get_or_create` | `rlm/core/session.py` | ~130–175 |
| `check_same_thread=False` | `rlm/core/session.py` | ~116 |
| Guard dupla execução | `rlm/core/supervisor.py` | ~98–107 |
| Supervisor executor | `rlm/core/supervisor.py` | ~69 |
| EventRouter (stateless) | `rlm/server/event_router.py` | inteiro |
| SSE implementado | `rlm/server/webchat.py` | inteiro |
| Memory tools | `rlm/tools/memory.py` | inteiro |
| Memory manager | `rlm/core/memory_manager.py` | inteiro |

---

## Resumo Executivo

| Pergunta | Resposta |
|---|---|
| O RLM executa escritório e celular em paralelo? | **Sim** — threads independentes |
| Uma sessão pode interferir em outra? | **Não** — isolamento completo por instância |
| O servidor bloqueia durante uma execução longa? | **Não** — `run_in_executor` isola |
| Quantas execuções longas simultâneas suporta? | **~8** (pool padrão, 4 cores) |
| Suficiente para 4 dispositivos pessoais? | **Sim** com margem |
| Celular e escritório compartilham memória hoje? | **Não** — requer ativação de `memory_manager` |
| ESP32 recebe resposta formatada corretamente? | **Não ainda** — requer perfil `iot` |
| Streaming de resposta parcial disponível? | **Apenas no WebChat** — não nos outros canais |
