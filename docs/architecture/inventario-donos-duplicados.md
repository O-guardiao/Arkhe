# Inventário de Donos Duplicados — Estado Pós-Fase 1

**Data**: 2026-04-12  
**Baseline**: 2127 testes passando, 0 violações de camada  
**Referência**: `plano-correcao-arquitetural-v2.md` T06

---

## Tabela de Conceitos

| # | Conceito | Arquivo Canônico | Arquivo Duplicado/Shim | Status | Ação |
|---|---------|------------------|------------------------|--------|------|
| 1 | **Session lifecycle** | `rlm/core/session/_impl.py` (SessionManager) | `rlm/session.py` (RLMSession — memória conversacional) | Conceitos distintos | DOCUMENTAR — não são duplicatas |
| 2 | **Envelope (bus routing)** | `rlm/core/comms/envelope.py` (Envelope, Direction, MessageType) | — | Canônico | MANTER |
| 3 | **Envelope (ingresso)** | `rlm/gateway/message_envelope.py` (InboundMessage) | — | Canônico | MANTER |
| 4 | **Envelope (WS schema)** | `rlm/gateway/envelope.py` (schema v1 TypeScript↔Python) | — | Complementar | DOCUMENTAR papel, aposentar quando TS gateway morrer |
| 5 | **Dispatch runtime** | `rlm/server/runtime_pipeline.py` (dispatch_runtime_prompt_sync) | 4 caminhos alternativos (ver T11) | 5 paths redundantes | CONVERGIR na Fase 3 |
| 6 | **Config** | `rlm/core/config.py` (RLMConfig, load_config) | — | Canônico único | MANTER ✅ |
| 7 | **Auth (legacy hash)** | `rlm/core/auth.py` | `rlm/core/security/auth.py` (JWT 9.4) | **OVERLAP** | CONSOLIDAR — JWT → core/auth.py, deprecar security/auth.py |
| 8 | **Channel registry** | `rlm/plugins/channel_registry.py` | — | Canônico | MANTER |
| 9 | **Channel status** | `rlm/core/comms/channel_status.py` | — | Canônico | MANTER |
| 10 | **Channel bootstrap** | `rlm/core/comms/channel_bootstrap.py` | — | Canônico | MANTER |
| 11 | **webhook_dispatch** | `rlm/server/webhook_dispatch.py` | `rlm/gateway/webhook_dispatch.py` | **SHIM** (re-export puro) | DELETAR shim (T09) |
| 12 | **SiblingBus** | `rlm/core/orchestration/sibling_bus.py` | ~~`rlm/core/comms/sibling_bus.py`~~ | ✅ Resolvido | DELETADO na Fase 1 (T03) |
| 13 | **operator_bridge** | `rlm/server/operator_bridge.py` | ~~`rlm/gateway/operator_bridge.py`~~ | ✅ Resolvido | DELETADO na Fase 1 (T04) |
| 14 | **Porta padrão** | `rlm/core/config.py` (5000) | — | ✅ Resolvido | UNIFICADO na Fase 1 (T05) |
| 15 | **tools/memory** | `rlm/tools/memory.py` (RLMMemory) | `rlm/skills/memory/` (SKILL.md only) | Sem overlap de código | MANTER (skills/ é doc-only) |
| 16 | **plugins/browser** | `rlm/plugins/browser.py` | `rlm/skills/browser/` (SKILL.md only) | Sem overlap de código | MANTER (skills/ é doc-only) |

---

## Resumo de Ações Pendentes

| Prioridade | Conceito | Ação | Task |
|-----------|---------|------|------|
| **P0** | webhook_dispatch shim | Deletar `gateway/webhook_dispatch.py`, atualizar imports | T09 |
| **P1** | Auth overlap | Consolidar JWT em `core/auth.py` | Novo (pós-T09) |
| **P2** | Envelope pipeline | Documentar papel de cada tipo | T07 |
| **P2** | Session naming | Documentar diferença SessionManager vs RLMSession | T07 |
| **P2** | Dispatch paths | Mapear e convergir os 5 caminhos | T11 → Fase 3 |

---

## Descobertas Positivas

- **Config**: fonte única de verdade. Bem encapsulado.
- **Channel trio** (registry/status/bootstrap): responsabilidades bem separadas.
- **tools/memory vs skills/memory**: sem duplicação de código. skills/ é doc-only.
- **plugins/browser vs skills/browser**: sem duplicação de código. skills/ é doc-only.
- **SiblingBus, operator_bridge, porta**: já resolvidos na Fase 1.
