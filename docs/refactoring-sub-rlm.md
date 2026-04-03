# Refatoração de `sub_rlm.py` — Documentação Completa

## 1. Visão Geral

O arquivo `rlm/core/engine/sub_rlm.py` era um monolito de **2080 linhas** que acumulou
três responsabilidades distintas: definição de tipos públicos (exceptions, dataclasses,
handles), helpers privados de interação com o pai e heurísticas, e as factories de execução
(serial, async, paralela). A refatoração separou essas responsabilidades em **3 módulos
coesos**, corrigiu **5 bugs** (1 CRITICAL), eliminou **~440 linhas de duplicação**, e
manteve zero regressions — 1708 testes passaram antes e depois.

### Métricas

| Arquivo | Linhas | Responsabilidade |
|---|---:|---|
| `sub_rlm.py` (refatorado) | 1302 | Factories: serial, async, parallel + re-exports |
| `_sub_rlm_types.py` | 259 | Exceptions, dataclasses, AsyncHandle, type aliases |
| `_sub_rlm_helpers.py` | 388 | 18 helpers privados: parent interaction, heuristics, guidance |
| **Total** | **1949** | — |
| **Original** | **2080** | Monolito |
| **Redução líquida** | **-131 (~6%)** | Deduplicação supera overhead de imports/headers |

### Git Stats

```
commit e1e4670
+1252 / -1370  (net reorganizado em 3 módulos)
```

---

## 2. Por Quê — Motivações da Refatoração

### 2.1. Monolito com 3 responsabilidades misturadas

O arquivo original misturava:
1. **Tipos públicos** (exceptions, dataclasses, `AsyncHandle`) consumidos por importers externos
2. **Helpers privados** (interação com parent env, resolução de estratégia, heurísticas MCTS)
3. **Lógica de negócio** (factories serial/async/parallel, spawn de filhos, coordenação)

Editar uma exception forçava revisar 2080 linhas. Helpers de heurística de stop-condition
estavam intercalados com código de spawn de threads. Separar por responsabilidade reduz a
área de superfície cognitiva de cada mudança futura.

### 2.2. Bug CRITICAL escondido pela complexidade

O IndexError no dimensionamento de arrays (`_sys_prompts`, `_models`, `_interaction_modes`)
só ocorria quando `len(tasks) > max_workers`. Com 2080 linhas de contexto, a causa-raiz
era invisível. Na extração, o bug saltou à vista em 3 segundos.

### 2.3. ~440 linhas de duplicação entre parallel e parallel_detailed

`sub_rlm_parallel()` e `sub_rlm_parallel_detailed()` duplicavam:
- Toda a infraestrutura de coordenação (ThreadPoolExecutor, cancel events, futures)
- Propagação de memória e canal para filhos
- Heurísticas de stop condition
- Resumo de execução para o parent

Essa duplicação significava que **todo bugfix precisava ser aplicado em dois lugares**.
A extração de `_run_parallel_core()` unificou isso.

### 2.4. Duplicação serial ↔ async no spawn de filhos

`make_sub_rlm_fn()` e `make_sub_rlm_async_fn()` repetiam a mesma lógica de:
- Construção de `env_kwargs` (memória, canal, bus, cancel event)
- Criação da instância RLM filha com todas as propagações

Extraídos como `_prepare_child_env_kwargs()` e `_spawn_child_rlm()`.

### 2.5. Importação seletiva

Importers como `rlm_context_mixin.py` precisavam de `AsyncHandle` e
`SubRLMParallelTaskResult`, mas carregavam 2080 linhas para acessar 2 nomes.
Com a separação, `_sub_rlm_types.py` (259 linhas) pode ser importado independentemente.
(Na prática, re-exports em `sub_rlm.py` mantêm backward compat — a otimização é futura.)

---

## 3. Arquitetura Após Refatoração

