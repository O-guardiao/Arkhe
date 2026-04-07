# Plano Corrigido: Recursao Python -> Superficies TypeScript

# ajuda com contexto e intoduções: 

C:\Users\demet\AppData\Roaming\Code\User\workspaceStorage\9193fd705184cfa50a8a4fbc0f7bf984\GitHub.copilot-chat\memory-tool\memories\Y2JjOWUxZjEtMGIzZi00MmQ4LWI3ZjktYzM4OGJmMzZlODNl\rlm-analysis-focused-python-repl-recursion.md

C:\Users\demet\AppData\Roaming\Code\User\workspaceStorage\9193fd705184cfa50a8a4fbc0f7bf984\GitHub.copilot-chat\memory-tool\memories\Y2JjOWUxZjEtMGIzZi00MmQ4LWI3ZjktYzM4OGJmMzZlODNl\arkhe-recursion-data-flow.md

C:\Users\demet\AppData\Roaming\Code\User\workspaceStorage\9193fd705184cfa50a8a4fbc0f7bf984\GitHub.copilot-chat\memory-tool\memories\Y2JjOWUxZjEtMGIzZi00MmQ4LWI3ZjktYzM4OGJmMzZlODNl\python-typescript-boundary-complete-analysis.md 

C:\Users\demet\AppData\Roaming\Code\User\workspaceStorage\9193fd705184cfa50a8a4fbc0f7bf984\GitHub.copilot-chat\chat-session-resources\cbc9e1f1-0b3f-42d8-b7f9-c388bf36e83e\call_0GPcWteKgO8j5vc6kaTpl84n__vscode-1775575540812\content.txt


> Tese corrigida: o produto continua sendo o runtime de recursao em Python.
> O centro do sistema esta em rlm/environments e rlm/core. TypeScript foi
> introduzido para gateway, CLI, server e multichannel, ou seja, para a camada
> de interacao humano-maquina. O problema principal nao e "a TUI nao desenha a
> arvore direito". O problema principal e que a migracao nao definiu um
> contrato explicito entre o estado rico da recursao em Python e as superficies
> TypeScript que observam e controlam esse runtime.

---

## 1. Correcao do Enquadramento

O documento anterior estava centrado no lugar errado.

Erros do plano anterior:

1. Tratou a TUI como se fosse o fluxo principal do produto.
2. Tratou `BranchTaskBinding` como se fosse o modelo central da recursao.
3. Misturou problema semantico de fronteira com problema de layout e polling.
4. Pressupoz que melhorar branch tree era o primeiro objetivo, quando o primeiro
   objetivo real e exportar corretamente a semantica do runtime Python.
5. Pressupoz que WebSocket de mensagens e caminho natural para telemetria de
   recursao, quando hoje esse WS serve primariamente ao transporte multicanal.

Correcao conceitual:

- Python continua sendo a fonte de verdade do runtime.
- O REPL Python continua sendo gerado e injetado como antes.
- A recursao continua nascendo e executando dentro do ambiente Python.
- TypeScript nao e o dono da recursao; ele e consumidor de projecoes dela.
- Portanto, a primeira tarefa nao e "melhorar TUI"; e "criar um contrato de
  projecao da recursao Python para superficies externas".

---

## 2. Mapa de Contexto

### 2.1 Arquivos centrais do produto

| Arquivo | Papel real no sistema | Observacao |
|---|---|---|
| `rlm/environments/local_repl.py` | Gera o ambiente Python, injeta tools, mantem namespace persistente | Nucleo do produto |
| `rlm/environments/_runtime_state.py` | Ledgers, snapshot runtime, controles operacionais | Exporta estado consolidado |
| `rlm/environments/_repl_tools.py` | Fabrica as ferramentas injetadas no REPL | Mantem a semantica de interacao intra-runtime |
| `rlm/core/engine/sub_rlm.py` | Spawn de filhos, depth guard, timeout, propagacao de contexto | Mecanismo de recursao |
| `rlm/core/engine/_sub_rlm_helpers.py` | Registro e atualizacao de tarefas/branches | Ponte entre spawn e workbench |
| `rlm/core/engine/runtime_workbench.py` | `AgentContext`, `TaskLedger`, `RecursiveSessionLedger`, `CoordinationDigest` | Estado interno rico |
| `rlm/core/observability/operator_surface.py` | Construcao do payload de observabilidade | Fronteira de exportacao |
| `rlm/server/operator_bridge.py` | Endpoints HTTP de operador | Superficie de acesso ao runtime |

