# 01. Core, Engine e Memory

## Escopo

Este documento cobre os blocos canonicos de computacao e execucao:

- `rlm/core/`
- `rlm/clients/`
- `rlm/environments/`

## `rlm/core/` raiz

Arquivos mapeados:

- `rlm/core/auth.py`
- `rlm/core/config.py`
- `rlm/core/structured_log.py`
- `rlm/core/types.py`
- `rlm/core/_refactor_rlm.py`
- `rlm/core/rlm copy.md`
- `rlm/core/__init__.py`

Leitura:

- `config.py` centraliza configuracao estrutural do runtime
- `types.py` concentra contratos de dados reaproveitados em varias camadas
- `structured_log.py` formaliza logging estruturado e observabilidade
- `_refactor_rlm.py` e `rlm copy.md` sao artefatos historicos; nao sao contrato de produto

## `rlm/core/comms/`

Arquivos mapeados:

- `channel_bootstrap.py`
- `channel_probe.py`
- `channel_status.py`
- `comms_utils.py`
- `crosschannel_identity.py`
- `delivery_worker.py`
- `envelope.py`
- `internal_api.py`
- `mcp_client.py`
- `message_bus.py`
- `outbox.py`
- `routing_policy.py`
- `sibling_bus.py`
- `__init__.py`

Contrato observavel:

- existe uma infraestrutura multichannel explicita
- mensagens podem transitar por envelope comum
- ha fila/outbox e worker de entrega
- a identidade cross-channel e tratada como problema de primeira classe

Riscos de QA:

- ordenacao de mensagens entre canais
- loops de encaminhamento cross-channel
- backlog/outbox crescendo sem drenagem previsivel
- duplicidade entre `core/comms/sibling_bus.py` e `core/orchestration/sibling_bus.py`

## `rlm/core/engine/`

Arquivos mapeados:

- `comms_utils.py`
- `compaction.py`
- `control_flow.py`
- `enums.py`
- `hooks.py`
- `lm_handler.py`
- `loop_detector.py`
- `permission_policy.py`
- `rlm.py`
- `rlm_context_mixin.py`
- `rlm_loop_mixin.py`
- `rlm_mcts_mixin.py`
- `rlm_persistence_mixin.py`
- `runtime_workbench.py`
- `session_journal.py`
- `sub_rlm.py`
- `sub_rlm.py.bak`
- `sub_rlm.py.new`
- `_sub_rlm_helpers.py`
- `_sub_rlm_types.py`
- `__init__.py`

Leitura correta:

- `rlm.py` e o ponto de montagem do motor recursivo
- os mixins dividem contexto, loop, MCTS e persistencia
- `lm_handler.py` materializa a ponte com clientes LLM
- `sub_rlm.py` concentra a semantica de subagentes e recursao
- `compaction.py`, `loop_detector.py` e `control_flow.py` defendem o loop contra explosao de contexto e repeticao
- `hooks.py` e `runtime_workbench.py` ampliam observabilidade e coordenacao
- `sub_rlm.py.bak` e `sub_rlm.py.new` sao artefatos de trabalho; nao devem ser tratados como codigo canonico

Contrato observavel para clean room:

- o produto executa em loop iterativo LLM -> codigo/ferramenta -> feedback -> nova iteracao
- subagentes existem e herdam contexto essencial do pai
- ha limites de profundidade, iteracao e compaction
- o runtime pode reaproveitar estado aquecido entre turnos
- o sistema diferencia papeis como planner, worker, evaluator e variantes leves

Hotspots de regressao:

- `rlm.py`
- `rlm_loop_mixin.py`
- `sub_rlm.py`
- `rlm_context_mixin.py`
- `compaction.py`
- `loop_detector.py`
- `permission_policy.py`

## `rlm/core/integrations/`

Arquivos mapeados:

- `mcp_client.py`
- `obsidian_bridge.py`
- `obsidian_mirror.py`
- `__init__.py`

Leitura:

- o runtime fala com MCP e com o espaco de conhecimento/nota local
- integracao com Obsidian nao e periferica: ela participa da estrategia de memoria e espelhamento

Riscos:

- drift entre mirror e fonte original
- integracao MCP ampliando superficie de privilegio

## `rlm/core/lifecycle/`

Arquivos mapeados:

- `cancellation.py`
- `disposable.py`
- `shutdown.py`
- `__init__.py`

Contrato:

- cancelamento e descarte de recursos nao sao ad hoc
- o runtime tenta formalizar encerramento e propagacao de cancelamento

## `rlm/core/memory/`

Arquivos mapeados:

- `embedding_backend.py`
- `hybrid_search.py`
- `knowledge_base.py`
- `knowledge_consolidator.py`
- `memory_budget.py`
- `memory_hot_cache.py`
- `memory_manager.py`
- `memory_mini_agent.py`
- `memory_types.py`
- `mmr.py`
- `semantic_retrieval.py`
- `temporal_decay.py`
- `vector_utils.py`
- `__init__.py`

Contrato observavel:

- memoria nao e so banco de vetores; ela mistura busca semantica, keyword, consolidacao e budget
- o runtime possui camada de conhecimento de longo prazo
- ha estrategias explicitas de hot cache, MMR, decaimento temporal e consolidacao
- a busca pode aproveitar aceleracao Rust, mas o comportamento precisa continuar valido com fallback Python

Riscos de QA:

- inconsistencias entre fallback Python e aceleracao Rust
- retrieval contaminado por metadata errada de agente/canal/profundidade
- budget de memoria causando perda silenciosa de contexto util
- consolidacao unindo fatos incorretamente

## `rlm/core/observability/`

Arquivos mapeados:

- `operator_surface.py`
- `turn_telemetry.py`
- `__init__.py`