```
sub_rlm.py (1302 linhas)
│
│  ┌─── re-exports públicos ──────────────────────────────────────────┐
│  │  SubRLMError, SubRLMDepthError, SubRLMTimeoutError,             │
│  │  SubRLMResult, SubRLMArtifactResult, AsyncHandle,               │
│  │  SubRLMParallelTaskResult, SubRLMParallelDetailedResults,       │
│  │  SubRLMCallable, SubRLMParallelCallable                        │
│  └─────────────────────────────────────────────────────────────────┘
│
├── _sub_rlm_types.py (259 linhas)
│   ├── Exceptions: SubRLMError, SubRLMDepthError, SubRLMTimeoutError
│   ├── Dataclasses: SubRLMResult, SubRLMArtifactResult 
│   │                (com callables(), values(), as_custom_tools())
│   ├── AsyncHandle (thread handle com bus, cancel, log_poll, result)
│   ├── SubRLMParallelTaskResult, SubRLMParallelDetailedResults
│   └── Type aliases: SubRLMCallable, SubRLMParallelCallable
│
├── _sub_rlm_helpers.py (388 linhas)
│   ├── Parent interaction (7):
│   │   _get_parent_env, _record_parent_runtime_event,
│   │   _attach_parent_bus, _register_parent_subagent_task,
│   │   _update_parent_subagent_task, _ensure_parallel_batch_root,
│   │   _set_parent_parallel_summary
│   ├── Strategy (2):
│   │   _get_parent_active_recursive_strategy, _resolve_parallel_strategy
│   ├── Utilities (3):
│   │   _string_preview, _extract_answer_text, _merge_context_fragments
│   ├── MCTS archive (2):
│   │   _get_parent_archive_snapshot, _format_archive_guidance
│   ├── Heuristics (3):
│   │   _compute_parallel_heuristics, _infer_stop_condition_mode,
│   │   _evaluate_stop_condition
│   └── Guidance (1):
│       _build_recursive_guidance_context
│
└── sub_rlm.py — Factories de execução
    ├── _prepare_child_env_kwargs()    ← compartilhado serial/async/parallel
    ├── _spawn_child_rlm()            ← compartilhado serial/async/parallel
    ├── _propagate_cancel_token()     ← bridge CancellationToken ↔ Event
    │
    ├── make_sub_rlm_fn(parent)       → sub_rlm() closure [serial]
    ├── make_sub_rlm_async_fn(parent) → sub_rlm_async() closure [async/thread]
    └── make_sub_rlm_parallel_fn(parent)
        ├── _run_parallel_core()      ← coordenação unificada
        ├── sub_rlm_parallel()        ← interface simples (list[str])
        └── sub_rlm_parallel_detailed() ← interface completa (DetailedResults)
```

### Dependência entre módulos

```
_sub_rlm_types.py          ← sem dependências internas (folha)
        ↑
_sub_rlm_helpers.py        ← importa SubRLMArtifactResult de _types
        ↑
sub_rlm.py                 ← importa ambos; re-exporta _types
```

---

## 4. O Que Cada Módulo Faz

### `_sub_rlm_types.py` — Tipos Públicos (259 linhas)

Módulo-folha sem dependências internas. Define todos os tipos que consumidores
externos (e.g. `rlm_context_mixin.py`, testes) importam.

**Exceptions:**
- `SubRLMError(RuntimeError)` — erro genérico de sub_rlm
- `SubRLMDepthError(SubRLMError)` — depth limit atingido
- `SubRLMTimeoutError(SubRLMError)` — timeout de execução

**Dataclasses:**
- `SubRLMResult` — resultado simples (task, answer, depth, elapsed_s, timed_out, error)
- `SubRLMArtifactResult` — resultado com artefatos computacionais do REPL filho
  - `callables()` → só funções/classes
  - `values()` → só dados
  - `as_custom_tools()` → formato pronto para `custom_tools=` no RLM pai

**Classes:**
- `AsyncHandle` — handle de filho em daemon thread com:
  - `is_done`, `elapsed_s` (propriedades)
  - `result(timeout_s)` — bloqueia e retorna
  - `log_poll()` — drena mensagens de progresso
  - `cancel()` — sinaliza cancelamento via Event + CancellationToken
  - `bus`, `branch_id` — coordenação P2P via SiblingBus

**Resultado paralelo:**
- `SubRLMParallelTaskResult` — resultado por branch (branch_id, task, answer, error, elapsed_s)
- `SubRLMParallelDetailedResults` — resultado completo do batch (results, winner, heuristics, strategy)

**Type aliases:**
- `SubRLMCallable` — signature da closure sub_rlm()
- `SubRLMParallelCallable` — signature da closure sub_rlm_parallel()

