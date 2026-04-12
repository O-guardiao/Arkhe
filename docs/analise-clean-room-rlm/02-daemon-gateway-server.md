# 02. Daemon, Gateway, Server, Runtime e Plugins

## Escopo

Este documento cobre as camadas que mantem o runtime vivo, exposto e integrado a canais:

- `rlm/daemon/`
- `rlm/server/`
- `rlm/gateway/`
- `rlm/runtime/`
- `rlm/plugins/`

## `rlm/daemon/`

Arquivos mapeados:

- `channel_subagents.py`
- `contracts.py`
- `llm_gate.py`
- `memory_access.py`
- `recursion_daemon.py`
- `task_agents.py`
- `warm_runtime.py`
- `__init__.py`

Leitura correta:

- `recursion_daemon.py` e o centro do runtime persistente
- `llm_gate.py` decide quando uma interacao pode ser respondida deterministicamente e quando precisa cair no motor LLM
- `task_agents.py` materializa agentes leves que evitam subir um child RLM pesado em todos os casos
- `warm_runtime.py` formaliza aquecimento e reuso do ambiente
- `memory_access.py` projeta leitura/escrita de memoria para a operacao e para a TUI
- `channel_subagents.py` normaliza eventos por canal antes do dispatch ao daemon

Contrato observavel:

- o ambiente recursivo nao precisa morrer a cada interacao
- existe snapshot operacional do daemon com estado, latencia, canais anexados e sessoes ativas
- parte das interacoes pode ser respondida sem passar pelo pipeline completo do LLM
- sessoes aquecidas e manutencao fazem parte do comportamento esperado

Riscos de QA:

- lock contention no daemon sob varios canais ativos
- pruning de sessoes erradas durante maintenance
- drift entre runtime aquecido e estado de sessao persistido
- classificacao errada no `llm_gate.py`, desviando mensagens para rota deterministica indevida

## `rlm/server/`

Arquivos mapeados:

- `api.py`
- `backpressure.py`
- `brain_api.py`
- `brain_router.py`
- `dedup.py`
- `drain.py`
- `event_router.py`
- `health_monitor.py`
- `openai_compat.py`
- `runtime_pipeline.py`
- `scheduler.py`
- `ws_server.py`
- `__init__.py`

Leitura correta:

- `api.py` sobe a superficie FastAPI principal e encadeia startup/shutdown
- `runtime_pipeline.py` e a ponte entre request/canal e o runtime real
- `backpressure.py` e `drain.py` tratam sobrecarga e encerramento gracioso
- `openai_compat.py` revela ambicao de expor API compativel
- `scheduler.py` mostra que o runtime nao e apenas reativo; ele tambem inicia trabalho programado
- `health_monitor.py` e `event_router.py` reforcam observabilidade e roteamento interno

Contrato observavel:

- ha API REST, WebSocket e endpoints operacionais
- o servidor consegue operar em modo com auth/token e integrar canais
- o scheduler dispara trabalho no runtime vivo
- shutdown e drain nao sao improvisados

Riscos:

- requests entrando durante drain
- inconsistencias entre API compativel OpenAI e contratos internos
- backpressure mal calibrado causando timeout em cascata
- websocket e webhook disputando sessao/estado sem serializacao suficiente

## `rlm/gateway/`

Arquivos mapeados:

- `auth_helpers.py`
- `backoff.py`
- `chunker.py`
- `discord_gateway.py`
- `envelope.py`
- `gateway_state.py`
- `heartbeat.py`
- `message_envelope.py`
- `operator_bridge.py`
- `README.md`
- `slack_gateway.py`
- `telegram_gateway.py`
- `transport_router.py`
- `webchat.py`
- `webhook_dispatch.py`
- `whatsapp_gateway.py`
- `ws_gateway_endpoint.py`
- `__init__.py`

Leitura correta:

- os gateways Python sao a implementacao canonica das integracoes multichannel
- `transport_router.py` e `webhook_dispatch.py` concentram fluxo e acoplamento entre transporte e runtime
- `operator_bridge.py` existe para expor uma superficie interna/operacional separada dos webhooks comuns
- `backoff.py`, `heartbeat.py`, `chunker.py` e `gateway_state.py` indicam preocupacao real com resiliencia e UX de entrega

Contrato observavel:

- o sistema suporta Telegram, Discord, Slack, WhatsApp, webchat e WS bridge
- existe envelope de mensagem e roteamento unificado para varios transportes
- auth interna e tokens administrativos importam para a operacao
- o runtime precisa saber anexar/destacar canais e publicar respostas para o destino correto

Riscos de QA:

- mensagens chunkadas fora de ordem
- replay ou duplicacao em webhooks
- prioridade errada entre tokens internos e administrativos
- ciclo de cross-channel forwarding
- `packages/gateway/` virar referencia acidental, apesar de `rlm/gateway/` ser o canonico

## `rlm/runtime/`

Arquivos mapeados:

- `contracts.py`
- `native_policy_adapter.py`
- `python_runtime_guard.py`
- `__init__.py`

Leitura:

- a pasta `runtime/` e pequena, mas importante: ela declara contratos e guardas para aproximar o runtime Python de politicas e adaptadores nativos
- `native_policy_adapter.py` aponta para a expansao Rust/Policy sem tornar isso ainda a espinha dorsal do sistema

## `rlm/plugins/`

Arquivos mapeados:

- `audio.py`
- `browser.py`
- `channel_registry.py`
- `discord.py`
- `mcp.py`
- `slack.py`
- `telegram.py`
- `whatsapp.py`
- `__init__.py`

Leitura correta:

- plugins aqui sao adaptadores e registros, nao a implementacao inteira do canal
- `channel_registry.py` e um ponto sensivel, pois coordena reply/dispatch entre superfices
- `mcp.py`, `audio.py` e `browser.py` ampliam a nocao de canal para alem de mensageria textual

## Fluxo funcional observado

Fluxo tipico de interacao:

1. evento chega por webhook, websocket, operator route ou scheduler
2. `server/api.py` ou `gateway/webhook_dispatch.py` valida e normaliza
3. `runtime_pipeline.py` resolve sessao e entrega `ChannelEvent` ou equivalente ao daemon
4. `daemon/llm_gate.py` classifica a demanda
5. daemon escolhe rota deterministica, task agent ou pipeline completo do motor recursivo
6. memoria e snapshots operacionais sao atualizados
7. `gateway/transport_router.py` ou registry de canal devolve a resposta ao destino

## Testes relacionados

Cobertura observada relevante:

- `tests/test_recursion_daemon.py`
- `tests/test_gateway_infra.py`
- `tests/test_ws_gateway_protocol.py`
- `tests/test_telegram_bridge.py`
- `tests/test_channel_bootstrap.py`
- `tests/test_channels.py`
- `tests/test_message_bus.py`
- `tests/test_phase3_gateway_migration.py`
- `tests/test_phase4_config_crosschannel.py`
- `tests/test_phase5_auth_clients.py`
- `tests/test_phase6_channel_discovery.py`
- `tests/test_critical_gateway.py`
- `tests/test_service_update.py`

## O que preservar numa reimplementacao clean room

- runtime persistente com nocao explicita de warm state
- gate que separa rotas deterministicas de rotas que exigem LLM
- infraestrutura de gateways multichannel com envelope comum
- shutdown/drain/backpressure como comportamento operacional do produto
- snapshots e visibilidade de runtime para CLI/TUI/operador

## O que nao copiar

- os nomes `RecursionDaemon`, `LLMGate`, `ChannelEvent`, `RecursionResult`
- a topologia exata entre `server`, `gateway`, `plugins` e `daemon`
- a prioridade historica de variaveis de ambiente e tokens so porque ela ficou assim nesta codebase
- a duplicidade entre `message_envelope.py`, `envelope.py` e outros artefatos se sua arquitetura puder unificar melhor
