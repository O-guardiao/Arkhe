# _migrated_to_ts — Arquivos Python com equivalente TypeScript

Estes arquivos Python foram **movidos para cá** (não deletados) porque já possuem
equivalente funcional em TypeScript em `packages/gateway/src/`.

Mantidos para referência durante a transição. Quando o sistema estiver estável
em produção com os equivalentes TypeScript, estes arquivos poderão ser removidos.

---

## `server/` → `packages/gateway/src/`

| Python (aqui) | TypeScript (ativo) | Motivo seguro para mover |
|---|---|---|
| `telegram_gateway.py` | `channels/telegram.ts` | import em `try/except` no api.py |
| `discord_gateway.py` | `channels/discord.ts` | import em `try/except` no api.py |
| `slack_gateway.py` | `channels/slack.ts` | import em `try/except` no api.py |
| `whatsapp_gateway.py` | `channels/whatsapp.ts` | import em `try/except` no api.py |
| `webchat.py` | `channels/webchat.ts` | import em `try/except` no api.py |
| `operator_bridge.py` | `operator.ts` | import em `try/except` no api.py |
| `backoff.py` | `backoff.ts` | só usado por telegram_gateway (movido) |
| `heartbeat.py` | `heartbeat.ts` | só usado por telegram_gateway (movido) |
| `dedup.py` | `dedup.ts` | só usado por slack/whatsapp gateway (movidos) |
| `chunker.py` | `chunker.ts` | nenhum importador externo |
| `backpressure.py` | `backpressure.ts` | nenhum importador externo |
| `scheduler.py` | `scheduler.ts` | nenhum importador externo |
| `gateway_state.py` | `state-machine.ts` | nenhum importador externo |

## `plugins/` → `packages/gateway/src/adapters/`

| Python (aqui) | TypeScript (ativo) | Motivo seguro para mover |
|---|---|---|
| `telegram.py` | `adapters/telegram.ts` | só importado por telegram_gateway (movido) |
| `discord.py` | `adapters/discord.ts` | apenas referência de string em event_router.py |
| `slack.py` | `adapters/slack.ts` | apenas referência de string em event_router.py |
| `whatsapp.py` | `adapters/whatsapp.ts` | só importado por whatsapp_gateway (movido) |

---

## Arquivos que NÃO puderam ser movidos ainda

Permanecem em `rlm/server/` porque são importados no topo de arquivos permanentes do brain Python:

| Arquivo | Bloqueador |
|---|---|
| `auth_helpers.py` | `brain_router.py` — import top-level |
| `ws_server.py` | `server/__init__.py` + `runtime_factory.py` — import top-level |
| `message_envelope.py` | `api.py` linha 55 + `core/comms/` — import top-level |
| `drain.py` | `api.py` linha 49 — import top-level |
| `health_monitor.py` | `api.py` linha 50 — import top-level |
| `openai_compat.py` | `api.py` linha 45 — import top-level |
| `webhook_dispatch.py` | `api.py` linha 44 — import top-level |
| `channel_registry.py` | `api.py`, `runtime_pipeline.py`, `channel_bootstrap.py` etc. |

Para desbloqueá-los: converter os imports top-level em `api.py` para blocos `try/except ImportError`.