### `_sub_rlm_helpers.py` — 18 Helpers Privados (388 linhas)

Todos prefixados com `_` — consumidos exclusivamente por `sub_rlm.py`.
Organizados em 6 categorias:

| Categoria | Funções | Responsabilidade |
|---|---|---|
| Parent interaction | 7 | Registro de eventos, tarefas, bus, summary no env do pai |
| Strategy | 2 | Resolve estratégia recursiva ativa e política de coordenação |
| Utilities | 3 | Preview de strings, extração de answer, merge de contexto |
| MCTS archive | 2 | Snapshot de arquivo MCTS, formatação de guidance |
| Heuristics | 3 | Métricas paralelas, inferência de stop mode, avaliação |
| Guidance | 1 | Construção completa do contexto recursivo com guidance |

Cada helper usa `getattr()` defensivo para acessar atributos opcionais do parent,
tolerando ausência silenciosa (nunca levanta exception se o parent não tem o atributo).

### `sub_rlm.py` — Factories de Execução (1302 linhas)

O módulo principal que os consumidores importam. Contém:

**Helpers compartilhados (não-exportados):**
- `_prepare_child_env_kwargs(parent, ...)` — constrói env_kwargs com memória, canal,
  bus, cancel event. Elimina duplicação serial ↔ async.
- `_spawn_child_rlm(parent, ...)` — cria instância RLM filha com todas as propagações
  (backend, model, strategy, archive, system_prompt).
- `_propagate_cancel_token(child, cancel_event)` — bridge bidirecional entre
  `threading.Event` e `CancellationToken` do filho.

**Factory serial:** `make_sub_rlm_fn(parent) → sub_rlm(task, ...)`
- Cria filho RLM síncrono, executa, captura resultado
- Suporta `return_artifacts=True` para extração de closures

**Factory async:** `make_sub_rlm_async_fn(parent) → sub_rlm_async(task, ...)`
- Lança daemon thread, retorna `AsyncHandle` imediatamente
- Conecta SiblingBus compartilhado para coordenação P2P

**Factory parallel:** `make_sub_rlm_parallel_fn(parent) → (sub_rlm_parallel, sub_rlm_parallel_detailed)`
- `_run_parallel_core()` — coordenação unificada via ThreadPoolExecutor
- `sub_rlm_parallel(tasks)` → `list[str]` (interface simples)
- `sub_rlm_parallel_detailed(tasks)` → `SubRLMParallelDetailedResults`

**Re-exports:** Todos os 10 nomes públicos de `_sub_rlm_types` são re-exportados
via `from _sub_rlm_types import ... # noqa: F401`.

---

## 5. Bugs Encontrados e Corrigidos

### Bug #1: IndexError em arrays de configuração paralela [CRITICAL]

**Severidade: CRITICAL** — Crash em produção quando `len(tasks) > max_workers`.

**Causa**: Os arrays `_sys_prompts`, `_models` e `_interaction_modes` eram dimensionados
como `[None] * _n` onde `_n = min(len(tasks), max_workers)`. Porém, o loop de iteração
usava `enumerate(tasks)`, indexando até `len(tasks) - 1`.

```python
# ANTES (bugado)
_n = min(len(tasks), max_workers)
_sys_prompts = [None] * _n      # ex: [None, None, None, None]  (max_workers=4)
_models = [None] * _n
_interaction_modes = ["repl"] * _n

for idx, task in enumerate(tasks):   # idx pode ser 0..7 se tasks tem 8 itens
    _sys_prompts[idx] = ...          # IndexError quando idx >= 4!
```

**Depois**: Dimensionamento correto pelo número de tarefas:

```python
# DEPOIS (corrigido)
_task_count = len(tasks)
_sys_prompts = [None] * _task_count    # dimensiona pelo total de tasks
_models = [None] * _task_count
_interaction_modes = ["repl"] * _task_count
```

**Impacto**: Qualquer chamada `sub_rlm_parallel(tasks)` com mais tarefas que workers
(cenário comum — 8 tasks, 4 workers) causava `IndexError` silenciado pela thread,
resultando em branches com `None` onde deveria haver prompt/modelo específico.

### Bug #2: Docstring com default errado

**Severidade: Baixa** — Documentação incorreta.

