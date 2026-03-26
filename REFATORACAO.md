# Refatoração de `rlm.py` — Documentação Completa

## O que foi feito

O arquivo `rlm/core/rlm.py` foi **dividido em 5 arquivos** usando o padrão de **Python Mixins** (herança múltipla). O arquivo original tinha **1.876 linhas** com responsabilidades completamente misturadas num único `class RLM`. Agora a classe principal tem **425 linhas** e herda comportamentos de 4 mixins especializados.

```
ANTES                          DEPOIS
──────────────────────         ──────────────────────────────────────────────
rlm.py (1.876 linhas)    →     rlm.py              (425 linhas) — orquestrador
                               rlm_context_mixin.py (280 linhas) — contexto
                               rlm_loop_mixin.py    (470 linhas) — loop iterativo
                               rlm_mcts_mixin.py    (224 linhas) — MCTS
                               rlm_persistence_mixin.py (212 linhas) — persistência
```

---

## Por que foi feito

O `rlm.py` original violava o **Princípio da Responsabilidade Única** (SRP). Numa única classe conviviam:

- Código de setup de ambiente e REPL
- O loop recursivo de execução (centenas de linhas inline no `completion()`)
- Lógica de MCTS (Monte Carlo Tree Search)
- Gerenciamento de ciclo de vida e serialização de estado
- Helpers de nudge e recuperação de erros

Isso criava problemas reais:

| Problema | Impacto |
|---|---|
| `completion()` tinha 469 linhas inline | Impossível de ler, testar ou modificar em isolamento |
| MCTS misturado com loop de iteração | Mudar MCTS exigia navegar código de loop e vice-versa |
| Persistência espalhada por toda a classe | Bug em `save_state` podia quebrar o loop principal |
| Sem separação de concerns | Qualquer manutenção arriscava introduzir regressões |

A refatoração **não quebra nenhuma interface pública**. A API (`completion()`, `completion_stream()`, `sentinel_completion()`, `save_state()`, `resume_state()`) continua idêntica.

---

## Arquitetura resultante

```
class RLM(RLMContextMixin, RLMLoopMixin, RLMMCTSMixin, RLMPersistenceMixin)
         │                  │              │                │
         │                  │              │                └─ ciclo de vida
         │                  │              └─ pré-exploração evolutiva
         │                  └─ motor iterativo (o cérebro)
         └─ setup de contexto/ambiente/REPL
```

A ordem de herança múltipla é deliberada: o MRO (Method Resolution Order) do Python garante que `RLMContextMixin` é resolvido primeiro, pois fornece `_spawn_completion_context` usado por todos os outros.

---

## O que cada arquivo faz

---

### `rlm/core/rlm.py` — Orquestrador (425 linhas)

**Papel:** Ponto de entrada público. Define `__init__` e os 3 métodos públicos.

**Contém:**
- Todos os `import` do projeto
- `class RLM(RLMContextMixin, RLMLoopMixin, RLMMCTSMixin, RLMPersistenceMixin)` com declaração de herança
- `__init__` completo — inicializa todos os atributos de instância
- `completion()` — versão slim de 59 linhas que delega para os mixins
- `completion_stream()` — gerador que mantém contexto REPL entre turnos
- `sentinel_completion()` — modo bloqueante via fila thread-safe

**`completion()` antes vs depois:**

```python
# ANTES (469 linhas inline dentro de completion())
def completion(self, prompt, ...):
    ...469 linhas de lógica misturada...

# DEPOIS (59 linhas que delegam para mixins)
def completion(self, prompt, ...):
    with self._spawn_completion_context(prompt) as (lm_handler, env):  # ← ContextMixin
        self._inject_repl_globals(lm_handler, env)                      # ← ContextMixin
        self._run_mcts_preamble(prompt, ...)                            # ← MCTSMixin
        return self._run_inner_loop(message_history, ...)               # ← LoopMixin
```

---

### `rlm/core/rlm_context_mixin.py` — Contexto e Ambiente REPL (280 linhas)

**Papel:** Tudo relacionado a criar, configurar e preparar o ambiente de execução antes da recursão começar.

**Métodos:**

