# RLM: Original vs Editado — Mapa Completo de Transformação

> **Versão original:** `rlm_original/` (alexzhang13/rlm — commit base)  
> **Versão editada:** `RLM_OpenClaw_Engine/rlm-main/` (este repositório)  
> **Análise gerada em:** Março 2026

---

## Índice Rápido

1. [Resumo Executivo — O que é cada versão](#1-resumo-executivo)
2. [Inventário de Arquivos — Adicionados, Removidos, Modificados](#2-inventário-de-arquivos)
3. [Mudanças em Arquivos Existentes — Diff Anotado](#3-mudanças-em-arquivos-existentes)
4. [Módulos Novos — O que foi construído do zero](#4-módulos-novos)
5. [Linha do Tempo das Evoluções](#5-linha-do-tempo-das-evoluções)
6. [Arquitetura: Antes vs Depois](#6-arquitetura-antes-vs-depois)
7. [Decisões de Projeto Documentadas](#7-decisões-de-projeto-documentadas)

---

## 1. Resumo Executivo

### Versão Original (`rlm_original`)

O repositório original é uma **biblioteca Python** criada por Alex Zhang (MIT), publicada sob MIT License.
Propósito: fornecer uma classe `RLM` reutilizável que executa o padrão "Recursive Language Model" —
um LLM que resolve tarefas iterativamente num REPL local, podendo fazer sub-chamadas recursivas
a outros LLMs.

**Características do original:**
- Biblioteca de uso em linha de comando ou como import em notebooks
- Sem servidor HTTP, sem persistência, sem multi-dispositivo
- 5 ambientes de execução: local, Docker, E2B, Modal, Daytona, Prime
- Callbacks event-driven opcionais (`on_subcall_start`, etc.)
- Controles de segurança: `max_budget`, `max_timeout`, `max_tokens`, `max_errors`
- Package name: `rlms` (PyPI), versão 0.1.1

### Versão Editada (`RLM_OpenClaw_Engine/rlm-main`)

O repositório editado é um **daemon servidor persistente multi-canal**, construído
sobre a mesma base da classe `RLM`. Em vez de ser uma biblioteca, tornou-se um
**sistema completo de agente autônomo com API REST, WebSocket, múltiplos canais
de mensagens e memória persistente**.

**Características da versão editada:**
- Servidor FastAPI completo com ~12 endpoints
- 8 canais de comunicação: Discord, Telegram, Slack, WhatsApp, WebChat, Webhook, OpenAI-compat, WebSocket
- Memória persistente em SQLite com busca semântica via embeddings
- Sistema de sessões multi-cliente com isolamento por `client_id`
- Supervisor de execução com timeout, abort remoto e detecção de loop
- CLI interativo com wizard de configuração
- MCTS (Monte Carlo Tree Search) para exploração de soluções paralelas
- Foraging Mode (modo exploração epistêmica)
- Skills carregáveis dinamicamente
- Package name: `rlm`, versão 0.1.0

---

## 2. Inventário de Arquivos

### 2a. Visão Geral

| Categoria | Contagem |
|---|---|
| Arquivos no original | 28 `.py` |
| Arquivos na versão editada | 83 `.py` |
| Adicionados | +55 arquivos novos |
| Removidos do original | 3 arquivos (`utils/exceptions.py`, `utils/token_utils.py`, `environments/e2b_repl.py`) |
| Diretórios novos | `cli/`, `plugins/`, `server/`, `skills/`, `static/`, `tools/` |

### 2b. Arquivos removidos / renomeados / absorvidos

| Arquivo original | Status na versão editada | Motivo |
|---|---|---|
| `utils/exceptions.py` | **Removido** | Exceções (`BudgetExceededError`, `CancellationError`, `TokenLimitExceededError`, etc.) foram eliminadas. A versão editada usa o `SupervisorConfig` + `ExecutionResult` com status string para controlar limites, não exceções Python. |
| `utils/token_utils.py` | **Removido** | `count_tokens()` e `get_context_limit()` foram substituídos por lógica interna no `ContextCompactor` (novo). O controle de token budget passou para o Supervisor. |
| `environments/e2b_repl.py` | **Removido** | Sandbox E2B descartado. A versão editada prioriza Docker local e o SandboxREPL do MCTS para isolamento. |
| `core/types.py` — `UsageSummary` | **Removida** da versão edit. | Rastreamento de custo ($) removido — o Supervisor não usa budget em USD, usa tempo e iterações. |

### 2c. Arquivos modificados (com delta de linhas)

| Arquivo | Original | Editado | Delta | Nível de mudança |
|---|---|---|---|---|
| `rlm/core/rlm.py` | 851 linhas | 738 linhas | -113 | **Alto** — parâmetros removidos, novos módulos integrados |
| `rlm/utils/prompts.py` | 197 linhas | 332 linhas | +135 | **Alto** — 3 novos system prompts adicionados |
| `rlm/environments/local_repl.py` | 539 linhas | 707 linhas | +168 | **Alto** — ferramentas de codebase injetadas |
| `rlm/environments/base_env.py` | 384 linhas | 182 linhas | -202 | **Alto** — custom tools support removido (movido para tools/) |
| `rlm/clients/openai.py` | 170 linhas | 129 linhas | -41 | **Médio** — simplificado |
| `rlm/logger/rlm_logger.py` | 91 linhas | 63 linhas | -28 | **Médio** |
| `rlm/core/lm_handler.py` | 233 linhas | 211 linhas | -22 | **Baixo** |
| `rlm/utils/parsing.py` | 176 linhas | 174 linhas | -2 | **Mínimo** |
| `rlm/core/comms_utils.py` | 264 linhas | 264 linhas | 0 | **Inalterado** |
| `rlm/utils/rlm_utils.py` | 12 linhas | 12 linhas | 0 | **Inalterado** |
| `rlm/environments/constants.py` | 32 linhas | 32 linhas | 0 | **Inalterado** |
| `pyproject.toml` | 83 linhas | 69 linhas | -14 | Dependências simplificadas |
| `AGENTS.md` | 324 linhas | 320 linhas | -4 | Mínimo |

### 2d. Novos diretórios e arquivos

#### `rlm/server/` — Backend HTTP completo (criado do zero)
| Arquivo | Propósito |
|---|---|
| `api.py` | Gateway principal FastAPI: todos os endpoints REST, lifecycle, plugin loading |
| `webhook_dispatch.py` | Receptor de webhooks externos com rate limiting por IP, HMAC auth |
| `openai_compat.py` | Endpoint `/v1/chat/completions` compatível com OpenAI API — substitui o RLM como backend de qualquer cliente que fale OpenAI |
| `ws_server.py` | Servidor WebSocket para streaming de eventos em tempo real (`RLMEventBus`) |
| `webchat.py` | Interface chat HTML/JS servida diretamente pelo FastAPI via SSE |
| `event_router.py` | Roteador de eventos: mapeia `source_pattern → action_handler` |
| `scheduler.py` | Scheduler server-side: expõe CronJobs via HTTP |
| `discord_gateway.py` | Gateway Discord (webhook recebe POSTs do Discord) |
| `telegram_gateway.py` | Gateway Telegram (long polling ou webhook Telegram) |
| `slack_gateway.py` | Gateway Slack (Events API) |
| `whatsapp_gateway.py` | Gateway WhatsApp (webhook Meta) |

#### `rlm/plugins/` — Canais de comunicação como plugins
| Arquivo | Propósito |
|---|---|
| `channel_registry.py` | Registro central de canais ativos |
| `discord.py` | Plugin Discord: formata resposta para Discord (Embeds, markdown) |
| `telegram.py` | Plugin Telegram: formata resposta (MarkdownV2) e envia via Bot API |
| `slack.py` | Plugin Slack: formata para Block Kit |
| `whatsapp.py` | Plugin WhatsApp: envia via Cloud API Meta |
| `browser.py` | Plugin navegador: `make_browser_globals()` injeta `browse_url()`, `screenshot()` no REPL |
| `audio.py` | Plugin áudio: `transcribe_audio()`, `text_to_speech()` via Whisper/TTS |
| `mcp.py` | Plugin MCP (Model Context Protocol): expõe tools via protocolo padronizado |

#### `rlm/core/` — Módulos de núcleo novos
| Arquivo | Propósito |
|---|---|
| `session.py` | `RLMSession` dataclass + `SessionManager` com SQLite (sessões isoladas por `client_id`) |
| `supervisor.py` | `RLMSupervisor`: envelope de execução com timeout, abort remoto, error-loop detection |
| `security.py` | `InputThreatReport` (21 padrões de injeção), `EnvVarShield`, `REPLAuditor` AST-based |
| `memory_manager.py` | `MemoryManager`: SQLite + embeddings + busca semântica (`memory_store`, `memory_search`) |
| `mcts.py` | `MCTSOrchestrator`: exploração paralela de branches REPL (Monte Carlo Tree Search) |
| `compaction.py` | `ContextCompactor`: compacta histórico de mensagens quando ultrapassa threshold de tokens |
| `loop_detector.py` | `LoopDetector`: detecta repetição de padrões no `stdout` do REPL (n-gram hashing) |
| `hooks.py` | `HookSystem`: callbacks registráveis para eventos do agente (`on_iteration_start`, etc.) |
| `sub_rlm.py` | `make_sub_rlm_fn()` e `make_sub_rlm_parallel_fn()`: fábricas de funções `rlm_query` para o REPL |
| `fast.py` | `find_code_blocks()`, `find_final_answer()` reescritos em Python puro otimizado (sem regex complexo) |
| `scheduler.py` | `RLMScheduler` + `CronJob`: cron jobs gerenciados em background thread |
| `skill_loader.py` | `SkillLoader`: carrega skills Python de `RLM_SKILLS_DIR` como ferramentas REPL |
| `exec_approval.py` | `ExecApprovalGate`: requer aprovação humana antes de executar código de alto risco |
| `sif.py` | SIF (Structured Iterative Feedback): loop de feedback estruturado para code review |
| `mcp_client.py` | Cliente MCP: conecta ao servidor MCP e expõe tools como funções REPL |
| `structured_log.py` | Logger estruturado JSON para análise de execução |
| `optimized.py` | Backend otimizado em Python para parsing, transporte e mensagens LM |

#### `rlm/cli/` — Interface de linha de comando
| Arquivo | Propósito |
|---|---|
| `main.py` | Entry point `rlm` (registrado no pyproject.toml como script) |
| `service.py` | Comandos: `rlm start`, `rlm stop`, `rlm status` (gerenicia o daemon) |
| `wizard.py` | `rlm init` — wizard interativo de configuração inicial com rich |

#### `rlm/tools/` — Ferramentas injetadas no REPL
| Arquivo | Propósito |
|---|---|
| `codebase.py` | `list_files()`, `read_file()`, `search_code()`, `file_outline()`, `file_stats()`, `directory_tree()` — ferramentas de análise de codebase |
| `memory.py` | `memory_store()`, `memory_read()`, `memory_analyze()`, `memory_link()`, `memory_list()` — API de memória no REPL |
| `memory_tools.py` | `memory_chunk_and_store()`, `memory_reassemble()`, `memory_batch_analyze()`, `memory_semantic_search()` — ferramentas avançadas de memória |
| `embeddings.py` | `embed_text()`: gera embeddings via OpenAI text-embedding-3-small para busca semântica |
| `critic.py` | `rlm_critic()`: sub-agente especializado em criticar e melhorar respostas |

---

## 3. Mudanças em Arquivos Existentes

### 3a. `rlm/core/rlm.py` — O núcleo do agente

**Parâmetros REMOVIDOS do `__init__`:**

| Parâmetro removido | Tipo | Motivo da remoção |
|---|---|---|
| `max_budget: float` | float | Rastreamento de custo em USD foi delegado ao Supervisor/provedor |
| `max_timeout: float` | float | Timeout agora gerenciado pelo `RLMSupervisor` fora do RLM |
| `max_tokens: int` | int | Limite de tokens delegado ao `ContextCompactor` |
| `max_errors: int` | int | Erro loop delegado ao `LoopDetector` |
| `custom_tools: dict` | dict | Movido para `ToolRegistry` externo |
| `custom_sub_tools: dict` | dict | Idem |
| `compaction: bool` | bool | Sempre ativo via `ContextCompactor.enabled` |
| `compaction_threshold_pct: float` | float | Config em `CompactionConfig` |
| `on_subcall_start: Callable` | Callable | Substituído por `HookSystem` |
| `on_subcall_complete: Callable` | Callable | Idem |
| `on_iteration_start: Callable` | Callable | Idem |
| `on_iteration_complete: Callable` | Callable | Idem |

**Parâmetros ADICIONADOS ao `__init__`:**

| Parâmetro novo | Tipo | Propósito |
|---|---|---|
| `event_bus: Any` | Any | `RLMEventBus` para streaming de eventos via WebSocket |

**Importações REMOVIDAS:**

```python
# Original importava de utils — na versão editada passaram para core:
from rlm.utils.exceptions import (BudgetExceededError, CancellationError,
    ErrorThresholdExceededError, TimeoutExceededError, TokenLimitExceededError)
from rlm.utils.token_utils import count_tokens, get_context_limit
```

**Importações ADICIONADAS:**

```python
# Versão editada importa da nova arquitetura:
from rlm.core.fast import find_code_blocks, find_final_answer     # parsing rápido
from rlm.core.mcts import MCTSOrchestrator, generate_branch_variants  # exploração paralela
from rlm.core.compaction import ContextCompactor, CompactionConfig     # compactação
from rlm.core.loop_detector import LoopDetector, LoopDetectorConfig   # anti-loop
from rlm.core.hooks import HookSystem                                   # callbacks
from rlm.core.sub_rlm import make_sub_rlm_fn, make_sub_rlm_parallel_fn # sub-agentes
from rlm.plugins.browser import make_browser_globals                    # ferramentas browser
from rlm.utils.prompts import (RLM_CODE_SYSTEM_PROMPT,                 # novos prompts
    RLM_FORAGING_SYSTEM_PROMPT, build_multimodal_user_prompt)
```

**Mudança arquitetural: `_spawn_completion_context`**

```
ORIGINAL: passava custom_tools, custom_sub_tools, compaction como env_kwargs
          - O LocalREPL recebia tudo no construtor

EDITADO:  remove esses parâmetros do env_kwargs
          - custom_tools são gerenciados externamente pelo ToolRegistry
          - compaction é instância interna (self.compactor)
          - Adiciona tratamento multimodal: self._is_multimodal_content_list(prompt)
          - Adiciona sandbox auto-selection: se RLM_SANDBOX=1 e environment=="local",
            redireciona para "sandbox" (DockerREPL)
```

**Validação de `other_backends` alterada:**

```python
# ORIGINAL: exigia exactly 1 backend adicional
if len(other_backends) != 1:
    raise ValueError("We currently only support one additional backend!")

# EDITADO: suporta múltiplos backends (sem limite fixo)
if other_backend_kwargs is not None and len(other_backends) != len(other_backend_kwargs):
    raise ValueError(f"other_backends e other_backend_kwargs must have the same length.")
```

---

### 3b. `rlm/utils/prompts.py` — System Prompts

**3 novos system prompts adicionados:**

| Prompt | Propósito |
|---|---|
| `RLM_FORAGING_SYSTEM_PROMPT` | Modo exploração epistêmica — ativado quando o REPL falha repetidamente. Age como um cientista descobrindo leis de um sistema desconhecido. |
| `RLM_CODE_SYSTEM_PROMPT` | Modo análise de codebase — ativado automaticamente quando o contexto é um diretório. Inclui `list_files()`, `read_file()`, `search_code()`, `file_outline()`, `memory_batch_analyze()`. |
| `build_multimodal_user_prompt()` | Constrói mensagem de usuário para prompts com imagens/áudio (Vision/Audio — Phase 11.2). |

**Mudança em `build_rlm_system_prompt()`:**

```python
# ORIGINAL: formato complexo com {custom_tools_section} no template,
#           construía section de tools inline, retornava ["system", "user"]
from rlm.environments.base_env import format_tools_for_prompt
tools_formatted = format_tools_for_prompt(custom_tools)
custom_tools_section = f"\n6. Custom tools...\n{tools_formatted}"
final_system_prompt = system_prompt.format(custom_tools_section=...)
return [{"role": "system", ...}, {"role": "user", ...}]  # user = metadata

# EDITADO: simplificado — sem custom_tools_section, sem template format()
#          retorna ["system", "assistant"] (metadata vai como "assistant", não "user")
return [
    {"role": "system", "content": system_prompt},
    {"role": "assistant", "content": metadata_prompt},  # ← mudança: user→assistant
]
```

**`build_user_prompt()` — mudança no formatador:**

```python
# ORIGINAL: f-string direta com root_prompt no template
USER_PROMPT_WITH_ROOT = """...\"{root_prompt}\"....."""
prompt = USER_PROMPT_WITH_ROOT.format(root_prompt=root_prompt)

# EDITADO: função segura que serializa JSON para tipos não-string
def _format_user_prompt_with_root(root_prompt: str | list | dict) -> str:
    if not isinstance(root_prompt, str):
        prompt_str = json.dumps(root_prompt, ensure_ascii=False)  # multimodal seguro
    else:
        prompt_str = root_prompt
```

---

### 3c. `rlm/environments/base_env.py` — Redução de 384→182 linhas

**O que foi removido:**

O `base_env.py` original continha toda a infraestrutura de **custom tools**:
`ToolInfo`, `parse_tool_entry()`, `parse_custom_tools()`, `format_tools_for_prompt()`.
Na versão editada, essa infraestrutura foi **movida** para `rlm/tools/` e gerenciada
externamente, tornando o `base_env.py` mais limpo.

**O que permaneceu:** `BaseEnv`, `IsolatedEnv`, `NonIsolatedEnv`, `SupportsPersistence` protocol.

---

### 3d. `rlm/environments/local_repl.py` — Adição de 168 linhas

**O que foi adicionado:**

1. **Codebase tools injection** — As ferramentas de `rlm/tools/codebase.py` são injetadas
   automaticamente no REPL quando o contexto é um diretório:
   ```python
   if os.path.isdir(context_payload):
       from rlm.tools.codebase import inject_codebase_tools
       inject_codebase_tools(self._globals, context_payload)
   ```

2. **Memory tools injection** — `memory_store`, `memory_read`, `memory_analyze`, etc.
   são injetados via `rlm/tools/memory.py`:
   ```python
   from rlm.tools.memory import inject_memory_tools
   inject_memory_tools(self._globals, session_id=self._session_id)
   ```

3. **Foraging mode reset** — `reset_foraging()` injeta função para sair do modo foraging:
   ```python
   self._globals["reset_foraging"] = lambda: setattr(self, "_foraging_mode", False)
   ```

4. **Event bus integration** — `self._event_bus.emit()` chamado a cada iteração de execução,
   para o WebSocket server poder fazer streaming em tempo real.

---

### 3e. `pyproject.toml`

| Campo | Original | Editado |
|---|---|---|
| `name` | `rlms` | `rlm` |
| `version` | `0.1.1` | `0.1.0` |
| `license` | `"MIT"` (campo formal) | Campo removido |
| `classifiers` | 8 classifiers | Removidos |
| `[project.urls]` | Homepage + Repository + Issues | Removidos |
| `[project.scripts]` | Não existia | `rlm = "rlm.cli.main:main"` adicionado |
| `[optional-dependencies]` | modal, e2b, daytona, prime | modal, daytona, prime (e2b removido) |

---

### 3f. Outros arquivos com mudanças menores

**`rlm/clients/openai.py`** (-41 linhas):
- Removida lógica de rastreamento de custo (`_accumulate_cost()`)
- Removida classe `OpenAICostTracker`
- Simplificada a criação de client (sem async client separado)

**`rlm/logger/rlm_logger.py`** (-28 linhas):
- Removidos métodos de log de budget usage
- Simplificado `log_result()` (sem campos de custo USD)

**`rlm/core/lm_handler.py`** (-22 linhas):
- Removida propagação de custo para o `RLM` pai
- Simplificada inicialização de clients

---

## 4. Módulos Novos — O que foi Construído do Zero

Esta seção documenta os 55 arquivos novos criados, agrupados por camada.

---

### Camada 1: Execution Control

#### `core/supervisor.py` — Supervisor de execução

```
Problema resolvido: o RLM entrava em loop infinito de socket_request errors
(documentado em testedeexecução.md) sem nenhum mecanismo de parada.

Solução: envelope ao redor de RLM.completion() com 4 mecanismos independentes:
  1. Timeout via threading.Timer — mata a thread após max_execution_time segundos
  2. Abort externo — DELETE /sessions/{id} seta um threading.Event que o loop verifica
  3. Error loop detection — se o mesmo padrão de erro aparece N+ vezes consecutivas, para
  4. Double-execution guard — if is_running(session_id): return error (sem fila, sem race)

Resultado (ExecutionResult.status):
  completed | timeout | aborted | error_loop | error
```

#### `core/session.py` — Gerenciador de sessões

```
Problema resolvido: múltiplos dispositivos/canais gerando sessões não isoladas.

Solução: RLMSession (dataclass) + SessionManager (SQLite):
  - Cada client_id tem sua própria sessão com estado independente
  - thread-safe via threading.Lock()
  - SQLite com check_same_thread=False (multi-threaded)
  - Tabelas: sessions (10 colunas) + event_log (FK para sessions)
```

#### `core/loop_detector.py` — Detector de loops

```
Detecta repetição de padrões no stdout do REPL usando n-gram hashing.
Evita que o agente execute o mesmo código N vezes sem progresso.
Algoritmo: sliding window de hashes Jaccard over últimas K iterações.
```

#### `core/compaction.py` — Compactador de contexto

```
Problema: histórico de mensagens cresce ilimitado → overflow do context window do LLM.

Solução: monitora tokens usados (len(str(messages)) como proxy).
         Quando ultrapassa CompactionConfig.max_history_tokens:
           1. Resume as últimas N mensagens em uma síntese via llm_query
           2. Substitui as mensagens antigas pela síntese
           3. Mensagens recentes são preservadas integralmente
```

---

### Camada 2: Server/API

#### `server/api.py` — Gateway principal

```
FastAPI app com lifespan (startup/shutdown):
  - startup: inicializa SessionManager, RLMSupervisor, PluginLoader, EventRouter
  - shutdown: shutdown gracioso de todas as sessões ativas

Endpoints:
  POST   /webhook/{client_id}   — entry point principal de eventos
  GET    /sessions              — lista sessões (filtra por status)
  GET    /sessions/{id}         — detalhes de uma sessão (RLMSession completo)
  DELETE /sessions/{id}         — aborta execução em curso
  GET    /sessions/{id}/events  — log de eventos (SSE stream opcional)
  GET    /plugins               — lista plugins carregados
  GET    /routes                — lista event routes configuradas
  GET    /health                — health check com uptime e session count
```

#### `server/openai_compat.py` — Compatibilidade OpenAI

```
Permite usar o RLM como backend de qualquer aplicação que fala OpenAI API.
Endpoints:
  POST /v1/chat/completions    — recebe no formato OpenAI, executa via RLM
  GET  /v1/models              — retorna lista fake compatível com ChatGPT clients

Streaming: resposta via SSE em chunks de 20 chars (_iter_sse_chunks())
Auth: Authorization: Bearer igual ao OpenAI padrão
Mapeamento: campo "user" do request → client_id da sessão RLM
```

#### `server/ws_server.py` — WebSocket streaming

```
RLMEventBus: bus de eventos com buffer circular (max_history=500)
  - Agentes emitem eventos durante execução: on_iteration, on_code_exec, on_llm_call
  - Clientes WebSocket se conectam e recebem stream em tempo real

Auth: token via ?token= query param ou Authorization: Bearer
      validação antes do handshake HTTP (não após — economia de recursos)

Fallback SSE: SSEStream.stream() generator com keepalive 100ms
              para clientes que não suportam WebSocket
```

#### `server/webhook_dispatch.py` — Dispatcher de webhooks

```
Recebe eventos externos de qualquer source (IoT, automações, etc.)
_RateLimiter: sliding window por IP (60 req/min padrão)
_extract_token(): suporte a 3 métodos de auth:
  1. Header X-Hook-Token (preferido)
  2. Authorization: Bearer
  3. Path URL /api/hooks/{token} (deprecated — vaza em logs)
_validate_token(): hmac.compare_digest (timing-safe)

Rotas:
  POST /api/hooks/{token}
  POST /api/hooks/{token}/{client_id}
  GET  /api/hooks/info (sem auth — status público)
```

---

### Camada 3: Canais de Comunicação

#### `plugins/` + `server/*_gateway.py` — 8 canais

| Canal | Gateway | Plugin | Formato |
|---|---|---|---|
| Discord | `discord_gateway.py` | `plugins/discord.py` | Embeds, markdown Discord |
| Telegram | `telegram_gateway.py` | `plugins/telegram.py` | MarkdownV2, teclado inline |
| Slack | `slack_gateway.py` | `plugins/slack.py` | Block Kit JSON |
| WhatsApp | `whatsapp_gateway.py` | `plugins/whatsapp.py` | Meta Cloud API |
| WebChat | `server/webchat.py` | — | HTML+JS+SSE (serve o próprio frontend) |
| Webhook | `webhook_dispatch.py` | — | JSON genérico de qualquer source |
| OpenAI-compat | `openai_compat.py` | — | OpenAI API format |
| WebSocket | `ws_server.py` | — | JSON events via WS |

---

### Camada 4: Inteligência / Algoritmos

#### `core/mcts.py` — Monte Carlo Tree Search sobre o REPL

```
Em vez de executar um único caminho greedy (iteração 0→1→2→...→resposta),
o MCTSOrchestrator gera N branches independentes em paralelo até profundidade D.

Componentes:
  SandboxREPL: clone isolado do LocalREPL em tmpdir separado por branch
  default_score_fn: pontua cada branch pelo output (+execução sem erro, +stdout, -Traceback)
  generate_branch_variants: gera variações do prompt inicial para cada branch

Custo conservador: branches=3, max_depth=2 → máximo 7 chamadas ao LLM
                   (vs 1 chamada de RLM com max_iterations=7)
Ativação: RLM.completion(..., mcts_branches=3)

Poda agressiva: branches com score=0 no primeiro passo são descartados imediatamente.
```

#### `core/memory_manager.py` — Memória persistente com grafos

```
Backend: SQLite (tables: memories, embeddings, links)

Operações de escrita:
  memory_store(key, content)              — armazena texto exato
  memory_analyze(key, analysis, source)   — armazena análise LLM + link ao source
  memory_link(from_key, relation, to_key) — cria relação semântica explícita

Operações de busca:
  memory_read(key)                         — recupera por key exata
  memory_semantic_search(query, top_k=5)   — busca por similaridade coseno
  memory_list(prefix)                      — lista keys por prefixo

Operações em batch (paralelas):
  memory_batch_analyze(items, template)    — analisa N items via sub-LMs concorrentes
  memory_batch_chunk_and_store(files)      — chunking de N arquivos de uma vez
  memory_reassemble(prefix)               — reconstrói arquivo dos chunks

Segurança: _sanitize_memory_chunk() verifica injeção de prompt em READ TIME
           (não modifica o que está armazenado — proteção na entrega)
```

#### `core/security.py` — Segurança de execução

```
3 componentes independentes:

InputThreatReport: analisa texto de entrada com 21 padrões regex compilados
  Níveis: clean | low | medium | high
  Exemplos high: "ignore all previous instructions", "you are now DAN"
  Ação: REPL recusa execução de código se threat_level=="high"

EnvVarShield: intercepta acesso a os.environ no REPL
  Redacta variáveis contendo KEY, TOKEN, SECRET, PASSWORD
  Resultado: o agente nunca "vê" OPENAI_API_KEY mesmo que tente print(os.environ)

REPLAuditor (AST-based): walk da AST Python antes de executar código
  Bloqueia: os.system, os.popen, subprocess.*, shutil.rmtree, __import__
  Diferencial vs regex: não pode ser contornado com getattr(os, "sys"+"tem")
  check_path_access(): bloqueia ~/.ssh, ~/.aws, C:\Windows\System32
```

---

### Camada 5: CLI

#### `cli/main.py` + `cli/service.py` + `cli/wizard.py`

```
Entry point registrado no pyproject.toml:
  [project.scripts]
  rlm = "rlm.cli.main:main"

Comandos disponíveis:
  rlm init              — wizard interativo de configuração (.env)
  rlm start             — inicia o daemon servidor (uvicorn)
  rlm stop              — para o daemon
  rlm status            — mostra sessões ativas, uptime, canais
  rlm client add <id>   — cria novo client token
  rlm client list       — lista clients registrados
  rlm doctor            — verifica configuração, dependências, conexão LLM

Interface: rich (tabelas, progress bars, prompts coloridos)
```

---

### Camada 6: Ferramentas REPL

#### `tools/codebase.py` — Análise de codebases

```
Ferramentas injetadas no REPL quando contexto é um diretório:
  list_files(dir, extensions, max_depth)     — lista arquivos
  read_file(path, start_line, end_line)      — lê com números de linha
  search_code(pattern, dir, extensions)      — grep no codebase (regex)
  file_outline(path)                         — extrai defs/classes (language-aware)
  file_stats(dir)                            — estatísticas (contagens, linguagens)
  directory_tree(dir, max_depth)             — árvore ASCII

Linguagens suportadas para outline: Python, TypeScript, JavaScript, Go, Rust
```

#### `tools/embeddings.py` + `tools/memory.py` + `tools/memory_tools.py`

```
Interface REPL para o MemoryManager:
  memory_store(key, content)
  memory_read(key)
  memory_analyze(key, analysis, source_ref)
  memory_link(from_key, relation, to_key)
  memory_list(prefix)
  memory_status()
  memory_chunk_and_store(path, prefix, chunk_lines=200)
  memory_reassemble(prefix)
  memory_batch_analyze(items, prompt_template)
  memory_batch_chunk_and_store(files, chunk_lines)
  memory_semantic_search(query, top_k=5)
```

---

## 5. Linha do Tempo das Evoluções

As evoluções foram numeradas no código via comentários como `# Evolution N` ou `# Phase N`.

| Fase | Nome | O que criou |
|---|---|---|
| **Base** | RLM Core (original) | `rlm.py`, `lm_handler.py`, `local_repl.py`, `comms_utils.py` |
| **Fase 1** | CLI básica | `cli/main.py`, `cli/service.py` |
| **Fase 2** | SessionManager | `core/session.py` |
| **Fase 3** | Server básico | `server/api.py` (v1) |
| **Fase 4** | Webhook + Auth | `server/webhook_dispatch.py`, `_RateLimiter`, `hmac.compare_digest` |
| **Fase 5** | Event Bus | `server/ws_server.py`, `RLMEventBus` |
| **Fase 6** | Supervisor | `core/supervisor.py` (`timeout`, `abort`, `error_loop`) — resolve bug de loop infinito |
| **Fase 6.1** | Foraging Mode | `RLM_FORAGING_SYSTEM_PROMPT` em `utils/prompts.py` |
| **Fase 6.3** | MCTS | `core/mcts.py`, `SandboxREPL`, `MCTSOrchestrator` |
| **Fase 7** | Canais | `plugins/discord.py`, `plugins/telegram.py`, `plugins/slack.py`, `plugins/whatsapp.py` |
| **Fase 7.4** | API rewrite | `server/api.py` (reescrita com PluginLoader, EventRouter) |
| **Fase 8** | Advanced Infra | `core/hooks.py`, `core/compaction.py`, `core/loop_detector.py` |
| **Fase 9** | Memory + Security | `core/memory_manager.py`, `core/security.py`, `tools/memory*.py` |
| **Fase 9.3** | Memory guardrails | `_sanitize_memory_chunk()` em memory_manager |
| **Fase 10** | Codebase Mode | `tools/codebase.py`, `RLM_CODE_SYSTEM_PROMPT`, auto-detect diretório |
| **Fase 11** | Sub-RLM factory | `core/sub_rlm.py`, `make_sub_rlm_fn()` |
| **Fase 11.2** | Vision/Audio | `build_multimodal_user_prompt()`, `_is_multimodal_content_list()` |
| **Otimização** | Backend Python otimizado | `core/fast.py`, `core/optimized.py` |

---

## 6. Arquitetura: Antes vs Depois

### Original — Biblioteca de uso direto

```
Usuário
  └── Python script / Jupyter notebook
      └── RLM(backend="openai", environment="local")
          └── completion("minha pergunta")
              ├── LMHandler (OpenAI client)
              └── LocalREPL (subprocess Python)
                  ├── llm_query()
                  └── rlm_query() → RLM recursivo
```

### Editado — Sistema daemon multi-dispositivo

```
Dispositivos externos
  ├── Discord Bot        ─┐
  ├── Telegram Bot       ─┤
  ├── Slack App          ─┤  HTTP/WebSocket
  ├── WhatsApp           ─┤
  ├── ESP32 Webhook      ─┤
  ├── Celular (WebChat)  ─┤
  └── Qualquer cliente   ─┘
       OpenAI-compat API ─┘

           ↓ POST /webhook/{client_id}
           ↓ POST /v1/chat/completions
           ↓ WS  /ws

  ┌─────────────────────────────────┐
  │  FastAPI Server (uvicorn)        │
  │  ├── EventRouter                 │
  │  ├── PluginLoader                │
  │  └── Endpoints REST              │
  └──────────────┬──────────────────┘
                 │
  ┌──────────────▼──────────────────┐
  │  RLMSupervisor                   │
  │  (timeout, abort, error_loop)    │
  └──────────────┬──────────────────┘
                 │
  ┌──────────────▼──────────────────┐
  │  SessionManager (SQLite)         │
  │  client_id → RLMSession          │
  └──────────────┬──────────────────┘
                 │
  ┌──────────────▼──────────────────┐
  │  RLM.completion()                │
  │  ├── HookSystem                  │
  │  ├── ContextCompactor            │
  │  ├── LoopDetector                │
  │  ├── MCTSOrchestrator (opt.)     │
  │  └── LMHandler                   │
  └──────────────┬──────────────────┘
                 │
  ┌──────────────▼──────────────────┐
  │  LocalREPL (subprocess Python)   │
  │  ├── codebase tools             │
  │  ├── memory tools (SQLite)      │
  │  ├── llm_query / rlm_query      │
  │  ├── browser tools (opt.)       │
  │  └── custom skills (opt.)       │
  └─────────────────────────────────┘
```

---

## 7. Decisões de Projeto Documentadas

### 7a. Por que remover max_budget / max_timeout / max_tokens?

No original, esses limites eram lançados como exceções Python:
`BudgetExceededError`, `TimeoutExceededError`, `TokenLimitExceededError`.

Na versão daemon, exceções propagadas de dentro do REPL thread podem deixar
sessões em estado inconsistente (thread morta, SQLite com sessão em status "running").
A solução foi centralizar toda lógica de limite no `RLMSupervisor`, que monitora
de fora via `threading.Timer` e `threading.Event`, e grava o resultado atômico
(`completed|timeout|error_loop`) no SQLite antes de liberar a sessão.

### 7b. Por que "papel do role" mudou de "user" para "assistant" no metadata_prompt?

```python
# Original: metadata (comprimento do contexto) era mensagem de "user"
return [{"role": "system", ...}, {"role": "user", "content": metadata_prompt}]

# Editado: metadata é mensagem de "assistant"
return [{"role": "system", ...}, {"role": "assistant", "content": metadata_prompt}]
```

O motivo: em multi-turn conversations (modo `persistent=True`), o loop do RLM
replica a última mensagem no histórico como "user" para o próximo turno. Se a
mensagem de metadata fosse "user", seria interpretada como a query do usuário
na segunda iteração, confundindo o agente.

### 7c. Por que o custom_tools saiu do construtor do RLM?

No original, `custom_tools` eram passados no `__init__` e injetados no REPL
pelo `LocalREPL` em cada `completion()`. Isso tornava difícil:
- Adicionar ferramentas dinamicamente (ex: skills carregadas por arquivo)
- Revogar ferramentas específicas (sem recriar o RLM inteiro)

Na versão editada, as ferramentas são injetadas no namespace do REPL no momento
do `setup()` pelo próprio environment, gerenciadas pelo `SkillLoader` e `ToolRegistry`
externos. O RLM não precisa mais saber quais ferramentas existem.

### 7d. Por que `find_code_blocks()` foi movido de `utils/parsing.py` para `core/fast.py`?

Profiling mostrou que o parsing de blocos de código é chamado centenas de vezes
por sessão (uma vez por resposta do LLM). O módulo `core/fast.py` agora prioriza
um backend Python otimizado com regex compilado, framing endurecido e helpers de
hash/formatação mantidos no próprio runtime Python.

### 7e. Por que o nome do pacote mudou de `rlms` para `rlm`?

`rlms` no PyPI pertence ao projeto original de Alex Zhang. Para evitar conflito
de namespace e distinguir o fork como um produto diferente, o pacote foi renomeado.
O entry point CLI `rlm` foi adicionado intencionalmente simples: `$ rlm start`.

### 7f. Por que E2B foi removido?

E2B (e2b-code-interpreter) é uma sandbox remota paga. Os casos de uso do projeto
são locais/domésticos (raspberry pi, PC, servidor privado). Docker local é gratuito
e oferece isolamento equivalente. O `SandboxREPL` do MCTS fornece isolamento em
tmpdir sem custo adicional para os casos que precisam de branch isolation.
