# Analise Real do Sistema RLM

Este documento registra uma leitura tecnica do RLM baseada no codigo em execucao, nao apenas na documentacao de intencao. O objetivo e deixar claro como o sistema realmente opera hoje, onde ele ja esta maduro, onde esta apenas parcialmente consolidado e onde ainda ha lacunas entre a ambicao arquitetural e o comportamento observavel do runtime.

## 1. Tese central

O RLM nao e, na pratica, um chatbot com historico persistente e algumas ferramentas acopladas. Ele e uma engine recursiva de execucao sobre um ambiente Python persistente. O fluxo principal do sistema assume que o modelo vai:

- receber um prompt inicial
- responder com texto e codigo
- executar esse codigo no REPL persistente
- transformar estado computacional em contexto para a iteracao seguinte
- abrir subagentes completos quando necessario
- finalizar com uma resposta derivada do estado do ambiente

Isso significa que o centro real do sistema nao e a conversa. O centro real e o ambiente de execucao.

Consequencia pratica:

- o historico textual importa, mas nao e a estrutura primaria de trabalho
- a memoria operacional mais importante e o namespace persistente do REPL
- a recursao via subagentes nao e um adorno; ela e parte da arquitetura central
- a qualidade do sistema depende mais da disciplina do runtime do que da interface conversacional

## 2. Caminho real de execucao

## 2.1 Entrada pelo servidor

O fluxo HTTP principal passa por [rlm/server/api.py](../rlm/server/api.py).

Em termos reais, a API faz o seguinte:

1. resolve autenticacao e contexto da requisicao
2. recupera ou cria sessao via `SessionManager`
3. injeta helpers, skills, funcoes SIF, handoff e controles no REPL da sessao
4. executa o trabalho principal via supervisor
5. depois da resposta principal, roda a orquestracao de papeis se houver handoff pendente ou auto-avaliacao necessaria
6. persiste status e eventos da sessao

O ponto importante aqui e que o fluxo de handoff ja nao e apenas registro. Ele entra no caminho real da resposta, apos a completion principal.

## 2.2 Sessao e ativacao

O `SessionManager` em [rlm/core/session.py](../rlm/core/session.py) faz o papel de pool de sessoes persistidas em SQLite.

Ele realmente faz quatro coisas:

- mapeia cliente para sessao reaproveitavel
- persiste metadata e event log em banco
- instancia `RLMSession` por sessao ativa
- salva e restaura estado do RLM quando fecha ou reabre a sessao

Em termos praticos, o sistema possui sessao de verdade. Nao e apenas um objeto temporario de request.

## 2.3 Supervisao de execucao

O `RLMSupervisor` em [rlm/core/supervisor.py](../rlm/core/supervisor.py) envolve a chamada principal com:

- timeout por timer
- abort externo por evento legado
- cancelamento composicional por `CancellationToken`
- controle de estado running, idle, error

Isso significa que a completion principal nao roda solta. Ela ja esta sob um envelope de supervisao. O limite aqui e que o cancelamento ainda e cooperativo: ele pede parada, nao mata computacao arbitraria de forma preemptiva.

## 2.4 Sessao conversacional

O `RLMSession` em [rlm/session.py](../rlm/session.py) encapsula um `RLM` persistente e adiciona uma camada conversacional com:

- janela quente de turnos recentes
- resumo compactado de turnos antigos
- memoria de longo prazo opcional por sessao
- compactacao em background

O papel real do `RLMSession` nao e substituir o RLM, mas embrulhar o RLM em um contexto multi-turno mais economico.

## 2.5 Loop principal do RLM

O centro da maquina esta em [rlm/core/rlm.py](../rlm/core/rlm.py).

O fluxo efetivo da `completion()` e este:

1. se `depth >= max_depth`, faz fallback e nao continua a recursao
2. cria `LMHandler` e ambiente de execucao por `_spawn_completion_context()`
3. reaproveita `LocalREPL` se a instancia for persistente
4. injeta `sub_rlm`, `sub_rlm_parallel`, `sub_rlm_async`, aliases e browser globals no ambiente
5. monta `message_history` com system prompt e metadados
6. itera ate `max_iterations`
7. em cada iteracao:
   - registra evento de inicio
   - verifica cancelamento por token, ambiente e abort event
   - dispara compaction se necessario
   - monta prompt corrente
   - chama `_completion_turn()`
   - extrai `FINAL` ou `FINAL_VAR`
   - registra timeline e eventos de thought, repl e final answer
8. ao finalizar, persiste historico no ambiente persistente
9. devolve `RLMChatCompletion`

Essa estrutura esta madura e ja incorpora varias camadas de evolucao operacional.

## 2.6 Um turno de completion

O `_completion_turn()` ainda em [rlm/core/rlm.py](../rlm/core/rlm.py) faz a ponte entre inferencia e execucao:

- manda o prompt ao modelo via `LMHandler`
- extrai blocos de codigo
- executa cada bloco no ambiente
- registra eventos do runtime
- monta um `RLMIteration`