### 2.2 Arquivos TypeScript consumidores

| Arquivo | Papel real no sistema | Observacao |
|---|---|---|
| `packages/cli/src/tui/live-api.ts` | Cliente HTTP do payload Python | Consome `Record<string, unknown>` |
| `packages/cli/src/tui/app.ts` | Hidrata paineis a partir de `/activity` | Extrai apenas uma fatia do estado |
| `packages/cli/src/tui/branch-tree.ts` | Renderiza arvore visual | Ja aceita `parentId`, mas nao o recebe |
| `packages/server/src/index.ts` | Sobe/aguarda o brain Python | Nao modela recursao |
| `packages/server/src/app.ts` e `http-proxy.ts` | Proxy e roteamento HTTP | Superficie operacional |
| `packages/gateway/src/ws-bridge.ts` | Ponte de mensagens envelope | Nao e stream nativo da recursao |

### 2.3 Dependencias e acoplamentos relevantes

| Relacao | Implicacao |
|---|---|
| `sub_rlm.py` -> `local_repl.py` | Filho nasce como novo ambiente Python, nao como extensao da TUI |
| `local_repl.py` -> `_repl_tools.py` | Tudo que o LLM usa continua sendo injetado no REPL |
| `operator_surface.py` -> `_runtime_state.py` | O que TS ve depende da projecao, nao do estado interno bruto |
| `live-api.ts` -> `operator_bridge.py` | TS depende de contrato HTTP estavel |
| `app.ts` -> `branch-tree.ts` | TUI so consegue mostrar o que a projecao Python expuser |

### 2.4 Testes existentes e lacunas

| Arquivo | Cobertura atual |
|---|---|
| `packages/cli/src/tui/app.test.ts` | Hidrata payload Python no TUI, mas com schema frouxo |
| `packages/cli/tests/client.test.ts` | Cliente CLI, nao cobre semantica de recursao |
| `tests/test_live_riemann.py` | Evidencia spawn/finish em fluxo live |
| `tests/test_live_riemann_parallel.py` | Evidencia spawn/finish em fluxo paralelo |

Divida de teste atual:

- Nao existe suite dedicada para `CoordinationDigest`.
- Nao existe suite dedicada para `operator_surface.build_runtime_snapshot()`.
- Nao existe contrato tipado compartilhado Python -> TS para o payload de
  recursao.

---

## 3. Realidade Atual do Produto

### 3.1 O que continua sendo Python

Antes da migracao, o ambiente era gerado e tudo era injetado no REPL Python.
Isso continua verdadeiro.

O runtime ainda funciona assim:

1. `LocalREPL.__init__()` cria o namespace.
2. `LocalREPL.setup()` injeta ferramentas, builtins seguros, funcoes de task,
   timeline, recursao, `llm_query`, `FINAL`, `FINAL_VAR`, etc.
3. `load_context()` carrega payloads e alias como `context`.
4. `execute_code()` executa Python no namespace combinado.
5. `sub_rlm()` cria um novo RLM filho com novo `LocalREPL`, novo namespace,
   depth incrementado, e canais de coordenacao.

O que segue injetado no ambiente Python:

- `FINAL()` e `FINAL_VAR()`
- `llm_query()` e `llm_query_batched()`
- APIs de task, attachment, timeline e recursive session
- ferramentas de IPC como `parent_log()` e `check_cancel()`
- ferramentas especializadas como `critic_fuzz()` e `mcts_explore()`
- sandbox de builtins e `env_shield`
- custom tools fornecidas ao runtime

Conclusao: a recursao continua nascendo, vivendo e encerrando no runtime Python.

### 3.2 O que foi migrado para TypeScript

Foi migrado o que fica do lado de fora do runtime:

- gateway
- CLI
- TUI
- server/proxy
- multichannel

Esses componentes foram movidos para TypeScript para lidar com:

- entrada e saida humana
- transporte entre canais
- operacao do servidor
- UX do operador

Mas eles nao substituíram:

- REPL
- recursao
- ledgers internos
- memoria do agente
- estrategia de execucao
- ciclo LLM+ambiente

---

## 4. Onde o Plano Anterior Errou Tecnicamente

### 4.1 `BranchTaskBinding` nao e a recursao

`BranchTaskBinding` e apenas um binding de coordenacao dentro de
`CoordinationDigest`.

Ele serve para amarrar:

- branch id
- task id
- modo
- titulo
- estado resumido
- metadata generica

Ele nao carrega sozinho a semantica completa da recursao.

A recursao real esta distribuida entre:

- `AgentContext`
- `TaskLedger`
- `RecursiveSessionLedger`
- `CoordinationDigest`
- timeline de runtime
- controles operacionais
- dados do proprio `LocalREPL`

Logo, usar apenas `BranchTaskBinding` como contrato de exportacao e reduzir o
produto a um artefato auxiliar de coordenacao.

### 4.2 A TUI consome uma projecao fina demais

Hoje o fluxo e:

1. Python agrega um snapshot em `operator_surface.py`.
2. `live-api.ts` busca esse payload como `Record<string, unknown>`.
3. `app.ts` extrai apenas uma pequena fatia de `coordination.branch_tasks`.
4. `branch-tree.ts` recebe so `id`, `label`, `status` e opcionalmente
   `durationMs`.

O proprio `branch-tree.ts` ja suporta `parentId`, mas o dado nao chega.

Ou seja: o problema nao e o componente visual. O problema e a projecao anterior
ao componente.

### 4.3 Mensageria multicanal nao deve virar telemetria de recursao

O WS usado por gateway/multichannel serve ao transporte de envelopes e mensagens
 de canais. Isso nao o torna automaticamente o barramento correto para telemetria
estruturada de recursao.

Se a telemetria de recursao passar por esse caminho sem contrato proprio, o
sistema mistura duas preocupacoes diferentes:

- mensagem de produto para usuario/canal
- observabilidade e controle do runtime

### 4.4 ETag e layout vieram cedo demais

ETag, 304, layout adaptativo e refinamento da arvore sao otimizacoes de camada
externa. Elas so fazem sentido depois que a semantica correta do runtime Python
estiver sendo exportada.

---

## 5. Problema Real a Resolver

O problema real e a ausencia de um contrato explicito entre o estado interno da
recursao Python e as superficies TypeScript.

Esse problema aparece em quatro niveis.

### 5.1 Nivel 1: fonte de verdade dispersa

O estado da recursao esta distribuido em varios ledgers e objetos internos.
Nenhum deles, isoladamente, representa a recursao inteira.

### 5.2 Nivel 2: projecao de exportacao fraca

`build_runtime_snapshot()` e `build_activity_payload()` exportam um snapshot
util para observacao geral, mas fraco demais para representar a semantica da
recursao para consumidores externos.

### 5.3 Nivel 3: consumo TS sem schema

`live-api.ts` trabalha com `Record<string, unknown>`. Logo, o TypeScript nao
faz papel de contrato; apenas de parse ad hoc.

### 5.4 Nivel 4: superficies externas tentam inferir semantica

TUI, gateway e futuras interfaces acabam tendo que adivinhar:

- hierarquia
- papel do branch
- causa de falha
- duracao
- estado do operador
- relacao entre branch e tarefa

Esse e o sintoma central da migracao mal fechada.

---

## 6. Estado Interno Rico vs Estado Exportado

### 6.1 O que Python sabe internamente

O runtime Python conhece muito mais do que hoje cruza a fronteira:

- depth efetivo
- lineage de execucao
- status intermediario e final
- timeout e erro
- contexto de canal
- foco/pausa/prioridade operacional
- eventos de spawn e finish
- timeline e mensagens recursivas
- vinculacao entre estrategia ativa e branches

### 6.2 O que cruza hoje para TS

Cruza uma fatia estreita:

- `branch_id`
- `task_id`
- `mode`
- `title`
- `status`
- `metadata`
- timestamps genericos do binding
- alguns dados de summary paralelo

### 6.3 O que TS realmente usa hoje

No TUI atual, quase tudo isso e ignorado. Na pratica, a arvore consome:

- id
- label textual composta
- status

Isso confirma que a perda de semantica acontece antes da renderizacao.

---

## 7. Modelo Alvo Corrigido

O modelo alvo nao deve ser "mais campos enfiados no branch tree".
O modelo alvo deve separar tres camadas.

### 7.1 Camada A: Runtime interno Python

Permanece Python-first.

Objetivo:

- manter a recursao como responsabilidade do brain
- nao deslocar semantica do produto para TypeScript
- permitir que os ledgers internos continuem ricos e mutaveis

### 7.2 Camada B: Projecao de observabilidade/controle

