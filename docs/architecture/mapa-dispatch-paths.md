# Mapa de Caminhos de Dispatch — RLM Runtime

> Baseline: 5 caminhos confirmados convergindo em `dispatch_runtime_prompt_sync`.  
> Fase 3 do plano de correção deve unificar estes caminhos.

---

## Visão Geral

```
┌─────────────────────────────────────────────────────────────────────┐
│  Path #5: Telegram                                                  │
│  telegram_gateway._process_via_bridge ──HTTP POST──┐                │
│                                                     ▼                │
│  Path #1: api.py:638  receive_webhook ──────► dispatch_runtime_*    │
│                                                     ▲                │
│  Path #4: webhook_dispatch.py:346  _dispatch ───────┘                │
│                                                                      │
│  Path #3: ws_gateway_endpoint.py:294 ──┐                            │
│                                         ▼                            │
│  Path #2: brain_api.py:71  dispatch_prompt ──► dispatch_runtime_*   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Tabela de Caminhos

| # | Arquivo | Linha | Função | Chama diretamente | Convenção |
|---|---------|-------|--------|-------------------|-----------|
| 1 | `rlm/server/api.py` | 638 | `receive_webhook` | `dispatch_runtime_prompt_sync` | args diretos |
| 2 | `rlm/server/brain_api.py` | 71 | `dispatch_prompt` | `dispatch_runtime_prompt_sync` | Envelope → args |
| 3 | `rlm/server/ws_gateway_endpoint.py` | 294 | `ws_gateway` | `brain_api.dispatch_prompt` | Envelope puro |
| 4 | `rlm/server/webhook_dispatch.py` | 346 | `_dispatch` | `dispatch_runtime_prompt_sync` | args diretos |
| 5 | `rlm/gateway/telegram_gateway.py` | 348 | `_process_via_bridge` | HTTP POST → Path #1 | bridge HTTP |

---

## Identidade em Cada Caminho

| # | `session_id` | `client_id` | Contexto adicional |
|---|-------------|-------------|-------------------|
| 1 | SessionManager.get_or_create() | Path param | user_id, channel via session |
| 2 | Dentro do Envelope | `envelope.source_client_id` | Envelope carrega contexto completo |
| 3 | Dentro do Envelope | `envelope.source_client_id` | Gateway TS serializa Envelope JSON |
| 4 | SessionManager.get_or_create() | Path token + body override | Auth per-device; client_id overridável |
| 5 | Indireto (via Path #1) | `f"telegram:{chat_id}"` | Constrói client_id do chat_id Telegram |

---

## Argumentos em `dispatch_runtime_prompt_sync`

| Argumento | Tipo | Paths que usam |
|-----------|------|---------------|
| `services` | `RuntimeDispatchServices` | Todos |
| `client_id` | `str` | #1, #2, #4 direto; #3 via Envelope; #5 via bridge |
| `payload` | `dict` (text, channel, metadata) | #1, #2, #4 |
| `session` | `Session` (opcional) | #1, #4 |
| `record_conversation` | `bool` | #1 |
| `source_name` | `str` | #1 ("webhook"), #4 ("webhook_dispatch") |

---

## Convenções de Chamada

| Convenção | Caminhos | Descrição |
|-----------|----------|-----------|
| **Args diretos** | #1, #4 | `dispatch_runtime_prompt_sync(services, client_id, payload, **kwargs)` |
| **Envelope → args** | #2 | Extrai campos do `Envelope`, converte para args diretos |
| **Envelope puro** | #3 | WebSocket carrega Envelope; delega a #2 |
| **Bridge HTTP** | #5 | Faz POST HTTP que ativa #1 |

---

## Proposta de Convergência (Fase 3)

**Problema**: 3 convenções de chamada diferentes para a mesma operação.

**Solução proposta**:

1. Criar `RuntimeDispatchContext` (builder) que normaliza Envelope, args diretos e request metadata em forma canônica.
2. Todo ingresso constrói `RuntimeDispatchContext` com `SessionIdentity` (T08).
3. `dispatch_runtime_prompt_sync` recebe `RuntimeDispatchContext` em vez de args soltos.
4. `brain_api.dispatch_prompt` e `receive_webhook` viram wrappers finos que constroem o contexto e delegam.

**Resultado**: 5 entradas → 1 pipeline → 1 contrato.
