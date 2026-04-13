# ADR-004 — Python Canônico, packages/ Legado, Rust Pontual

| Campo | Valor |
|-------|-------|
| **Status** | Aceito |
| **Data** | 2026-04-12 |
| **Autores** | Equipe Arkhe |
| **Contexto** | Plano de Correção Arquitetural v2 — Fase 0 T01 |

---

## Contexto

O repositório Arkhe (RLM) contém três ecossistemas de linguagem:

1. **Python** — motor recursivo (`rlm/`), 2134+ testes, CI, layer checker, schemas versionados.
2. **TypeScript/JavaScript** — `packages/` (193 fontes), herança do BrowserOS-agent. Nenhum import canônico de `rlm/` aponta para `packages/`. Já demarcado como legado em `packages/LEGACY.md`.
3. **Rust** — Usado em crates auxiliares (`agent-browser/crates/`). Não é o motor principal.

A ausência de uma declaração formal de dono por linguagem causa:
- Ambiguidade sobre onde implementar features novas.
- Manutenção de CI/linting em código morto.
- Confusão para contribuidores: "uso o módulo Python ou o pacote TS?"

---

## Decisão

### 1. Python é o runtime canônico

Todo código que implementa:
- Engine recursivo (core/, orchestration/)
- Comunicação multicanal (comms/, gateway/)
- Server e API (server/)
- Daemon e CLI (daemon/, cli/)
- Observabilidade e contratos (runtime/, observability/)
- Tools, skills, plugins (tools/, skills/, plugins/)

...DEVE ser escrito em Python e residir em `rlm/`.

### 2. packages/ é legado

- **Status**: Congelado. Nenhum código novo em `packages/`.
- **Sunset**: A ser removido de `main` na Fase 5 do plano arquitetural.
- **CI**: Imports de `packages/` em `rlm/` devem falhar o build (T10).
- **Referência**: `packages/LEGACY.md` já declara status. Esta ADR formaliza.

### 3. Rust é pontual

- Rust é usado para módulos de performance crítica (crates computacionais, bindings nativos).
- Rust NÃO substitui Python como linguagem do motor.
- Novos crates Rust requerem justificativa documentada (benchmark comparativo com Python).

---

## Consequências

| Consequência | Impacto |
|-------------|---------|
| Toda feature nova é implementada em Python em `rlm/` | Fim da ambiguidade de linguagem |
| `packages/` não recebe código novo | Redução de carga cognitiva e CI |
| Contribuidores sabem onde implementar | Onboarding mais rápido |
| Rust é usado por exceção, não por padrão | Complexidade de build controlada |

---

## Referências

- `packages/LEGACY.md` — declaração de legado
- `docs/architecture/plano-correcao-arquitetural-v2.md` — plano completo
- `scripts/check_layer_imports.py` — enforcement de camadas Python