Criar uma projecao explicita e versionada da recursao, derivada do estado
interno do runtime.

Essa projecao deve ser produzida no lado Python e ser a unica forma oficial de
consumo externo.

Proposta de objetos de projecao:

```python
@dataclass
class RecursionBranchView:
    branch_id: int
    parent_branch_id: int | None
    depth: int
    role: str
    mode: str
    task_id: int | None
    parent_task_id: int | None
    title: str
    status: str
    final_status: str | None
    channel: str | None
    spawned_at: str | None
    started_at: str | None
    completed_at: str | None
    duration_ms: float | None
    error_type: str | None
    error_message: str | None
    operator_paused: bool
    operator_focused: bool
    operator_priority: int | None
    metadata: dict[str, Any]


@dataclass
class RecursionSnapshotView:
    runtime_attached: bool
    active_branch_id: int | None
    focused_branch_id: int | None
    winner_branch_id: int | None
    branches: list[RecursionBranchView]
    summary: dict[str, Any]
    controls: dict[str, Any]
    events: list[dict[str, Any]]
```

Observacao importante:

- `BranchTaskBinding` pode continuar existindo internamente.
- Mas ele nao deve ser confundido com o contrato oficial do sistema.

### 7.3 Camada C: Consumidores TypeScript

TUI, CLI operator, gateway e outras superficies devem consumir a projecao acima,
nao o estado interno cru e nao um dicionario generico.

Proposta de tipo TS:

```ts
export interface RecursionBranchView {
  branch_id: number;
  parent_branch_id: number | null;
  depth: number;
  role: "root" | "child_serial" | "child_parallel" | string;
  mode: "serial" | "parallel" | string;
  task_id: number | null;
  parent_task_id: number | null;
  title: string;
  status: string;
  final_status?: string | null;
  channel?: string | null;
  spawned_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  error_type?: string | null;
  error_message?: string | null;
  operator_paused: boolean;
  operator_focused: boolean;
  operator_priority?: number | null;
  metadata: Record<string, unknown>;
}
```

---

## 8. Principios de Projeto

1. Python continua sendo a autoridade semantica da recursao.
2. TypeScript consome projecoes, nao reconstrui a semantica.
3. O contrato de observabilidade deve ser separado do contrato de mensagem.
4. `metadata` nao pode continuar sendo o deposito principal de campos cruciais.
5. Layout, cache e polling so entram depois que o contrato estiver correto.

---

## 9. Fases Corretas de Implementacao

### Fase 0 — Reposicionar o problema e mapear a fonte de verdade

Status: concluida neste documento.

Entregas:

- reconhecer Python como centro do produto
- reconhecer TS como superficie de interacao
- separar estado interno, projecao e consumo

### Fase 1 — Construir a projecao Python oficial da recursao

Objetivo: parar de expor apenas bindings internos e passar a expor uma view
explicita do runtime de recursao.

Arquivos principais:

- `rlm/core/observability/operator_surface.py`
- `rlm/environments/_runtime_state.py`
- `rlm/core/engine/runtime_workbench.py`
- `rlm/core/engine/sub_rlm.py`
- `rlm/core/engine/_sub_rlm_helpers.py`

Trabalho:

1. Criar builders de projecao como `build_recursion_snapshot()`.
2. Consolidar dados vindos de `AgentContext`, `TaskLedger`,
   `RecursiveSessionLedger`, `CoordinationDigest` e controles.
3. Tornar canonicos campos como:
   - `parent_branch_id`
   - `depth`
   - `role`
   - `spawned_at`, `started_at`, `completed_at`
   - `duration_ms`
   - `error_type`, `error_message`
   - estado/foco operacional por branch
4. Decidir explicitamente quais campos vivem no estado interno e quais sao
   apenas derivados na projecao.

Nota importante:

- enriquecer `BranchTaskBinding` pode ser necessario como apoio.
- mas o objetivo da fase nao e "turbiná-lo".
- o objetivo e criar uma projecao oficial do runtime.

### Fase 2 — Tipar o contrato no lado TypeScript

Objetivo: remover o consumo ad hoc de `Record<string, unknown>` para o bloco de
recursao.

Arquivos principais:

- `packages/cli/src/tui/live-api.ts`
- `packages/cli/src/tui/app.ts`
- arquivo novo compartilhado de tipos para operator payload

Trabalho:

