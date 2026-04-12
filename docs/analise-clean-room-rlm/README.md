# Suite de Analise Clean Room do Arkhe/RLM

Esta pasta organiza uma leitura tecnica do repositorio com foco em engenharia reversa por comportamento, QA de arquitetura e preparacao para reimplementacao clean room.

## Escopo

A leitura cobre:

- o pacote canonico `rlm/`
- a suite de testes em `tests/`
- os contratos formais em `schemas/`
- a documentacao viva em `docs/`
- os crates Rust em `native/`
- as superficies TypeScript em `packages/` e `visualizer/`
- exemplos e arquivos operacionais do topo do repositorio

## Exclusoes intencionais

Os seguintes grupos foram tratados como ruido operacional ou artefato gerado, nao como material de engenharia para clean room:

- `.venv-py313/`
- `node_modules/`
- `.next/`
- `dist/`
- `native/*/target/`
- `__pycache__/`
- `*.egg-info/`
- bancos SQLite, checkpoints e estados locais gerados em runtime

## Estrategia usada

A leitura foi distribuida por subagentes em quatro frentes:

1. `rlm/core`, `rlm/clients`, `rlm/environments`
2. `rlm/daemon`, `rlm/server`, `rlm/gateway`, `rlm/runtime`, `rlm/plugins`
3. `rlm/cli`, `rlm/tools`, `rlm/skills`, `rlm/utils`, `rlm/logger`
4. `tests`, `docs`, `schemas`, `native`, `packages`, `visualizer`, `examples`

Depois disso, os inventarios foram limpos manualmente para remover build artifacts e separar codigo canonico de legado, suporte e gerados.

## Ordem recomendada de leitura

1. `00-inventario-estrutural.md`
2. `01-core-engine-memory.md`
3. `02-daemon-gateway-server.md`
4. `03-cli-tools-skills.md`
5. `04-tests-docs-schemas-native.md`
6. `05-guia-qa-clean-room.md`

## O que esta suite entrega

- mapa estrutural por area
- inventario por pasta e por arquivo relevante
- contratos observaveis de comportamento
- hotspots de regressao, concorrencia e seguranca
- separacao entre codigo canonico, auxiliar, experimental e legado
- runbook de QA para revisar sua reimplementacao sem copiar o original

## Como usar na pratica

Se voce for recriar uma funcionalidade:

1. consulte o inventario para localizar o subsistema correto
2. extraia o contrato externo do documento tematico
3. compare sua implementacao com o comportamento, nao com os nomes internos
4. valide edge cases, seguranca e testes contra o guia final

## Nota importante de clean room

O repositorio ja mistura tres camadas historicas:

- o nucleo Python canonico
- uma migracao parcial para TypeScript
- aceleradores e futuros modulos em Rust

Uma reimplementacao saudavel nao deve imitar essa historia. Ela deve preservar o comportamento externo certo e ignorar as bifurcacoes internas que nasceram do caminho evolutivo deste repo.