`sub_rlm()` docstring dizia "Default 15" mas o parâmetro real era `max_iterations=8`.

```python
# ANTES
def sub_rlm(task, ..., max_iterations=8, ...):
    """... max_iterations: Default 15 ..."""

# DEPOIS
def sub_rlm(task, ..., max_iterations=8, ...):
    """... max_iterations: Default 8 ..."""
```

### Bug #3: `import time` redundante dentro de closures

**Severidade: Baixa** — Micro-ineficiência.

Três closures internas continham `import time` apesar de `import time` existir no
topo do módulo. O Python re-executa a busca em `sys.modules` a cada chamada (custo ~100ns,
irrelevante isoladamente, mas sujeira desnecessária).

```python
# ANTES (3 ocorrências em linhas 619, 1451, e dentro de _run_one_detailed)
def _run_one(branch_id, task):
    import time              # redundante — já importado no módulo
    start = time.perf_counter()
    ...

# DEPOIS
# Removidos. Módulo já tem `import time` no topo.
```

### Bug #4: Parâmetro `models` aceito mas nunca encaminhado

**Severidade: Média** — Feature silenciosamente quebrada.

`sub_rlm_parallel_detailed(tasks, ..., models=None)` aceitava uma lista de modelos
por branch, mas nunca a passava para `_serial_fn`. Cada branch sempre usava o modelo
padrão do pai, ignorando a customização.

```python
# ANTES
def sub_rlm_parallel_detailed(tasks, ..., models=None, ...):
    ...
    def _run_one_detailed(branch_id, task):
        answer = _serial_fn(task, ...)    # model nunca passado
        ...

# DEPOIS (unificado em _run_parallel_core)
def _run_one(branch_id, task):
    answer = _serial_fn(task, ..., model=_models[branch_id], ...)
```

### Bug #5: Defaults inconsistentes entre parallel e parallel_detailed

**Severidade: Baixa** — Comportamento inesperado.

`sub_rlm_parallel()` tinha `max_iterations=8`, mas `sub_rlm_parallel_detailed()`
tinha `max_iterations=15`. Sem razão documentada para a diferença.

```python
# ANTES
def sub_rlm_parallel(..., max_iterations=8, ...):    # 8
def sub_rlm_parallel_detailed(..., max_iterations=15, ...):  # 15 — por quê?

# DEPOIS — padronizado
def sub_rlm_parallel(..., max_iterations=8, ...):
def sub_rlm_parallel_detailed(..., max_iterations=8, ...):
```

---

## 6. Deduplicação: `_run_parallel_core()`

O maior ganho da refatoração. As duas funções `sub_rlm_parallel` e
`sub_rlm_parallel_detailed` compartilhavam ~400 linhas de infraestrutura idêntica:

| Bloco duplicado | Linhas (~) |
|---|---:|
| ThreadPoolExecutor setup + future submission | 35 |
| Cancel event creation + propagation | 25 |
| `as_completed` loop com stop condition | 60 |
| Timeout handler com cancel de branches pendentes | 30 |
| Replan handler (cancel + reinvocação com prompt refinado) | 40 |
| Heuristics computation + stop evaluation | 30 |
| Parent summary registration | 20 |
| Error formatting com prefixos `[ERRO branch N]` / `[CANCELLED branch N]` | 25 |
| Model/sys_prompt/interaction_mode arrays | 20 |
| Memory/channel/bus propagation | 25 |
| **Subtotal duplicado** | **~310** |

A diferença real entre as duas era **apenas o formato de retorno**:
- `sub_rlm_parallel` → `list[str]` (answer ou `[ERRO branch N] msg`)
- `sub_rlm_parallel_detailed` → `SubRLMParallelDetailedResults` (structured)

**Solução**: `_run_parallel_core()` executa toda a coordenação e retorna uma
tupla `(answers, errors, results, heuristics, strategy)`. Cada wrapper
formata o retorno no seu tipo.

```
_run_parallel_core(tasks, ...)
    → answers: list[str|None]
    → errors: list[str|None]
    → results: list[SubRLMParallelTaskResult]
    → heuristics: dict
    → strategy: dict

sub_rlm_parallel(tasks) → [answer or error_str for ...]
sub_rlm_parallel_detailed(tasks) → SubRLMParallelDetailedResults(...)
```

