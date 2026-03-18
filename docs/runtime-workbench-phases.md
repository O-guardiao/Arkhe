# Runtime Workbench: Plano por Fases

Leitura complementar recomendada: [Analise Real do Sistema RLM](rlm-runtime-reality-analysis.md)

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

## Avaliação por fase

### Avaliação da Fase 1

Status: concluída no backend.

Evidência:

- APIs `task_*`, `attach_*` e `timeline_*` já estão injetadas no `LocalREPL`.
- Checkpoint restaura ledger, anexos, timeline e digest de coordenação.
- `/sessions/{session_id}/runtime` já expõe snapshot operacional da sessão.
- O loop do `RLM` já registra eventos automáticos de execução, iteração, compaction e finalização.

Lacunas para considerar a fase 100% fechada no produto, não só no runtime:

- Nenhuma crítica para o backend.
- Opcionalmente, documentar o contrato HTTP do endpoint de runtime em referência de API.

Conclusão:

- O critério de saída da fase 1 já foi atingido.

### Avaliação da Fase 2

Status: majoritariamente concluída, com uma lacuna funcional relevante.

Evidência:

- `sub_rlm`, `sub_rlm_async`, `sub_rlm_parallel` e `sub_rlm_parallel_detailed` já registram `task_id`, `branch_id`, profundidade, spawn e término.
- O `SiblingBus` já alimenta um digest persistido por sessão com sinais semânticos e eventos observáveis.
- O batch paralelo já é um nó pai na árvore operacional, e o detailed já devolve resumo com vencedor, cancelamentos e mapeamento de tasks por branch.
- Handoff e retry orientado por evaluator já reaproveitam `task_id` em vez de abrir execução derivada órfã.

O que ainda falta para fechar totalmente a fase 2:

- Tornar visível no snapshot, de forma explícita, quais artefatos úteis foram produzidos por cada subagente ou branch, em vez de depender só de `return_artifacts=True` e inspeção manual.
- Opcionalmente expor uma árvore de execução já materializada, em vez de apenas tasks e branch bindings separados.

Conclusão:

- A fase 2 está operacionalmente forte, mas ainda não fecha completamente a parte de artefatos úteis por branch.

### Avaliação da Fase 3

Status: parcialmente iniciada.

Evidência:

- Já existe uma camada separada de anexos de contexto no runtime.
- Já existe pinagem de anexos e persistência separada do histórico textual.

O que falta para concluir a fase 3:

- Separar formalmente `task_context`, `workspace_context`, `session_context` e `memory_context` como objetos de primeira classe.
- Fazer a compaction agir por tipo de contexto, não só no transcript.
- Criar promoção explícita de anexos para artefatos reutilizáveis entre turnos.

Conclusão:

- Esta fase ainda não está concluída. Hoje existe infraestrutura base, mas não existe a separação tipada prometida.

### Avaliação da Fase 4

Status: parcialmente concluída no backend, aberta no produto.

Evidência:

- O snapshot por sessão já existe e já suporta filtros de coordenação.
- O runtime já produz timeline suficiente para inspeção externa.

O que falta para concluir a fase 4:

- Feed incremental por WebSocket ou mecanismo equivalente de atualização contínua.
- Visualização externa clara da árvore de execução, tasks, anexos e coordenação em tempo real.
- Filtros adicionais por task ativa, anexos pinados e possivelmente por fase operacional.

Conclusão:

- O backend do cockpit existe. O cockpit como superfície operacional ainda não existe.

### Avaliação da Fase 5

Status: parcialmente concluída.

Evidência:

- Já existe política real de redução de custo com `stop_on_solution` para cortar branches redundantes.
- O loop principal já respeita cancelamento disparado pela coordenação.

O que falta para concluir a fase 5:

- Heurísticas de loop e redundância baseadas em timeline e padrão operacional.
- Política de promoção de artefatos e anexos com justificativa e governança.
- Gatilhos de supervisão por saúde de branch, profundidade e custo acumulado.
- Políticas além de `solution_found`, como `switch_strategy` e `consensus_reached`, influenciando execução de forma concreta.

Conclusão:

- A fase 5 começou pelo caso de maior retorno imediato, mas ainda está longe de concluída como camada de governança.

## Fechamento real das etapas

Se o objetivo for declarar as etapas documentadas como concluídas com rigor técnico, o estado real é este:

- Fase 1: concluída.
- Fase 2: quase concluída, faltando fechar artefatos por branch e opcionalmente árvore materializada.
- Fase 3: não concluída.
- Fase 4: não concluída como produto; parcialmente pronta no backend.
- Fase 5: não concluída; existe apenas a primeira política forte de coordenação.

Portanto, não faz sentido dizer que o plano inteiro foi executado. O que foi executado com solidez cobre a espinha dorsal das fases 1 e 2 e apenas a base técnica das fases 3 a 5.

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