1. Definir tipos TS para `OperatorActivityPayload` e `RecursionSnapshotView`.
2. Fazer `fetchActivity()` retornar tipo conhecido para a parte de runtime.
3. Parar de depender de `metadata["campo"]` como fonte principal.
4. Tornar a fronteira verificavel por testes.

### Fase 3 — Criar stream estruturado de observabilidade da recursao

Objetivo: separar telemetria/controle da recursao do fluxo de mensagens de
usuario.

Arquivos principais:

- `rlm/server/operator_bridge.py`
- mecanica de WS/event bus que ja alimenta superficies operacionais
- consumidores TS de operador/TUI

Eventos alvo:

- `operator.recursion.branch_registered`
- `operator.recursion.branch_started`
- `operator.recursion.branch_updated`
- `operator.recursion.branch_finished`
- `operator.recursion.controls_changed`
- `operator.recursion.summary_changed`

Decisao arquitetural:

- esses eventos nao devem depender do envelope multicanal de usuario.
- eles pertencem ao plano de observabilidade do runtime.

### Fase 4 — Atualizar consumidores TypeScript

Objetivo: fazer as superficies TS consumirem a projecao certa.

Arquivos principais:

- `packages/cli/src/tui/app.ts`
- `packages/cli/src/tui/branch-tree.ts`
- `packages/cli/src/tui/header-panel.ts`
- superficies operator/gateway que precisarem da mesma view

Trabalho:

1. `branch-tree.ts` passa a consumir `parent_branch_id`, `depth`,
   `duration_ms`, `error_message`, `operator_focused`.
2. `header-panel.ts` passa a mostrar dados canonicos do summary.
3. `events` deixam de ser logs opacos e passam a ter schema.
4. Outros consumidores podem reutilizar a mesma view sem duplicar inferencia.

### Fase 5 — Otimizacoes de superficie

Somente depois da Fase 1-4.

Entram aqui:

- ETag/304
- deltas
- layout adaptativo
- compressao de payload
- throttling e cache

Essas otimizacoes estao corretas, mas vieram cedo demais no plano anterior.

---

## 10. Mudancas Minimas Necessarias no Estado Interno

Embora o contrato oficial deva ser uma projecao, algumas mudancas internas sao
necessarias para nao depender de heuristica.

Campos que provavelmente precisam existir de forma canonica em algum ponto do
runtime, e nao apenas em metadata eventual:

- `parent_branch_id`
- `depth`
- `role`
- `spawned_at`
- `started_at`
- `completed_at`
- `duration_ms`
- `error_type`
- `error_message`

Esses campos podem viver em:

- `BranchTaskBinding`, se fizer sentido operacional
- ou em uma nova estrutura interna dedicada a observabilidade

O que nao pode continuar acontecendo e depender de:

- `metadata.child_depth`
- `metadata.elapsed_s`
- strings livres dispersas por eventos

---

## 11. Riscos Reais

| Risco | Impacto | Mitigacao |
|---|---|---|
| Poluir o core Python com necessidades especificas da TUI | Alto | Separar estado interno de projecao |
| Transformar `BranchTaskBinding` em dump de tudo | Alto | Criar view oficial e usar binding so onde fizer sentido |
| Misturar envelope multicanal com telemetria de runtime | Alto | Manter stream operacional separado |
| Criar tipos TS sem builder Python estavel | Medio | Primeiro estabilizar a projecao Python |
| Otimizar polling antes de corrigir semantica | Medio | Deixar performance para a fase final |

---

## 12. Criterio de Sucesso Corrigido

O plano sera considerado bem-sucedido quando:

1. O runtime Python continuar sendo claramente a fonte de verdade.
2. A recursao puder ser observada de fora sem inferencias ad hoc.
3. TypeScript consumir tipos explicitos, nao dicionarios genericos.
4. TUI deixar de reconstruir semantica e passar apenas a exibi-la.
5. Gateway, CLI e futuras superficies poderem reutilizar a mesma projecao.
6. Otimizacoes de polling/layout passarem a ser detalhe de UX, nao compensacao
   por contrato ausente.

---

## 13. O Que Este Plano Nao Faz

Este plano nao propoe:

- migrar a recursao para TypeScript
- deslocar a geracao do ambiente para fora do REPL Python
- usar a TUI como nova autoridade do sistema
- misturar observabilidade da recursao com mensagens normais de canal

Este plano propoe apenas fechar corretamente a fronteira entre:

- produto Python
- projecao de runtime
- superficies TypeScript

Essa e a correcao estrutural que faltava.