Leitura:

- existe uma projecao operacional oficial da recursao e do runtime
- a camada de operacao nao depende so de logs crus; ela espera snapshots estruturados

## `rlm/core/optimized/`

Arquivos mapeados:

- `benchmark.py`
- `fast.py`
- `opt_types.py`
- `parsing.py`
- `wire.py`
- `_impl.py`
- `__init__.py`

Leitura:

- a area optimized concentra aceleracoes e backends alternativos de parsing/serializacao
- e um detalhe de implementacao, nao um contrato de produto

## `rlm/core/orchestration/`

Arquivos mapeados:

- `handoff.py`
- `mcts.py`
- `role_orchestrator.py`
- `scheduler.py`
- `sibling_bus.py`
- `supervisor.py`
- `__init__.py`

Contrato observavel:

- ha uma camada de supervisao acima do loop bruto
- handoff entre papeis e estrategia de role orchestration fazem parte do comportamento esperado
- MCTS aparece como capacidade experimental, nao como espinha dorsal obrigatoria

## `rlm/core/security/`

Arquivos mapeados:

- `auth.py`
- `execution_fence.py`
- `execution_policy.py`
- `exec_approval.py`
- `_impl.py`
- `__init__.py`

Contrato observavel:

- execucao de codigo precisa passar por politica, fence e aprovacao
- autenticacao e politica de execucao vivem em camada propria
- o sistema tenta diferenciar profundidade/role ao aplicar restricoes

Riscos de seguranca:

- escapes em subagentes profundos
- fence insuficiente em shells e imports perigosos
- vazamento de tokens ou credenciais em logs e artefatos

## `rlm/core/session/`

Arquivos mapeados:

- `client_registry.py`
- `model_overrides.py`
- `send_policy.py`
- `session_key.py`
- `session_label.py`
- `transcript.py`
- `_impl.py`
- `__init__.py`

Contrato observavel:

- sessao e uma entidade formal do sistema, nao um dict improvisado
- existem chaves, rotulos, politicas de envio e overrides por sessao
- transcripts e estado operacional sao parte do produto

## `rlm/core/skillkit/`

Arquivos mapeados:

- `sif.py`
- `skill_loader.py`
- `skill_telemetry.py`
- `__init__.py`

Leitura:

- o sistema trata skill como pacote versionavel/telemetrico
- loading e observabilidade de skills fazem parte da infraestrutura, nao so do texto do prompt

## `rlm/core/tools/`

Arquivos mapeados:

- `dispatcher.py`
- `registry.py`
- `specs.py`
- `__init__.py`

Contrato observavel:

- tools possuem registry e especificacao formal
- dispatch de tool nao e acoplado diretamente ao loop do agente

## `rlm/clients/`

Arquivos mapeados:

- `anthropic.py`
- `azure_openai.py`
- `base_lm.py`
- `gemini.py`
- `litellm.py`
- `openai.py`
- `portkey.py`
- `__init__.py`

Contrato observavel:

- o runtime abstrai varios provedores por interface comum
- cada client precisa lidar com completion, acompletion e accounting de uso
- diferencas entre APIs nativas e proxies sao detalhe interno; o contrato externo e a resposta integrada ao loop

Riscos:

- divergencia entre provedores em tool calls e streaming
- tracking de uso inconsistente
- fallback acidental para modelos/base URLs inadequados

## `rlm/environments/`

Arquivos mapeados:

- `base_env.py`
- `constants.py`
- `daytona_repl.py`
- `docker_repl.py`
- `local_repl.py`
- `local_repl.py.bak`
- `modal_repl.py`
- `prime_repl.py`
- `_checkpoint.py`
- `_repl_tools.py`
- `_runtime_state.py`
- `_sandbox.py`
- `__init__.py`

Contrato observavel:

- existe um ambiente local principal e variantes isoladas/remotas
- o namespace do REPL recebe funcoes injetadas e contexto estruturado
- checkpoint, runtime state e sandbox sao responsabilidades formais
- `local_repl.py` e a espinha dorsal do produto recursivo
- `local_repl.py.bak` e artefato de trabalho, nao contrato

Riscos de QA:

- vazamento de estado entre turnos persistentes
- sandbox incompleto em execucao local
- diferenca de comportamento entre `local_repl`, `docker_repl`, `modal_repl`, `prime_repl` e `daytona_repl`

## Testes relacionados

Cobertura observada relevante:

- `tests/test_local_repl.py`
- `tests/test_local_repl_persistent.py`
- `tests/repl/test_local_repl.py`
- `tests/test_multi_turn_integration.py`
- `tests/test_memory_cohesion.py`
- `tests/test_memory_corrections_s15_17.py`
- `tests/test_knowledge_base.py`
- `tests/test_mcts.py`
- `tests/test_role_orchestration.py`
- `tests/test_sibling_bus.py`
- `tests/test_execution_policy.py`
- `tests/test_security.py`
- `tests/test_security_phase94.py`
- `tests/clients/test_openai.py`
- `tests/clients/test_gemini.py`

## O que preservar numa reimplementacao clean room

- loop iterativo com capacidade de delegacao e retorno final controlado
- sessao, memoria e compaction como problemas de primeira classe
- subagentes com limites de profundidade e contexto
- ambientes de execucao com injecoes explicitas de ferramentas e contexto
- politica de execucao e fences de seguranca

## O que nao copiar

- a divisao exata em mixins
- os nomes `sub_rlm`, `RLM`, `LMHandler`, `ContextCompactor` e afins
- a coexistencia historica de `.bak`, `.new`, notas de refatoracao e duplicacoes de modulo
- a fragmentacao interna de memoria so porque ela existe aqui
