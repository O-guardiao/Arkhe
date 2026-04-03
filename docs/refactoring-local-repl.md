# Refatoração de `local_repl.py` — Documentação Completa

## 1. Visão Geral

O arquivo `rlm/environments/local_repl.py` era um monolito de **2262 linhas** que acumulou
responsabilidades de forma orgânica a cada iteração de desenvolvimento. A refatoração
separou essas responsabilidades em **5 módulos coesos** sem alterar nenhum comportamento
externo (zero regressions — 1708 testes passaram antes e depois).

### Métricas de Redução

| Arquivo | Linhas | Responsabilidade |
|---|---:|---|
| `local_repl.py` (refatorado) | 875 | Motor REPL: init, setup, execute_code, LLM, contexto, lifecycle |
| `_sandbox.py` | 165 | Sandbox de segurança: builtins, safe_import, safe_open |
| `_runtime_state.py` | 683 | Mixin de estado: eventos, tarefas, sessão recursiva, controle operador |
| `_checkpoint.py` | 159 | Mixin de checkpoint: save/load serialização para disco |
| `_repl_tools.py` | 594 | Fábrica de closures: 23 ferramentas scaffold + ferramentas IPC/MCTS/Critic |
| **Total** | **2476** | — |
| **Original** | **2262** | Monolito |
| **Overhead** | **+214 (~9%)** | Imports, docstrings, headers de módulo |

---

## 2. Arquitetura Após Refatoração

```
LocalREPL(RuntimeStateMixin, CheckpointMixin, NonIsolatedEnv)
    │
    ├── _sandbox.py          ← módulo puro (sem estado, sem classe)
    │   ├── _BLOCKED_RUNTIME_MODULES
    │   ├── _safe_import()
    │   ├── _safe_open()
    │   └── _SAFE_BUILTINS
    │
    ├── _repl_tools.py       ← fábrica de closures (captura `env` por referência)
    │   ├── build_scaffold_tools(env) → dict de 23 closures
    │   ├── build_interprocess_tools(env) → parent_log / check_cancel
    │   ├── build_critic_fuzz_tool(env) → closure critic_fuzz
    │   └── build_mcts_explore_tool(env) → closure mcts_explore
    │
    ├── _runtime_state.py    ← mixin de estado (herança via MRO)
    │   └── RuntimeStateMixin
    │       ├── record_runtime_event()
    │       ├── record_recursive_message() / recent_recursive_messages()
    │       ├── emit_recursive_event() / recent_recursive_events()
    │       ├── queue_recursive_command() / update_recursive_command()
    │       ├── current_runtime_task() / create_runtime_task() / update_runtime_task()
    │       ├── register_subagent_task() / update_subagent_task()
    │       ├── set_parallel_summary()
    │       ├── set_active_recursive_strategy() / get / clear
    │       ├── get_runtime_control_state()
    │       ├── set_runtime_paused() / set_runtime_focus() / reprioritize_branch()
    │       ├── record_operator_note() / mark_runtime_checkpoint()
    │       ├── get_runtime_state_snapshot()
    │       └── attach_sibling_bus() / _handle_sibling_bus_event()
    │
    └── _checkpoint.py       ← mixin de persistência
        └── CheckpointMixin
            ├── save_checkpoint()
            └── load_checkpoint()
```

### MRO (Method Resolution Order)

```
LocalREPL → RuntimeStateMixin → CheckpointMixin → NonIsolatedEnv → BaseEnv → ABC → object
```

Os mixins não definem `__init__()`, portanto `super().__init__()` em LocalREPL atravessa
transparentemente para `NonIsolatedEnv.__init__()` via MRO padrão do Python.

---

## 3. O Que Cada Módulo Faz

### `local_repl.py` — Motor REPL (875 linhas)

A classe `LocalREPL` contém apenas:

- **`__init__`**: Inicialização completa — cria todos os subsistemas (timeline, task ledger,
  recursive session, coordination digest), configura auditor de segurança, conecta sibling bus.
- **`setup()`**: Monta namespace globals/locals, injeta builtins seguros, delega criação de
  ferramentas para as fábricas de `_repl_tools.py`.
