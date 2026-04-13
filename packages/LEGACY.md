# ⚠️ LEGACY — Não usar para novo desenvolvimento

Este diretório contém uma migração TypeScript parcial que **perdeu centralidade**.

## Status por módulo

| Módulo | Equivalente canônico Python | Status |
|---|---|---|
| `channels/` | `rlm/core/comms/` | Sem consumidor ativo |
| `cli/` | `rlm/cli/` | Absorvido pelo Python |
| `config/` | `rlm/core/config.py` | Duplica Python canônico |
| `daemon/` | `rlm/daemon/` | Duplica Python canônico |
| `gateway/` | `rlm/gateway/` | Duplica Python canônico |
| `server/` | `rlm/server/` | Duplica Python canônico |
| `terminal/` | `rlm/cli/tui/` + rich output | Absorvido pelo Python |

## Regras

1. **Nenhum módulo em `rlm/` pode importar de `packages/`.**
2. **Nenhum novo arquivo deve ser criado aqui.**
3. **Este diretório será aposentado progressivamente.**
4. **Data de sunset: 2025-Q3.** Após essa data o diretório será removido de `main` (ou movido para branch `legacy/`).

Referência: [ADR-001](../docs/architecture/ADR-001-canonical-ownership.md), [ADR-004](../docs/architecture/ADR-004-python-canonical.md)