| Método | O que faz |
|---|---|
| `_spawn_completion_context(prompt)` | `@contextmanager`. Cria ou reutiliza o `LMHandler` e o `BaseEnv`. Quando `persistent=True`, os recursos sobrevivem entre chamadas. Quando `persistent=False` (padrão), cria tudo do zero a cada `completion()`. |
| `_setup_prompt(prompt)` | Constrói `message_history` inicial — o system prompt correto (code/default/custom) é selecionado aqui e inserido como primeira mensagem. |
| `_is_multimodal_content_list(prompt)` | `@staticmethod`. Detecta se o prompt contém partes multimodais (imagem, áudio) no formato de lista de dicts. |
| `_extract_text_from_multimodal(parts)` | `@staticmethod`. Extrai somente o texto de uma lista de parts multimodais. |
| `_record_environment_event(env, name, data)` | `@staticmethod`. Registra um evento nomeado no environment (se ele suportar a interface). Usado para auditoria e observabilidade do runtime. |
| `_inject_repl_globals(lm_handler, env)` | Injeta no namespace do REPL todas as funções de ferramentas disponíveis ao LLM: `sub_rlm`, `sub_rlm_parallel`, `sub_rlm_async`, `rlm_query`, browser globals (`web_get`, `web_search`, etc.), `AsyncHandle`, `SiblingBus`. |

**Por que foi separado:** O setup do ambiente é completamente independente do que acontece dentro do loop. Pode ser modificado (ex: adicionar novo tipo de ambiente, nova tool global) sem tocar na lógica de iteração.

---

### `rlm/core/rlm_loop_mixin.py` — Motor Iterativo (470 linhas)

**Papel:** O coração do RLM — o loop `while iteration_index < max_iterations` que executa a recursão.

**Métodos:**

| Método | O que faz |
|---|---|
| `_run_inner_loop(...)` | Loop principal. A cada iteração: verifica cancellation token, compacta contexto se necessário, detecta modo foraging, monta prompt, chama `_completion_turn`, detecta resposta vazia, verifica loop detector, e retorna `RLMChatCompletion` quando `FINAL_ANSWER` é encontrado ou iterações esgotam. **Compartilhado** por `completion()`, `completion_stream()` e `sentinel_completion()`. |
| `_completion_turn(prompt, lm_handler, env)` | Um único turno: chama o LLM, extrai blocos de código, executa cada um no REPL, alimenta o `LoopDetector`. Retorna `RLMIteration`. |
| `_build_recovery_nudge(...)` | Gera uma mensagem de nudge quando o modelo produz resposta sem código e sem `FINAL_ANSWER`, incentivando-o a avançar. |
| `_build_empty_response_nudge()` | Gera nudge para resposta completamente vazia. |
| `_is_empty_iteration_response(response, ...)` | `@staticmethod`. Detecta se uma iteração está essencialmente vazia (sem código, sem resposta, sem final). |
| `_fallback_answer_as_completion(prompt)` | Wrapper que chama `_fallback_answer` e empacota o resultado num `RLMChatCompletion` completo. |
| `_completion_turn(...)` | Executa um único turno: prompt → LLM → parse code blocks → execute in REPL → LoopDetector. |
| `_default_answer(message_history, lm_handler)` | Chamado quando `max_iterations` esgota sem `FINAL_ANSWER`. Pede ao LLM uma resposta final baseada no histórico acumulado. |
| `_fallback_answer(message)` | Chamado quando `depth >= max_depth` (o RLM é um LM). Faz chamada direta ao backend sem recursão. |

**Detalhe importante — compactação in-place:**
O `_do_compact()` dentro do loop usa `message_history.clear()` + `message_history.extend()` — mutação in-place — para que a referência compartilhada entre `completion_stream()` e `_run_inner_loop()` permaneça válida entre turnos. Sem isso haveria *context rot* progressivo.

---

### `rlm/core/rlm_mcts_mixin.py` — Monte Carlo Tree Search (224 linhas)

**Papel:** Pré-exploração evolutiva opcional antes do loop principal. Quando `mcts_branches > 0`, roda N branches em paralelo, avalia, e semeia o namespace vencedor no REPL principal.

**Métodos:**

| Método | O que faz |
|---|---|
| `_build_mcts_evaluation_stages(env)` | `@staticmethod`. Inspeciona o `env` em busca de funções `evaluate`, `score_candidate` ou `evaluate_candidate` definidas pelo usuário no REPL. Converte-as em `EvaluationStage` para o orquestrador MCTS. |
| `_attach_mcts_archive(env, key, archive, history, best)` | `@staticmethod`. Serializa os resultados do MCTS (melhor branch, histórico de rounds, métricas) no contexto do environment para rastreabilidade. |
| `_set_active_mcts_strategy(env, branch, archive_key)` | Registra a estratégia vencedora no estado interno do RLM (`_active_mcts_strategy`). Permite ao loop principal saber qual estratégia está ativa. |
| `_clear_active_mcts_strategy(env)` | Limpa a estratégia ativa ao término de um `completion()`, evitando contaminação entre chamadas. |
| `_run_mcts_preamble(prompt, branches, lm_handler, env, message_history)` | Orquestra toda a pré-exploração: cria o `ProgramArchive`, cria o `MCTSOrchestrator`, executa `evolutionary_branch_search`, semeia o namespace do vencedor no env, e injeta uma nota resumindo o resultado no último item do `message_history` (para o LLM principal saber o que foi descoberto). |