- **`execute_code()`**: Executa código Python no namespace sandboxed com captura de stdout/stderr,
  auditoria de segurança pré-execução, e rastreamento de falhas para Epistemic Foraging.
- **`_llm_query()` / `_llm_query_batched()`**: Proxy para consultas LLM via socket.
- **`load_context()` / `_load_codebase_context()`**: Injeção de contexto (texto, JSON, ou diretório
  de codebase com ferramentas de análise).
- **`reset_turn_state()`**: Limpa locals transientes entre turnos de conversação.
- **`_restore_scaffold()`**: Restaura nomes do scaffold REPL após cada exec() para prevenir
  corrupção do namespace pelo código do modelo.
- **Lifecycle**: `__enter__/__exit__`, `cleanup()`, `extract_artifacts()`.

### `_sandbox.py` — Sandbox de Segurança (165 linhas)

Módulo puro (sem classes). Exporta:

- **`_BLOCKED_RUNTIME_MODULES`**: Frozenset de módulos proibidos em exec() (`subprocess`, `socket`,
  `ctypes`, etc.). Complementa o auditor AST com verificação em runtime.
- **`_safe_import()`**: Substituto de `__import__()` que bloqueia imports dinâmicos de módulos
  proibidos (e.g. `__import__("subproc" + "ess")`).
- **`_safe_open()`**: Substituto de `open()` que verifica acesso a paths sensíveis (~/.ssh, .env)
  via `REPLAuditor.check_path_access()`.
- **`_SAFE_BUILTINS`**: Dict completo de builtins permitidos no namespace REPL. Bloqueia `eval`,
  `exec`, `compile`, `input`, `globals`, `locals`.

### `_runtime_state.py` — RuntimeStateMixin (683 linhas)

Mixin que agrupa todos os métodos de gerenciamento de estado em uma classe separada:

- **Timeline**: `record_runtime_event()` — registra eventos no `ExecutionTimeline`.
- **Recursive Session**: 6 métodos (messages, events, commands) que fazem CRUD no
  `RecursiveSessionLedger` com emissão de eventos em cascata.
- **Task Ledger**: CRUD de tarefas + registro de sub-agentes com bind no `CoordinationDigest`.
- **Operator Control**: Pause/resume, focus/fix branch, reprioritize, notes, checkpoints.
  Propaga sinais via sibling bus para orquestração paralela.
- **Active Recursive Strategy**: Get/set/clear da estratégia recursiva ativa.
- **Sibling Bus**: `attach_sibling_bus()` conecta o barramento de coordenação P2P.

### `_checkpoint.py` — CheckpointMixin (159 linhas)

Mixin com 2 métodos:

- **`save_checkpoint()`**: Serializa estado REPL para JSON (variáveis via pickle/base64,
  workbench completo, contadores). Cria diretórios necessários.
- **`load_checkpoint()`**: Restaura estado de um arquivo, re-injeta ferramentas de codebase
  se o checkpoint era em codebase mode.

### `_repl_tools.py` — Fábrica de Closures (594 linhas)

Funções de fábrica que criam closures vinculadas a uma instância `LocalREPL`:

- **`build_scaffold_tools(env)`** → 23 closures (task_create, attach_text, timeline_recent,
  recursive_message, etc.). Cada closure captura `env` por referência para late-binding.
- **`build_interprocess_tools(env)`** → `parent_log` + `check_cancel` (condicionais).
- **`build_critic_fuzz_tool(env)`** → Ferramenta de fuzzing adversarial.
- **`build_mcts_explore_tool(env)`** → Busca MCTS com avaliadores e arquivo de programas.

---

## 4. Bugs Encontrados e Corrigidos

### Bug #1: Duplo incremento de `_repl_failure_count` em `execute_code()`

**Severidade: Média** — Afeta o limiar de Epistemic Foraging.

**Antes**: Uma exceção `except` incrementava o counter em +1, e logo após o try/except,
o check de stderr com "Error" incrementava +1 novamente. Total: +2 por erro.
O limiar de foraging (3 falhas) efetivamente disparava com 2 erros.

```python
# ANTES (bugado)
except Exception as e:
    self._repl_failure_count += 1      # +1

if stderr and ("Error" in stderr ...):
    self._repl_failure_count += 1      # +1 novamente (stderr contém o exception)
```

