# Runtime Workbench: Plano por Fases

Este documento transforma o item 5 da análise arquitetural em execução incremental dentro do RLM. O foco não é trocar a recursão por UX. O foco é tornar a recursão rastreável, recuperável, coordenável e controlável sem reduzir o papel central do REPL e dos subagentes.

Observabilidade aqui não significa apenas auditoria humana. Significa criar um tecido operacional que permita ao pai, aos filhos e aos irmãos paralelos saberem o que já foi tentado, o que foi produzido, qual branch está ativa e quando uma descoberta precisa ser propagada para evitar trabalho redundante.

## Estado atual

Implementado até agora:

- Ledger de tasks, anexos e timeline injetados no `LocalREPL` e persistidos em checkpoint.
- Endpoint `/sessions/{session_id}/runtime` com filtros de coordenação por operação, tópico e branch.
- Instrumentação do loop principal do `RLM` para início/fim de completion, iterações, compaction e recebimento de resposta do modelo.
- Registro formal de `task_id` para `sub_rlm`, `sub_rlm_async`, `sub_rlm_parallel` e `sub_rlm_parallel_detailed`.
- `SiblingBus` com sinais semânticos explícitos (`solution_found`, `stop`, `switch_strategy`, `consensus_reached`) e observadores para alimentar o digest do runtime.
- Política `stop_on_solution` funcional no paralelismo, com cancelamento de branches redundantes e retorno `[CANCELLED]` respeitado pelo loop principal.
- Batch paralelo promovido a nó pai na árvore operacional, com `parent_task_id` obrigatório para branches filhas.
- `sub_rlm_parallel_detailed` agora retorna resumo explícito com `winner_branch_id`, `cancelled_count`, `failed_count`, `task_ids_by_branch` e `batch_task_id`.
- `request_handoff` cria task filha no ledger e propaga `task_id` e `parent_task_id` no payload do handoff.
- Worker handoff e retry orientado por evaluator reutilizam o mesmo `task_id` para não deixar execução derivada fora da árvore operacional.

Pendências relevantes:

- Formalizar árvore operacional também para auto-avaliação sem handoff explícito, caso se queira rastrear evaluator automático como nó próprio.
- Definir políticas de promoção de anexos e artefatos para a camada de contexto operacional.
- Criar visualização externa da árvore de execução e do digest de coordenação em tempo real.
- Adicionar heurísticas de loop e redundância baseadas em timeline, não apenas em sinal de solução.

## Fase 1: Ledger, anexos e timeline

Objetivo: dar ao REPL persistente três primitivas explícitas de trabalho e coordenação.

- Task ledger: o agente declara o que está fazendo, qual tarefa está ativa e em que estado ela está.
- Context attachments: o agente anexa trechos de contexto, arquivos e payloads estruturados sem misturar tudo no histórico textual.
- Execution timeline: o runtime registra eventos manuais e automáticos para inspeção posterior e para consumo por outros agentes do mesmo fluxo recursivo.

Entregáveis:

- APIs injetadas no REPL: `task_*`, `attach_*`, `timeline_*`.
- Persistência no checkpoint do `LocalREPL`.
- Eventos automáticos para execução de código, `llm_query`, adição de contexto/histórico e finalização de iterações.
- Endpoint de inspeção por sessão: `/sessions/{session_id}/runtime`.
- Base operacional para replay parcial, handoff e sincronização entre pai, filhos e branches paralelas.

Critério de saída:

- Uma sessão persistente consegue ser interrompida e retomada com tasks, anexos e timeline intactos, sem perder o mapa interno da recursão.

## Fase 2: Contrato de subagentes

Objetivo: tirar a recursão do modo opaco sem burocratizar a execução.

- Registrar spawn, conclusão, erro e timeout de `sub_rlm`, `sub_rlm_parallel` e `sub_rlm_async`.
- Associar cada subagente a `branch_id`, profundidade, `task_id` e preview da tarefa.
- Permitir que o pai inspecione quais ramos produziram artefatos úteis versus quais só consumiram iterações.
- Dar aos ramos paralelos um contrato explícito de comunicação: o que foi publicado no barramento, o que virou sinal de controle e o que pode ser reaproveitado por irmãos.

Entregáveis:

- Eventos estruturados de subagente na timeline do pai.
- Resumo por chamada paralela: quantidade de branches, falhas e profundidade.
- Digest do `SiblingBus` e dos canais de controle como parte do estado da sessão.
- Base para UI de árvore de execução.

Critério de saída:

- O sistema consegue responder, por sessão, quem chamou quem, quais branches cooperaram, quantas falharam e em que ponto a coordenação interna mudou de direção.

## Fase 3: Separação de camadas de contexto

Objetivo: parar de tratar todo estado como histórico linear.

- Separar prompt ativo, histórico conversacional, anexos pinados, artefatos computacionais e memória terceirizada.
- Permitir promoção explícita de anexos para artefatos reutilizáveis.
- Fazer a compactação agir sobre a camada certa, não sobre tudo indiscriminadamente.

Entregáveis:

- Estrutura tipada de contexto operacional.
- Regras de compactação por camada.
- Pinos e promoções de anexos entre turnos.

Critério de saída:

- Compactar um turno não destrói o mapa operacional do que está sendo feito.

## Fase 4: Cockpit operacional

Objetivo: dar visibilidade externa e introspecção interna sem enfraquecer o REPL.

- Expor snapshot do workbench, árvore de subagentes, eventos recentes e sinais de coordenação por API.
- Criar filtros por tipo de evento, task ativa e anexos pinados.
- Suportar inspeção incremental por WebSocket e API REST.

Entregáveis:

- Snapshot por sessão.
- Feed de timeline em tempo real.
- Base para um painel que mostre tarefas, anexos e execução recursiva.

Critério de saída:

- O operador consegue auditar uma sessão viva sem abrir o namespace Python bruto, e os próprios agentes conseguem se orientar usando o mesmo mapa operacional.

## Fase 5: Políticas e automação

Objetivo: usar a observabilidade para governança real e para coordenação recursiva mais barata.

- Detectar loops por padrão operacional, não só por repetição de código.
- Interromper branches redundantes quando outra branch já resolveu o subtópico.
- Exigir justificativa curta para anexos pinados e promoção de artefatos.
- Transformar eventos e artefatos em sinais de coordenação automática para reduzir recursão inútil e aumentar recursão produtiva.

Entregáveis:

- Heurísticas baseadas em timeline.
- Políticas de recursão por profundidade, custo e redundância.
- Gatilhos de supervisão baseados em task ledger e branch health.

Critério de saída:

- A recursão fica mais barata porque o sistema aprende quando parar, consolidar ou promover resultado.