O ponto central aqui e que a resposta do modelo vira acao computacional. O sistema nao trata o modelo como resposta final por padrao; trata como gerador de proximos passos de execucao.

## 3. Como o ambiente realmente funciona

## 3.1 O LocalREPL e o centro operacional

O `LocalREPL` em [rlm/environments/local_repl.py](../rlm/environments/local_repl.py) e o componente mais importante depois do loop principal.

Ele oferece:

- namespace persistente entre iteracoes
- aliases de contexto e historico
- auditoria AST antes da execucao
- guards de runtime para import e acesso a arquivos
- restauracao de scaffold apos execucao
- integracao com ledger, anexos, timeline e digest de coordenacao

Essa combinacao faz do REPL a memoria operacional primaria do sistema.

## 3.2 Scaffold e restauracao

Um ponto importante confirmado no codigo e que `_restore_scaffold()` existe de verdade e e chamado apos execucao.

Isso protege o ambiente contra corrompimento acidental ou deliberado por codigo gerado pelo modelo, restaurando nomes essenciais como:

- `FINAL_VAR`
- `SHOW_VARS`
- `llm_query`
- `llm_query_batched`
- funcoes injetadas por `rlm.py`
- runtime tools do workbench

Esse detalhe importa porque sem ele o REPL persistente seria muito mais fragil do que a documentacao sugere.

## 3.3 Runtime workbench no ambiente

O `LocalREPL` hoje ja carrega no caminho quente:

- `TaskLedger`
- `ContextAttachmentStore`
- `ExecutionTimeline`
- `CoordinationDigest`

Isso significa que o runtime workbench ja nao e uma ideia externa ao sistema. Ele ja virou parte do ambiente persistente real.

## 3.4 Checkpoints

O RLM e o `LocalREPL` suportam persistencia de estado via checkpoint.

Na pratica, o que ja entra na restauracao e:

- contexto e historico
- locals serializaveis
- task ledger
- anexos
- timeline
- digest de coordenacao

Isso coloca o runtime num nivel acima de um simples executor de prompt: ele consegue continuar trabalho com mapa operacional preservado.

## 4. Recursao e subagentes

## 4.1 Subagente serial

`sub_rlm()` em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py) cria uma instancia filha completa com:

- depth incrementado
- timeout proprio
- namespace isolado
- backend herdado por padrao
- task_id vinculado ao pai quando disponivel
- integracao com timeline do pai

Esse caminho esta maduro e faz parte do que o sistema faz melhor.

## 4.2 Subagente async

`sub_rlm_async()` cria filhos em background e devolve `AsyncHandle`.

O que ele ja faz de verdade:

- cria branch_id monotono
- permite `log_poll()` e `result()`
- conecta filho ao barramento compartilhado
- registra task e eventos no pai

O limite real aqui e que o modelo de cancelamento e cooperativo e o valor do async depende do filho respeitar os sinais que recebe.

## 4.3 Paralelismo

`sub_rlm_parallel()` e `sub_rlm_parallel_detailed()` em [rlm/core/sub_rlm.py](../rlm/core/sub_rlm.py) sao a parte mais sofisticada do sistema hoje depois do loop principal.

Eles ja fazem de verdade:

- criacao de batch pai como task raiz operacional
- criacao de branch filhas com `parent_task_id`
- observacao do `SiblingBus`
- politica `stop_on_solution`
- cancelamento de branches redundantes
- resumo de chamada paralela no modo detailed

Isso significa que o paralelismo deixou de ser apenas N threads independentes. Ele virou uma estrutura com alguma governanca real.

## 4.4 Coordenacao entre irmaos

O `SiblingBus` em [rlm/core/sibling_bus.py](../rlm/core/sibling_bus.py) ja suporta:

- topicos FIFO
- canais de controle por geracao
- telemetria
- observadores
- semantic_type em mensagens
- sinais como `solution_found`, `stop`, `switch_strategy` e `consensus_reached`

Mas ha um ponto importante: a semantica completa ainda nao esta toda convertida em politica de execucao. `solution_found` ja governa comportamento real. Os outros sinais ainda sao mais infraestrutura do que automacao madura.

## 5. Handoff, avaliacao e papeis

## 5.1 Handoff nao e mais so log

O `request_handoff()` em [rlm/core/handoff.py](../rlm/core/handoff.py) gera `HandoffRecord` e injeta o payload no estado da sessao.

O passo decisivo e que a API em [rlm/server/api.py](../rlm/server/api.py) chama `orchestrate_roles()` ao final da execucao principal. Logo, o handoff entra no fluxo real da resposta.

## 5.2 Orquestracao real, mas enxuta

O `role_orchestrator` em [rlm/core/role_orchestrator.py](../rlm/core/role_orchestrator.py) ja faz de verdade:

- consumir handoffs pendentes
- executar worker ou micro via `sub_rlm`
- rodar evaluator
- disparar retry quando a avaliacao pedir
- escalonar para humano quando necessario

O que isso mostra:

- a especializacao por papel existe no runtime
- mas o contrato ainda e enxuto e pouco governado
- nao virou ainda um sistema rico de workers com objetivos, budgets e schemas obrigatorios

## 5.3 Task tree no handoff

Depois das melhorias recentes, handoff e retry do evaluator reaproveitam `task_id` quando ele ja existe e continuam a arvore operacional, em vez de abrir execucoes derivadas opacas.

Isso foi um passo importante porque tira o fluxo de papeis do campo apenas narrativo e o prende ao ledger do runtime.

## 6. Sessao, memoria e fronteiras de estado

## 6.1 Sessao operacional

Hoje existem pelo menos tres camadas reais convivendo:

- sessao persistente no servidor
- sessao conversacional do `RLMSession`
- ambiente persistente do `RLM`

Isso da poder, mas tambem cria acoplamento. O sistema funciona porque essas camadas se encaixam razoavelmente bem, nao porque estejam conceitualmente separadas de forma perfeita.

## 6.2 Memoria de longo prazo

O `RLMSession` tenta carregar `MultiVectorMemory` e consulta essa memoria antes da janela quente ao montar o prompt.

Isso significa que memoria de longo prazo existe de verdade no fluxo conversacional.

Mas a separacao entre:

- memoria persistente
- memoria de trabalho
- anexos pinados
- artefatos computacionais

ainda nao esta formalizada como arquitetura de contexto tipada. Essa continua sendo uma das maiores lacunas reais do sistema.

## 6.3 Persistencia e fechamento

O sistema sabe salvar estado e fechar sessao formalmente. O problema nao e ausencia de mecanismos de fechamento.

O problema real e que nao aparece, no caminho principal, uma politica automatica clara de expurgo de sessao ociosa. O `close_all()` existe, mas isso e fechamento de shutdown, nao governanca continua de lifecycle.

## 7. O que esta maduro

As partes mais maduras hoje sao:

- loop principal do `RLM`
- `LocalREPL` como ambiente persistente
- seguranca basica de execucao
- subagente serial
- sessao conversacional com compaction
- workbench de runtime no backend
- coordenacao paralela minima com `stop_on_solution`
- handoff com execucao real apos a completion principal

Essas areas ja formam um sistema tecnicamente serio.

## 8. O que esta funcional, mas ainda incompleto

As partes que ja rodam, mas ainda nao fecharam seu proprio contrato arquitetural, sao:

- paralelismo com coordenacao semantica mais rica
- artefatos de subagentes como ativos operacionais governados
- role orchestration com contrato forte
- separacao formal das camadas de contexto
- cockpit operacional acima do endpoint de runtime

Aqui o sistema ja possui infraestrutura, mas ainda nao possui fechamento de produto e de governanca.

## 9. O que ainda esta mais no plano do que no sistema

Ainda nao esta consolidado no runtime como comportamento completo:

- `task_context`, `workspace_context`, `session_context` e `memory_context` como objetos formais
- promocao de artefatos para ferramentas de sessao
- compaction tipada por camada de contexto
- cockpit visual de coding workspace
- politicas automaticas completas para `switch_strategy` e `consensus_reached`
- supervisao por custo, profundidade, redundancia e health de branch

Esses itens nao sao inexistentes como direcao. Eles apenas ainda nao estao completos como sistema operacional real.

## 10. Gaps arquiteturais reais

Os principais gaps reais hoje sao estes:

### 10.1 Separacao insuficiente de contextos

O sistema ja tem anexos, memoria e historico, mas ainda nao tem camadas formais de contexto. Essa e provavelmente a maior lacuna estrutural restante.

### 10.2 Governanca de artefatos fraca

O sistema ja devolve artefatos, mas ainda nao governa promocao, binding, versionamento e reutilizacao como politica de sessao.

### 10.3 Cockpit ausente

O backend ja produz snapshot operacional. A superficie de produto para operador ainda nao existe.

### 10.4 Cancelamento cooperativo

O sistema ja cancela melhor do que antes, mas a semantica predominante continua sendo cooperativa. Isso e suficiente para varias tarefas, mas nao equivale a controle total de execucao concorrente.

### 10.5 Lifecycle de sessao pouco governado

As sessoes existem, persistem e fecham, mas o ciclo de vida continuo ainda nao parece ter politica automatica forte de expiracao ou reciclagem.

## 11. Conclusao

O estado real do RLM hoje pode ser resumido assim:

- o nucleo recursivo esta consolidado
- o ambiente persistente esta consolidado
- a governanca operacional esta em fase intermediaria
- a camada de produto esta atras do backend

Em outras palavras:

- o cerebro computacional do sistema ja e forte
- o sistema nervoso ja existe em parte relevante
- a pele do produto ainda nao acompanha a sofisticacao do runtime

Se a pergunta for "como o RLM realmente esta hoje", a resposta correta e:

- ele ja e um runtime recursivo serio e funcional
- ele ja nao e apenas uma ideia promissora
- mas ele ainda nao concluiu a disciplina de contexto, a governanca de artefatos e o cockpit operacional que a propria arquitetura passou a exigir