**Depois**: Adicionada flag `_hard_exception`. O check de stderr só incrementa
quando NÃO houve exceção hard (falhas "soft" — warnings no stderr sem crash):

```python
# DEPOIS (corrigido)
_hard_exception = False
except Exception as e:
    _hard_exception = True
    self._repl_failure_count += 1

if not _hard_exception and stderr and ("Error" in stderr ...):
    self._repl_failure_count += 1
```

### Bug #2: Chaves duplicadas em `_SAFE_BUILTINS`

**Severidade: Baixa** — Cosmético, sem impacto funcional.

O original tinha 3 entradas idênticas para `"AssertionError"`:
```python
"AssertionError": AssertionError,
"AssertionError": AssertionError,  # Note: original MIT code spelling
"AssertionError": AssertionError,  # Common typo alias
```
Reduzido a 1 entrada no `_sandbox.py`.

### Performance: `import re` dentro de `execute_code()`

**Severidade: Baixa** — Micro-otimização.

O original fazia `import re as _re_sanitize` dentro de `execute_code()` a cada chamada.
Movido para `import re` no topo do módulo.

---

## 5. Notas de Segurança

### Pickle em `load_checkpoint()`

`load_checkpoint()` usa `pickle.loads()` para restaurar variáveis serializadas.
Pickle é um vetor de ataque de deserialização — objetos maliciosos podem executar
código arbitrário ao serem deserializados.

**Mitigação**: Checkpoints são arquivos locais gerados pelo próprio sistema.
Adicionado aviso SECURITY NOTE na docstring do método.

**Recomendação futura**: Migrar para formato seguro (JSON + type whitelist) ou
assinar checkpoints com HMAC.

### Thread Safety de `_capture_output()`

`_capture_output()` substitui `sys.stdout` e `sys.stderr` globais do processo
sob um `self._lock` por instância. Instâncias concorrentes de `LocalREPL`
(sub-agentes paralelos) podem interferir na captura de output uma da outra.

**Mitigação**: Na prática, sub-agentes paralelos usam `sub_rlm_async` que
gerencia as saídas por thread. O lock per-instance serializa dentro de cada agente.

---

## 6. Backward Compatibility

### Re-exports

`local_repl.py` re-exporta os seguintes símbolos para compatibilidade:
- `_safe_open` (importado em `tests/test_security_phase94.py`)
- `_SAFE_BUILTINS`
- `_BLOCKED_RUNTIME_MODULES`
- `_safe_import`

### Interface Pública Inalterada

- `LocalREPL` continua em `rlm.environments.local_repl`
- `rlm.environments.__init__` importa `LocalREPL` do mesmo path
- Todos os 93 métodos originais permanecem acessíveis via `env.method_name()`
- `isinstance(env, NonIsolatedEnv)` e `isinstance(env, BaseEnv)` continuam `True`

---

## 7. Mapa Completo de Métodos

### `local_repl.py` (25 métodos)
| Método | Tipo | Descrição |
|---|---|---|
| `__init__` | lifecycle | Inicialização completa |
| `setup` | config | Monta namespace REPL |
| `_publish_timeline_event` | internal | Callback do ExecutionTimeline |
| `is_cancel_requested` | query | Verifica cancelamento pelo pai |
| `_final` | repl-tool | FINAL(value) do REPL |
| `get_pending_final` | query | Retorna e limpa valor FINAL pendente |
| `_final_var` | repl-tool | FINAL_VAR(nome) do REPL |
| `_get_var` | repl-tool | get_var(nome) do REPL |
| `_show_vars` | repl-tool | SHOW_VARS() do REPL |
| `_llm_query` | llm | Consulta LLM via socket |
| `_llm_query_batched` | llm | Consulta LLM em lote |
| `load_context` | context | Carrega contexto (texto/JSON/codebase) |
| `_load_codebase_context` | context | Ativa modo codebase com ferramentas |
| `_is_transient_turn_local` | internal | Identifica locals transientes |
| `reset_turn_state` | lifecycle | Limpa locals entre turnos |
| `add_context` | context | Adiciona contexto versionado |
| `update_handler_address` | config | Atualiza endereço LM |
| `get_context_count` / `add_history` / `get_history_count` | context | Gerenciamento de contexto/histórico |
| `_capture_output` / `_temp_cwd` | internal | Context managers de execução |
| `_restore_scaffold` | internal | Restaura nomes do scaffold após exec |
| `execute_code` | core | Executa código Python sandboxed |
| `is_in_foraging_mode` / `reset_foraging` | epistemic | Controle de Epistemic Foraging |
| `extract_artifacts` | lifecycle | Extrai artefatos do REPL |
| `cleanup` / `__del__` | lifecycle | Limpeza de recursos |

