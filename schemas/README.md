# Schemas — Fonte da Verdade (Source of Truth)

Este diretório contém os **JSON Schemas formais** que definem os contratos de dados do sistema RLM (arkhe). Os schemas são a fronteira entre as três camadas tecnológicas (TypeScript Gateway ↔ Python Brain ↔ Rust Native).

## Arquivos

| Arquivo | Versão | Descrição |
|---------|--------|-----------|
| [`envelope.v1.json`](envelope.v1.json) | v1 | Unidade de transferência de mensagem entre canais e o Brain |
| [`ws-protocol.v1.json`](ws-protocol.v1.json) | v1 | Protocolo de mensagens WebSocket (Gateway ↔ Brain) |
| [`health-report.v1.json`](health-report.v1.json) | v1 | Relatório de saúde dos canais do gateway |
| [`runtime-projection.v1.json`](runtime-projection.v1.json) | v1 | Projeção operacional canônica do runtime para CLI, TUI, dashboard e operator surface |
| [`tool-spec.v1.json`](tool-spec.v1.json) | v1 | Especificação de ferramentas do ToolRegistry |
| [`permission-policy.v1.json`](permission-policy.v1.json) | v1 | Política de permissões para execução de ferramentas |

## Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                    Gateway TypeScript                        │
│   packages/gateway/src/envelope.ts  ←→  envelope.v1.json   │
│                        ↕  ws-protocol.v1.json               │
├─────────────────────────────────────────────────────────────┤
│                     Brain Python                             │
│   rlm/server/brain_router.py        ←→  envelope.v1.json   │
│   rlm/core/tools/registry.py        ←→  tool-spec.v1.json  │
│   rlm/core/observability/operator_surface.py ← runtime-projection │
│   rlm/core/engine/permission_policy.py ← permission-policy │
└─────────────────────────────────────────────────────────────┘
                             ↕
┌─────────────────────────────────────────────────────────────┐
│                   Rust Native Crates                         │
│   native/arkhe-wire  ←→  envelope.v1.json (deserialização) │
│   native/arkhe-policy-core ←→ permission-policy.v1.json    │
│   native/arkhe-bash  ←→  tool-spec.v1.json (DangerAccess)  │
└─────────────────────────────────────────────────────────────┘
```

## Design Principles

### 1. Envelope como Unidade Atômica
O `Envelope` (`envelope.v1.json`) é o único objeto que cruza a fronteira TypeScript↔Python. Toda mensagem — independente do canal — é normalizada para Envelope antes de ser processada.

**Campos chave:**
- `source_client_id`: chave de roteamento formato `{canal}:{id}` (ex: `telegram:123`)
- `direction`: `inbound` (canal→brain) | `outbound` (brain→canal) | `internal`
- `message_type`: tipo semântico para roteamento diferenciado
- `correlation_id` / `reply_to_id`: rastreio de conversas multi-turno

### 2. WebSocket Protocol como Canal de Evento
O protocolo WS (`ws-protocol.v1.json`) define o protocolo binário de controle entre Gateway e Brain:
- `envelope` — mensagem de dados
- `ack` — confirmação de entrega
- `event` — observabilidade
- `ping/pong` — keepalive
- `error` — erro de protocolo
- `health.request/report` — monitoramento

### 2.1 RuntimeProjection como visão oficial do runtime
O schema `runtime-projection.v1.json` é a única projeção oficial do estado recursivo consumida por superfícies humanas. CLI, TUI, dashboard e operator routes devem ler esta projeção, não reconstruir semântica a partir de ledgers internos.

**Campos chave:**
- `recursion`: branches, controles, eventos e sumário recursivo
- `daemon`: estado operacional, outbox, canais anexados e memória acessada
- blocos brutos `tasks`, `attachments`, `timeline`, `recursive_session`, `coordination`, `controls` e `strategy` preservados como contexto operacional, não como contrato interpretado pela UI

### 3. ToolSpec inspirado no claw-code
O schema `tool-spec.v1.json` replica a estrutura `ToolSpec` do claw-code (Rust):
```rust
pub struct ToolSpec {
    pub name: &'static str,
    pub description: &'static str,
    pub input_schema: Value,
    pub required_permission: PermissionMode,
}
```

As 3 camadas do `GlobalToolRegistry` do claw-code:
- **builtins**: ferramentas nativas (bash, read_file, write_file, etc.)
- **plugin_tools**: ferramentas de plugins externos
- **runtime_tools**: ferramentas MCP dinâmicas

### 4. Permission Policy com PolicyRule
O schema `permission-policy.v1.json` replica o sistema de `PolicyRule` do claw-code:
```rust
pub struct PolicyRule {
    pub name: String,
    pub condition: PolicyCondition,  // ToolName | ToolPattern | Permission | Always
    pub action: PolicyAction,         // Allow | Deny | RequireApproval | Audit
    pub priority: u32,
}
```

## Validação

Para validar um JSON contra um schema:

```bash
# Com ajv-cli (npm)
npx ajv validate -s schemas/envelope.v1.json -d path/to/envelope.json

# Com Python jsonschema
python -c "
import json, jsonschema
schema = json.load(open('schemas/envelope.v1.json'))
data = json.load(open('path/to/envelope.json'))
jsonschema.validate(data, schema)
print('Valid!')
"
```

## Versionamento

- Schemas **v1.x** são backwards-compatible (novos campos `optional`)
- Mudanças breaking → novo arquivo `envelope.v2.json`
- O campo `$id` define a URL canônica do schema

## Referência: claw-code ↔ RLM Mapping

| Padrão claw-code | Equivalente RLM |
|-----------------|-----------------|
| `ToolSpec` (Rust) | `tool-spec.v1.json` |
| `PermissionMode` (Rust) | `permission_policy.v1.json#/$defs/PolicyRule/condition` |
| `GlobalToolRegistry` | `rlm/core/tools/registry.py` |
| `Session.push_message()` | `rlm/core/engine/session_journal.py` |
| `ConversationRuntime<C,T>` | `rlm/core/engine/rlm.py` |
| `BashCommandInput` | `native/arkhe-bash/src/bash.rs` |
| `SandboxConfig` | `native/arkhe-bash/src/sandbox.rs` |
| `SessionPersistence` (JSONL rotation) | `rlm/core/engine/session_journal.py` |