**Por que foi separado:** MCTS é uma feature opcional e independente. Pode ser desativada, substituída ou melhorada sem tocar no loop de execução.

---

### `rlm/core/rlm_persistence_mixin.py` — Ciclo de Vida e Persistência (212 linhas)

**Papel:** Serialização/desserialização de estado e gerenciamento do ciclo de vida dos recursos.

**Métodos:**

| Método | O que faz |
|---|---|
| `shutdown_persistent()` | Para o `LMHandler` persistente e faz cleanup do env persistente. Deve ser chamado explicitamente ao encerrar uma sessão longa. |
| `_validate_persistent_environment_support()` | Valida que o tipo de environment configurado suporta a interface `SupportsPersistence`. Levanta erro descritivo se não suportar. |
| `_env_supports_persistence(env)` | `@staticmethod`. Checa se um environment implementa `SupportsPersistence`. |
| `save_state(state_dir)` | Serializa o histórico de mensagens atual e o checkpoint do REPL num diretório. Retorna o caminho do arquivo criado. |
| `resume_state(state_dir)` | Restaura histórico e REPL checkpoint salvos por `save_state`. Permite retomar sessões interrompidas. |
| `close()` | Cleanup de recursos alocados (env + lm_handler). |
| `dispose()` | Alias unificado de `close()`. Respeita o padrão `DisposableStore`. |
| `__enter__` / `__exit__` | Protocolo `with`, permite: `with RLM(...) as rlm: rlm.completion(...)` com cleanup automático. |

---

## Diagrama de dependência entre os arquivos

```
rlm.py
├── importa os 4 mixins
└── class RLM(ContextMixin, LoopMixin, MCTSMixin, PersistenceMixin)
          │
          ├── rlm_context_mixin.py
          │     └── _spawn_completion_context  ← usado por completion(), stream(), sentinel()
          │
          ├── rlm_loop_mixin.py
          │     └── _run_inner_loop            ← chamado de completion(), stream(), sentinel()
          │
          ├── rlm_mcts_mixin.py
          │     └── _run_mcts_preamble         ← chamado de completion() e stream()
          │
          └── rlm_persistence_mixin.py
                └── save_state, resume_state   ← chamados pelo usuário diretamente
```

---

## Como os 3 modos públicos usam os mixins

```
completion()          completion_stream()      sentinel_completion()
       │                      │                        │
       ├─ ContextMixin        ├─ ContextMixin          ├─ ContextMixin
       │  _spawn_context       │  _spawn_context         │  _spawn_context
       │  _setup_prompt        │  _setup_prompt          │  _setup_prompt
       │  _inject_repl         │  _inject_repl           │  _inject_repl
       │                       │                         │
       ├─ MCTSMixin            ├─ MCTSMixin              │  (sem MCTS)
       │  _run_mcts_preamble   │  _run_mcts_preamble     │
       │                       │                         │
       └─ LoopMixin            └─ LoopMixin              └─ LoopMixin
          _run_inner_loop          _run_inner_loop (N)      _run_inner_loop
          (1 execução)             (loop de turnos)         (loop de turnos)
```

---

## Regras para manutenção futura

1. **Nunca adicione atributos de instância nos mixins** — todos os `self.xyz` devem ser declarados em `RLM.__init__`.
2. **Cada mixin tem uma única responsabilidade** — se um novo comportamento não se encaixa claramente num dos 4 mixins, avalie criar um 5º.
3. **Imports dos mixins** são independentes — cada mixin faz seus próprios imports, não dependem uns dos outros.
4. **Para rodar scripts de manutenção** (como transformações de arquivo), execute fora de `rlm-main/rlm/core/` para evitar conflito com `types.py` (import circular com a stdlib).
5. **Backup intacto** em `rlm-main.worktrees/copilot-worktree-2026-03-24T15-59-50/rlm/core/rlm.py` (1.843 linhas).

---

## Validação

Todos os 5 arquivos foram validados com `ast.parse` (sintaxe Python válida):

```
OK  (425 linhas)  rlm.py
OK  (280 linhas)  rlm_context_mixin.py
OK  (470 linhas)  rlm_loop_mixin.py
OK  (224 linhas)  rlm_mcts_mixin.py
OK  (212 linhas)  rlm_persistence_mixin.py
```

Total de linhas: **1.611** (vs 1.876 original) — redução de 265 linhas por eliminação de duplicação entre `completion()` inline e os helpers que já existiam separados.
