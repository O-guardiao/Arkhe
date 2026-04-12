# ADR-001: Ownership Canônico do RLM

**Status**: Aceito
**Data**: 2026-04-12
**Contexto**: Plano de Correção de Trajetória do RLM (docs/plano-correcao-trajetoria-rlm.md)

---

## Decisão

### Python é o dono canônico do runtime recursivo

O núcleo do RLM — loop recursivo, REPL persistente, daemon, sessão, memória,
gateway, servidor e CLI — é implementado e mantido exclusivamente em Python
no pacote `rlm/`.

Qualquer funcionalidade que exista em Python canônico **não pode** ter uma
segunda implementação ativa em outra linguagem no caminho crítico.

### `packages/` é legado e está fora do caminho crítico

O diretório `packages/` contém uma migração TypeScript parcial que perdeu
centralidade. Nenhum módulo em `rlm/` pode importar de `packages/`.
Nenhum novo desenvolvimento deve ocorrer em `packages/`.

O destino de `packages/` é aposentadoria progressiva, não manutenção ativa.

### Rust entra apenas para hot path medido ou domínio de alta garantia

Os crates em `native/` (`arkhe-memory`, `arkhe-wire`, etc.) são aceleradores
pontuais. Rust **não** é uma terceira espinha dorsal do sistema.

Novos crates Rust só são aceitos quando:
1. Há benchmark demonstrando ganho mensurável no hot path, **ou**
2. O domínio exige garantias que Python não oferece (policy enforcement,
   vault, audit trail criptográfico).

### Um único dono por conceito central

Para cada conceito abaixo, existe **um e apenas um** módulo canônico:

| Conceito | Dono canônico |
|---|---|
| Sibling Bus | `rlm/core/orchestration/sibling_bus.py` |
| Envelope (MessageBus) | `rlm/core/comms/envelope.py` |
| Envelope (Gateway inbound) | `rlm/gateway/message_envelope.py` |
| Envelope (Gateway↔TS schema) | `rlm/gateway/envelope.py` |
| Auth de dispositivo/cliente | `rlm/core/auth.py` |
| Auth JWT | `rlm/core/security/auth.py` |
| Auth helpers HTTP | `rlm/gateway/auth_helpers.py` |
| Config | `rlm/core/config.py` |
| RuntimeProjection | `schemas/runtime-projection.v1.json` + `rlm/core/observability/operator_surface.py` |
| Sessão e identidade | `rlm/core/session/` |
| Dispatch de ingresso | Via daemon (`dispatch_runtime_prompt_sync`) |

### Superfícies humanas consomem projeções, não interpretam o runtime

CLI, TUI, dashboard e operator bridge são **consumidores** de
`RuntimeProjection`. Não fazem engenharia reversa do estado interno.

---

## Consequências

1. Toda documentação e onboarding deve refletir estas decisões.
2. CI deve validar que `rlm/` não importa de `packages/`.
3. Novos PRs que criem implementações paralelas de conceitos centrais
   serão rejeitados.
4. O shim `rlm/core/comms/sibling_bus.py` será mantido temporariamente
   para compatibilidade, com aviso de deprecação.
5. Nenhuma feature nova de superfície antes de consolidar ownership
   de sessão, contexto, memória e dispatch.