---

## 7. Deduplicação: Helpers Compartilhados de Spawn

Antes, cada factory (serial, async, parallel) construía manualmente os
`env_kwargs` e instanciava o filho com lógica repetida. Após:

| Helper | Antes | Depois |
|---|---|---|
| `_prepare_child_env_kwargs()` | 3 cópias (~25 linhas cada) | 1 função, 3 chamadas |
| `_spawn_child_rlm()` | 3 cópias (~30 linhas cada) | 1 função, 3 chamadas |
| `_propagate_cancel_token()` | 2 cópias (async + parallel) | 1 função, 2 chamadas |

**Redução**: ~130 linhas de duplicação eliminada.

---

## 8. Fixes em Testes Após o Swap

Ao substituir o monolito pelos 3 módulos, 3 testes quebraram por dependerem
de formatação exata de strings produzidas pela implementação:

### Fix 1: `par([])` levantava `SubRLMError` em vez de retornar `[]`

O guard `success_count == 0` disparava com `total_tasks == 0` porque
`0 == 0` é `True`. Adicionado early return antes do guard:

```python
if not tasks:
    return []
```

### Fix 2: Branches cancelados com formato de prefixo errado

Testes esperavam `[CANCELLED branch N] ...` como prefixo em erros de branches
cancelados. A reimplementação não formava o prefixo. Fix: detectar
`cancel_events[branch_id].is_set()` e formatar adequadamente.

### Fix 3: Branches com erro sem prefixo `[ERRO branch N]`

Similar ao anterior — testes esperavam o prefixo em todas as mensagens de erro.
Adicionado nos handlers de `except`, `as_completed/timeout`, e `replan`.

---

## 9. Backward Compatibility

### Re-exports

`sub_rlm.py` re-exporta todos os 10 símbolos públicos de `_sub_rlm_types.py`:

```python
from rlm.core.engine._sub_rlm_types import (  # noqa: F401
    SubRLMError, SubRLMDepthError, SubRLMTimeoutError,
    SubRLMResult, SubRLMArtifactResult, AsyncHandle,
    SubRLMParallelTaskResult, SubRLMParallelDetailedResults,
    SubRLMCallable, SubRLMParallelCallable,
)
```

### Importers externos inalterados

Apenas **2 arquivos de produção** importam de `sub_rlm`:

| Arquivo | Importa | Mudança necessária |
|---|---|---|
| `rlm_context_mixin.py` | `make_sub_rlm_fn`, `make_sub_rlm_parallel_fn`, `make_sub_rlm_async_fn`, `AsyncHandle`, `SubRLMParallelTaskResult` | Nenhuma |
| `role_orchestrator.py` | `make_sub_rlm_fn` | Nenhuma |

Os imports continuam apontando para `rlm.core.engine.sub_rlm` — os re-exports
garantem que tudo funciona sem alterar uma única linha nos consumidores.

### 3 arquivos de teste importam `SubRLMError`, `SubRLMTimeoutError`, etc.
Todos continuam funcionando via re-export.

---

## 10. Validação

```
$ python -m pytest tests/ -x -q
1708 passed in 142s
```

Todos os 1708 testes passaram após a refatoração, incluindo:
- Testes de `sub_rlm` serial, async e parallel
- Testes de stop condition e heuristics
- Testes de AsyncHandle lifecycle
- Testes de artifact extraction
- Testes de depth guard e timeout
- Testes de empty task list edge case
- Testes de cancel propagation

---

## 11. Resumo de Decisões

| Decisão | Justificativa |
|---|---|
| Separar types em módulo próprio | Folha sem dependências → importável isoladamente |
| Separar helpers em módulo próprio | Funções puras/stateless → testáveis isoladamente |
| Manter factories em sub_rlm.py | São a API pública; unificar spawn reduz risco |
| Re-exportar todos os tipos | Zero breaking changes para importers |
| `_run_parallel_core()` como função | Factories são closures → classe seria over-engineering |
| Prefixo `_` nos módulos auxiliares | Convenção Python para "não importe diretamente" |
| Early return para `tasks=[]` | Edge case que crashava sem necessidade |
| Padronizar `max_iterations=8` | Sem justificativa para 15 no detailed; 8 é o padrão serial |
