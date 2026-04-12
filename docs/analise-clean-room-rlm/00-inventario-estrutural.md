# 00. Inventario Estrutural

## Objetivo

Este documento fixa o mapa do repositorio antes da analise comportamental. Ele responde tres perguntas:

- quais pastas sao canonicas para entender o produto
- quais pastas sao apoio, experimento ou legado
- quais arquivos de topo governam instalacao, operacao e compatibilidade

## Arquivos de topo relevantes

Arquivos operacionais e de produto no topo do repositorio:

- `README.md`
- `QUICKSTART.md`
- `pyproject.toml`
- `Makefile`
- `install.sh`
- `rlm.toml`
- `CONTRIBUTING.md`
- `REFATORACAO.md`
- `OPTIMIZATION_README.md`
- `OPTIMIZATION_REPORT.md`
- `AGENTS.md`
- `NOTICE`
- `LICENSE`
- `LICENSES/upstream-rlm-mit.txt`

Artefatos de operacao local que existem no topo, mas nao sao fonte de comportamento canonico:

- `rlm_memory_v2.db`
- `rlm_sessions.db`
- `test.db`
- `test_sessions.db`
- `rlm_states/`
- `runtime-sess_checkpoint/`
- `.rlm_workspace/`
- `.pytest_cache/`
- `test_outputs/`

## Densidade por diretorio de topo

Contagem observada no workspace durante a leitura:

- `(root)`: 33 arquivos
- `rlm`: 865 arquivos totais observados antes da limpeza de caches e skills
- `tests`: 291 arquivos observados
- `docs`: 53 arquivos
- `visualizer`: 45 arquivos
- `native`: 4002 arquivos observados antes de remover `target/`
- `packages`: 19673 arquivos observados, majoritariamente por dependencias e build output

Leitura limpa para engenharia:

- `rlm/` e a base canonica do produto
- `tests/` e a base canonica de validacao
- `docs/` e a base canonica de intencao arquitetural
- `schemas/` formaliza contratos entre camadas
- `native/` so interessa pelos fontes e manifestos; `target/` foi descartado
- `packages/` e `visualizer/` foram tratados como superficies auxiliares, nao como verdade principal do sistema

## Classificacao por area

| Area | Papel | Status para clean room |
| --- | --- | --- |
| `rlm/` | runtime Python principal | canonico |
| `tests/` | validacao de comportamento e regressao | canonico |
| `docs/` | arquitetura, analises e planos | canonico, com material historico junto |
| `schemas/` | contratos formais JSON Schema | canonico |
| `examples/` | cenarios minimos de uso | auxiliar de alto valor |
| `native/` | aceleradores Rust e futuras camadas | auxiliar/experimental |
| `packages/` | migracao parcial TypeScript | auxiliar/legado misto |
| `visualizer/` | UI exploratoria Next.js | auxiliar |
| `_migrated_to_ts/` | snapshot de migracao antiga | legado |
| `.venv-py313/`, `node_modules/`, `target/` | dependencias e builds | excluir |

## Mapa do pacote `rlm/`

Distribuicao observada por subdiretorio de primeiro nivel:

- `core`: 396 arquivos observados antes da limpeza
- `cli`: 133
- `server`: 68
- `gateway`: 52
- `environments`: 32
- `tools`: 29
- `skills`: 28
- `plugins`: 26
- `daemon`: 24
- `clients`: 21
- `utils`: 21
- `runtime`: 13
- `logger`: 9
- `rlm-root`: 4
- `static`: 1

Leitura correta desse numero:

- `core/` concentra a maior parte do comportamento do produto
- `cli/`, `server/`, `gateway/` e `daemon/` formam a camada operacional
- `skills/` documenta capacidades empacotadas, mas nao a implementacao do runtime
- `runtime/`, `plugins/`, `logger/` e `utils/` sao apoio estrutural importante

## Pastas de maior valor tecnico

Para engenharia reversa baseada em comportamento, a prioridade real e:

1. `rlm/core/`
2. `rlm/environments/`
3. `rlm/daemon/`
4. `rlm/gateway/`
5. `rlm/server/`
6. `rlm/cli/`
7. `tests/`
8. `schemas/`
9. `docs/`

## Pastas que nao devem comandar a reimplementacao

Estas pastas podem informar estrategia, mas nao devem servir como molde direto:

- `packages/gateway/` porque duplica `rlm/gateway/`
- `packages/daemon/` e `packages/config/` porque nao sao a verdade principal
- `_migrated_to_ts/` porque registra uma fase abandonada
- `visualizer/` porque e uma superficie exploratoria, nao o motor
- `native/arkhe-mcts`, `native/arkhe-vault`, `native/arkhe-audit` porque ainda representam backlog ou trabalho em progresso

## Inventario por camada

Os arquivos por pasta foram distribuidos assim nesta suite:

- `01-core-engine-memory.md`: `rlm/core`, `rlm/clients`, `rlm/environments`
- `02-daemon-gateway-server.md`: `rlm/daemon`, `rlm/server`, `rlm/gateway`, `rlm/runtime`, `rlm/plugins`
- `03-cli-tools-skills.md`: `rlm/cli`, `rlm/tools`, `rlm/skills`, `rlm/utils`, `rlm/logger`, `rlm/__main__.py`, `rlm/session.py`
- `04-tests-docs-schemas-native.md`: `tests`, `docs`, `schemas`, `native`, `packages`, `visualizer`, `examples`

## Sinais estruturais importantes

- o repo conserva coexistencia de branding Arkhe e namespace `rlm`
- o produto principal continua em Python, apesar da presenca forte de TypeScript e Rust
- o daemon persistente e parte recente, mas ja central, da arquitetura
- a suite de testes funciona como registro historico das fases de migracao e dos slices criticos
- ha artefatos de refatoracao e backups locais (`.bak`, `.new`, notas de refatoracao) que nao sao contrato de produto

## Consequencia para QA e clean room

Se voce tentar reproduzir o repositorio por arvore de pastas, vai copiar acidente historico. O que interessa aqui e:

- quais contratos cada camada expoe
- quais fluxos realmente estao em producao
- quais modulos sao canonicos e quais sao somente transitorios
