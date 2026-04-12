# Inventário de Violações de Camada — RLM

**Gerado por**: `scripts/check_layer_imports.py`  
**Data**: 2025-07-11  
**Total**: 13 violações

## Violações core → gateway (2)

Ambas são TYPE_CHECKING imports de `InboundMessage` — já estão dentro de `if TYPE_CHECKING:` em `envelope.py`. Verificar se `message_bus.py` faz o mesmo.

| # | Arquivo | Linha | Import | Severidade |
|---|---|---|---|---|
| 1 | `rlm/core/comms/envelope.py` | 31 | `rlm.gateway.message_envelope` | Baixa — TYPE_CHECKING guard |
| 2 | `rlm/core/comms/message_bus.py` | 106 | `rlm.gateway.message_envelope` | Verificar |

## Violações core → daemon (3)

`sub_rlm.py` e `role_orchestrator.py` importam do daemon em runtime. São acoplamentos reais.

| # | Arquivo | Linha | Import | Severidade |
|---|---|---|---|---|
| 3 | `rlm/core/engine/sub_rlm.py` | 48 | `rlm.daemon` | **Alta** — runtime import |
| 4 | `rlm/core/engine/sub_rlm.py` | 49 | `rlm.daemon.task_agents` | **Alta** — runtime import |
| 5 | `rlm/core/orchestration/role_orchestrator.py` | 8 | `rlm.daemon` | **Alta** — top-level import |

## Violações core → server (1)

| # | Arquivo | Linha | Import | Severidade |
|---|---|---|---|---|
| 6 | `rlm/core/observability/operator_surface.py` | 725 | `rlm.server.runtime_pipeline` | **Alta** — inversão de dependência |

## Violações gateway → server (4)

Gateways importam de server (dedup, runtime_pipeline, brain_api). Indica que server expõe funcionalidade que deveria estar em core ou gateway.

| # | Arquivo | Linha | Import | Severidade |
|---|---|---|---|---|
| 7 | `rlm/gateway/operator_bridge.py` | 17 | `rlm.server.runtime_pipeline` | **Alta** |
| 8 | `rlm/gateway/slack_gateway.py` | 52 | `rlm.server.dedup` | Média — utility que deveria estar em core |
| 9 | `rlm/gateway/webhook_dispatch.py` | 51 | `rlm.server.runtime_pipeline` | **Alta** |
| 10 | `rlm/gateway/whatsapp_gateway.py` | 46 | `rlm.server.dedup` | Média — utility que deveria estar em core |
| 11 | `rlm/gateway/ws_gateway_endpoint.py` | 37 | `rlm.server.brain_api` | **Alta** |

## Violações server → daemon (2)

| # | Arquivo | Linha | Import | Severidade |
|---|---|---|---|---|
| 12 | `rlm/server/api.py` | 57 | `rlm.daemon` | Média — orquestração |
| 13 | `rlm/server/runtime_pipeline.py` | 23 | `rlm.daemon` | Média — orquestração |

## Plano de Correção Priorizado

### Batch 1 — TYPE_CHECKING guards (baixo risco)
- [x] `envelope.py:31` já está em `if TYPE_CHECKING:`
- [ ] Verificar `message_bus.py:106` e aplicar TYPE_CHECKING se necessário

### Batch 2 — Mover utilities para core (médio risco)
- [ ] `rlm/server/dedup.py` → `rlm/core/comms/dedup.py` (usado por 2 gateways)

### Batch 3 — Inversão de dependência interfaces (alto risco)
- [ ] Extrair interface/protocol de `runtime_pipeline` para `rlm/core/` 
- [ ] Extrair interface de `brain_api` para `rlm/core/`
- [ ] Desacoplar `sub_rlm.py` do daemon via injeção de dependência

### Batch 4 — Reestruturação profunda
- [ ] Mover `role_orchestrator` daemon import para injeção
- [ ] Resolver `operator_surface.py` → server (inversão)