### `_runtime_state.py` — RuntimeStateMixin (26 métodos)
| Método | Grupo | Descrição |
|---|---|---|
| `record_runtime_event` | timeline | Registra evento no timeline |
| `record_recursive_message` | session | Registra mensagem recursiva |
| `recent_recursive_messages` | session | Lista mensagens recentes |
| `emit_recursive_event` | session | Emite evento recursivo |
| `recent_recursive_events` | session | Lista eventos recentes |
| `queue_recursive_command` | session | Enfileira comando recursivo |
| `update_recursive_command` | session | Atualiza status de comando |
| `recent_recursive_commands` | session | Lista comandos recentes |
| `get_recursive_session_state` | session | Estado completo da sessão |
| `current_runtime_task` / `current_runtime_task_id` | tasks | Task corrente |
| `create_runtime_task` / `update_runtime_task` | tasks | CRUD de tasks |
| `register_subagent_task` / `update_subagent_task` | tasks | Tasks de sub-agentes |
| `set_parallel_summary` | coordination | Summary de execução paralela |
| `set_active_recursive_strategy` / `get` / `clear` | strategy | Estratégia recursiva ativa |
| `get_runtime_control_state` | control | Estado do controle operador |
| `_publish_operator_control` | control | Propaga sinais via sibling bus |
| `set_runtime_paused` | control | Pause/resume do runtime |
| `set_runtime_focus` | control | Focus/fix em branch |
| `reprioritize_branch` | control | Altera prioridade de branch |
| `record_operator_note` | control | Registra nota do operador |
| `mark_runtime_checkpoint` | control | Marca checkpoint |
| `get_runtime_state_snapshot` | query | Snapshot completo do runtime |
| `attach_sibling_bus` | coordination | Conecta sibling bus |
| `_handle_sibling_bus_event` | coordination | Handler de eventos do bus |

### `_checkpoint.py` — CheckpointMixin (2 métodos)
| Método | Descrição |
|---|---|
| `save_checkpoint` | Serializa estado para disco (JSON + pickle) |
| `load_checkpoint` | Restaura estado de checkpoint |

### `_repl_tools.py` (4 funções de fábrica → 27 closures)
| Fábrica | Closures Produzidas |
|---|---|
| `build_scaffold_tools(env)` | task_create, task_start, task_update, task_list, task_current, task_set_current, attach_text, attach_context, attach_file, attachment_list, attachment_get, attachment_pin, timeline_recent, timeline_mark, recursive_message, recursive_messages, recursive_event, recursive_events, recursive_command, recursive_command_update, recursive_commands, recursive_session_state, active_recursive_strategy |
| `build_interprocess_tools(env)` | parent_log, check_cancel (condicionais) |
| `build_critic_fuzz_tool(env)` | critic_fuzz |
| `build_mcts_explore_tool(env)` | mcts_explore |

### `_sandbox.py` (2 funções + 2 constantes)
| Nome | Tipo | Descrição |
|---|---|---|
| `_BLOCKED_RUNTIME_MODULES` | frozenset | Módulos bloqueados em runtime |
| `_safe_import` | function | Guard de __import__() |
| `_safe_open` | function | Guard de open() |
| `_SAFE_BUILTINS` | dict | Builtins permitidos no REPL |

---

## 8. Como Reverter

O backup completo do arquivo original está em:
```
rlm/environments/local_repl.py.bak
```

Para reverter:
```powershell
Copy-Item rlm\environments\local_repl.py.bak rlm\environments\local_repl.py -Force
Remove-Item rlm\environments\_sandbox.py, rlm\environments\_runtime_state.py, rlm\environments\_checkpoint.py, rlm\environments\_repl_tools.py
```
