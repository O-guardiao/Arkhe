# RuntimeProjection — Mapa de Uso por Superfície

**Data**: 2026-04-12  
**Schema**: `schemas/runtime-projection.v1.json`  
**Tipo Python**: `rlm.runtime.contracts.RuntimeProjection`  
**Referência**: `plano-correcao-arquitetural-v2.md` T15

---

## Contrato

`RuntimeProjection` é um dataclass (L0) com 9 campos. É instanciada em
**exatamente 1 ponto** (`operator_surface.py:372 build_runtime_snapshot()`)
e imediatamente serializada via `.to_dict()`. Todos os consumidores operam
sobre o `dict` serializado — nenhum acessa o dataclass diretamente.

## Campos

| Campo | Tipo Python | Descrição |
|-------|-------------|-----------|
| `tasks` | `dict[str, Any]` | Tarefas ativas do runtime |
| `attachments` | `dict[str, Any]` | Arquivos/artefatos anexados |
| `timeline` | `dict[str, Any]` | Eventos cronológicos da sessão |
| `recursive_session` | `dict[str, Any]` | Estado da sessão recursiva (messages, commands) |
| `coordination` | `dict[str, Any]` | Estado de coordenação paralela (branches, summary) |
| `controls` | `dict[str, Any]` | Controles de runtime (paused, focused_branch, checkpoint) |
| `strategy` | `dict[str, Any]` | Estratégia ativa do solver |
| `recursion` | `RuntimeRecursionProjection` | Sub-projeção de recursão (branches, events, summary) |
| `daemon` | `RuntimeDaemonProjection | None` | Sub-projeção do daemon (memory_accesses, channel_runtimes) |

## Fábrica

| Ponto | Arquivo | Função |
|-------|---------|--------|
| Criação | `rlm/core/observability/operator_surface.py:372` | `build_runtime_snapshot()` |
| Serialização | `rlm/runtime/contracts.py` | `RuntimeProjection.to_dict()` |
| Fallback (daemon-only) | `operator_surface.py:339` | Retorna `{"daemon": ...}` quando env indisponível |

## Consumidores por Superfície

### CLI (Workbench)

| Arquivo | Linha | Endpoint/Método | Campos Consumidos |
|---------|-------|-----------------|-------------------|
| `rlm/cli/commands/workbench.py` | 545 | `build_activity_payload()` | Payload inteiro (`["runtime"]`) |

### TUI/Operator (Server)

| Arquivo | Linha | Endpoint | Campos Consumidos |
|---------|-------|----------|-------------------|
| `rlm/server/operator_bridge.py` | 140 | `GET /operator/session/{id}/activity` | Payload inteiro |
| `rlm/server/operator_bridge.py` | 226 | `POST /session/{id}/commands` | `runtime` em resposta de comando |

### WebChat (Gateway)

| Arquivo | Linha | Endpoint | Campos Consumidos |
|---------|-------|----------|-------------------|
| `rlm/gateway/webchat.py` | 232 | `GET /webchat/session/{id}/activity` | Payload inteiro |
| `rlm/gateway/webchat.py` | 270 | `POST /session/{id}/commands` | `runtime` em resposta de comando |

## Campo → Consumidores Detalhados (produção + testes)

| Campo | Consumidores de Produção | Consumidores de Teste |
|-------|--------------------------|----------------------|
| `tasks` | workbench, operator_bridge, webchat | test_tui_dual_mode |
| `attachments` | workbench, operator_bridge, webchat | — |
| `timeline` | workbench, operator_bridge, webchat | — |
| `recursive_session` | workbench, operator_bridge, webchat | test_channels (messages, commands) |
| `coordination` | workbench, operator_bridge, webchat | test_channels, test_local_repl_persistent |
| `controls` | workbench, operator_bridge, webchat | test_channels (paused, focused_branch_id, pause_reason, last_checkpoint_path) |
| `strategy` | workbench, operator_bridge, webchat | — |
| `recursion` | workbench, operator_bridge, webchat | test_tui |
| `daemon` | workbench, operator_bridge, webchat | test_tui (daemon-only) |

## Regras

1. **Toda expansão de superfície parte do contrato** (`RuntimeProjection`), não de parsing ad-hoc.
2. Se uma superfície precisa de campo que não existe em `RuntimeProjection`, é **gap do contrato** — não inferência.
3. Novas superfícies DEVEM consumir via `build_activity_payload()` → `["runtime"]`.
4. Schema em `schemas/runtime-projection.v1.json` é a fonte de verdade para validação externa.

## Gaps Identificados

- Nenhum consumidor acessa campos granulares tipados — todos operam sobre `dict[str, Any]`.
  Isso é intencional (flexibilidade) mas reduz type-safety nos consumidores.
- `timeline` e `strategy` não possuem consumidores específicos em testes (apenas consumo bulk).
- Superfícies não validam schema do payload recebido — confiam no contrato implícito.
