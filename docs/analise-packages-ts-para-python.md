# Analise dos Packages TypeScript e Destino Python

## Tese

O diretorio `packages/` e o resto operacional de uma fase em que o projeto tentou mover CLI, gateway e frontdoor para TypeScript. Esse desenho nao e mais o centro da arquitetura.

Hoje a realidade do repositorio e esta:

- Python e o dono da recursao, do runtime e da sessao.
- Rust e camada nativa de aceleracao pontual.
- TypeScript virou historicamente uma camada de superficie operacional.

Consequencia pratica: nao faz sentido recriar `packages/` inteiro em Python como se fosse um produto novo. A maior parte ja foi recriada ou absorvida em `rlm/cli`, `rlm/gateway`, `rlm/server` e `rlm/core/comms`. O trabalho correto agora e separar:

- o que ja existe em Python e precisa apenas consolidacao,
- o que vale portar pontualmente,
- o que deve ser arquivado e removido do caminho critico.

## Diagnostico Rapido

### O que ainda estava ativo no mundo TypeScript

- `packages/cli` era o shell operacional mais completo.
- `packages/server` era um frontdoor Node que subia ou encapsulava o brain Python.
- `packages/gateway` era a borda HTTP/WebSocket dos canais, falando com o brain Python via envelope/WS.
- `packages/terminal` era a unica biblioteca interna realmente consumida por outro pacote TS.

### O que estava essencialmente morto ou isolado

- `packages/channels` nao aparece importado por nenhum outro pacote do repositorio.
- `packages/config` nao aparece importado por nenhum outro pacote do repositorio.
- `packages/daemon` nao aparece importado por nenhum outro pacote do repositorio.

Isso significa que esses tres pacotes nao sao um runtime ativo. Sao bibliotecas de tentativa, nao infraestrutura viva.

## Mapa Pacote por Pacote

| Pacote TS | Papel real | Evidencia estrutural | Destino Python | Decisao |
| --- | --- | --- | --- | --- |
| `packages/channels` | Biblioteca generica de adapters, registry, allowlist e state-machine | So referencia a si mesmo; sem consumidores externos no repo | `rlm/gateway` e `rlm/core/comms` | Nao portar 1:1. Extrair apenas ideas pequenas que faltarem |
| `packages/config` | Schema Zod + defaults + merge + env overlay para config de gateway | Sem consumidores externos; foi uma tentativa de config central TS | `rlm/core/config.py` + `rlm/cli/wizard` | Consolidar em Python; nao manter pacote espelho |
| `packages/daemon` | Manager cross-platform para systemd, launchd e schtasks | Sem consumidores externos; comportamento ja existe no CLI Python | `rlm/cli/service_runtime.py` + `rlm/cli/service_installers.py` | Ja recriado. So extrair para modulo proprio se quiser limpeza |
| `packages/gateway` | Gateway HTTP/WS de canais, webhooks, OpenAI-compat, operator bridge | Hono + ws + zod; fala com o brain Python | `rlm/gateway` + `rlm/server/api.py` + `rlm/core/comms/channel_bootstrap.py` | Ja absorvido no Python; manter TS apenas como arqueologia |
| `packages/server` | Frontdoor TS que empacota o gateway e faz proxy para o brain Python | Importa internals de `../../gateway/src/*`; nao e backend autonomo | `rlm/server/api.py` + `rlm/cli/service_runtime.py` | Obsoleto na arquitetura Python-first |
| `packages/terminal` | Utilitarios de terminal para CLI/TUI TS | E o unico pacote interno consumido por outro TS (`packages/cli`) | `rich`, helpers de `rlm/cli/output.py`, opcional `prompt_toolkit` | Nao precisa porta literal; precisa equivalentes ergonomicos |
| `packages/cli` | CLI, wizard, TUI, launcher e operacao do frontdoor TS | Era o shell Node da fase TS-first | `rlm/cli` | Em grande parte ja recriado em Python |

## Leitura Estrutural por Pacote

### `packages/server`

Esse pacote nao e um servidor independente no sentido arquitetural. Ele e um embrulho.

Sinais disso:

- `packages/server/src/bootstrap-gateway.ts` importa diretamente modulos de `../../gateway/src/*`.
- `packages/server/src/index.ts` sobe um processo Python se o brain nao estiver de pe.
- `packages/server/src/app.ts` faz proxy de rotas para o upstream Python e delega o resto para `gatewayApp.fetch(...)`.

Traduzindo: `packages/server` era um supervisor Node com fachada HTTP. Isso nao e valor de dominio. E so uma camada de operacao que hoje deve viver em Python.

Destino correto:

- processo principal em `rlm/server/api.py`,
- bootstrap multicanal em `rlm/core/comms/channel_bootstrap.py`,
- operacao de lifecycle em `rlm/cli/service_runtime.py`.

### `packages/gateway`

Esse era o pacote mais importante da fase TS. Ele contem:

- envelope tipado,
- ponte WebSocket com o brain,
- webhooks dos canais,
- `/v1/chat/completions` compat OpenAI,
- operator bridge,
- `/events` para TUI.

Tudo isso ja existe semanticamente no lado Python:

- `rlm/gateway/envelope.py`
- `rlm/gateway/ws_gateway_endpoint.py`
- `rlm/gateway/webhook_dispatch.py`
- `rlm/gateway/operator_bridge.py`
- `rlm/server/openai_compat.py`
- `rlm/server/api.py`

O ponto importante nao e “portar gateway TS”. Isso ja foi feito. O ponto agora e eliminar as ultimas dependencias operacionais do caminho TS, para que o gateway canonicamente Python seja a unica borda oficial.

### `packages/cli`

Foi o pacote TS mais abrangente. Ele tentava fazer quatro coisas ao mesmo tempo:

- CLI de usuario,
- launcher do runtime,
- wizard/onboarding,
- TUI/workbench.

Mas o espelhamento para Python ja aconteceu em grande parte:

- `packages/cli/src/service-runtime.ts` -> `rlm/cli/service_runtime.py`
- `packages/cli/src/service.ts` -> `rlm/cli/service.py`
- `packages/cli/src/state/launcher.ts` -> `rlm/cli/state/launcher.py`
- `packages/cli/src/state/diagnosis.ts` -> `rlm/cli/state/diagnosis.py`
- `packages/cli/src/wizard/onboarding.ts` -> `rlm/cli/wizard/onboarding.py`
- `packages/cli/src/wizard/channels.ts` -> `rlm/cli/wizard/channels.py`
- `packages/cli/src/tui/live-api.ts` -> `rlm/cli/tui/live_api.py`
- `packages/cli/src/commands/*` -> `rlm/cli/commands/*`

O que ainda vale reaproveitar daqui nao e a estrutura do pacote. Sao alguns detalhes de UX:

- normalizacao de payload de atividade,
- pequenas escolhas de layout de TUI,
- convencoes de status operacional.

### `packages/terminal`

Esse pacote nao carrega semantica de dominio. Ele e um kit de apresentacao:

- ANSI,
- tabela,
- prompt style,
- progress line,
- safe text,
- restore de terminal.

No Python, a porta literal disso seria desperdicio. O ecossistema Python ja resolve essa camada melhor com:

- `rich` para tabelas, estilo e painel,
- `prompt_toolkit` se o prompt interativo precisar subir de nivel,
- `textual` apenas se houver decisao explicita de TUI rica e persistente.

Como o repositorio ja depende de `rich`, a decisao mais eficiente e:

- usar `rich` como substituto principal,
- manter helpers pequenos em `rlm/cli/output.py` ou criar `rlm/cli/terminal.py` se a superficie crescer.

### `packages/channels`

Esse pacote e conceitualmente interessante, mas operacionalmente morto.

Ele define:

- `ChannelAdapter`,
- `ChannelRegistry`,
- `ChannelStateMachine`,
- `allowlist`,
- adapters simples de Discord/Slack/WhatsApp/Webchat.

Problema: ele nao e a base real do gateway TS ativo. O pacote `packages/gateway` reimplementa sua propria interface de adapter e seus proprios handlers de canal. Entao `packages/channels` nao era a fundacao; era uma tentativa paralela.

Destino correto:

- nao recriar o pacote como espelho Python,
- aproveitar apenas microideias reutilizaveis que faltarem no `rlm/gateway`,
- manter a fonte de verdade dos canais em `rlm/gateway` e `rlm/core/comms`.

### `packages/config`

Esse pacote faz schema, defaults, merge e overlay de env. A ideia e correta; o encaixe original nao.

No Python ja existe um alvo mais coerente:

- `rlm/core/config.py` para configuracao estruturada via `rlm.toml` + env,
- `rlm/cli/wizard/env_utils.py` e `steps.py` para captura e escrita do `.env`,
- `rlm/server/api.py` para consumo operacional das variaveis.

O trabalho necessario aqui nao e criar `rlm/config/` copiando o TS. E consolidar o que ainda esta espalhado entre `core.config`, wizard e leitura ad hoc de env vars no server.

### `packages/daemon`

Ele oferece:

- deteccao de plataforma,
- arquivos de service para systemd/launchd/schtasks,
- auditoria JSONL,
- diagnostico de ambiente.

No Python, o grosso do comportamento ja esta implementado em:

- `rlm/cli/service_runtime.py`
- `rlm/cli/service.py`
- `rlm/cli/service_installers.py`

Se houver desejo de limpeza arquitetural, o movimento correto nao e “portar daemon TS”. E extrair a logica Python existente para um novo namespace, por exemplo `rlm/daemon/`.

## Dependencias Python Reais por Area

O repositorio ja tem quase tudo que precisa no `pyproject.toml`.

### Borda HTTP, WS e canais

- `fastapi`
- `uvicorn`
- `websockets`
- `httpx`
- `requests`
- `python-dotenv`

Mapeamento direto dos equivalentes TS:

- `hono` / `@hono/node-server` -> `fastapi` + `uvicorn`
- `ws` -> `websockets` e WebSocket do FastAPI/Starlette
- `node-fetch` -> `httpx` ou `requests`
- `zod` -> dataclasses, Pydantic via FastAPI e validacao especifica ja existente
- `pino` -> `rlm.core.structured_log`

### CLI e UX de terminal

- `rich` ja cobre a maior parte do que `chalk`, `ora` e parte de `@arkhe/terminal` faziam.

Opcional apenas se o produto pedir:

- `prompt_toolkit` para prompt interativo mais sofisticado,
- `textual` somente se a TUI Python for virar uma aplicacao rica de longa duracao.

### Configuracao

- `python-dotenv`
- `tomllib` nativo do Python 3.11+
- `jsonschema` quando houver necessidade de validar contratos externos

Nao ha justificativa tecnica para adicionar um equivalente Python de `zod` so para imitar a fase TS.

## O Que Ja Esta Recriado em Python

### Ja consolidado ou muito perto disso

- gateway canonicamente Python em `rlm/gateway`
- server principal Python em `rlm/server`
- infraestrutura multicanal em `rlm/core/comms`
- CLI Python em `rlm/cli`
- wizard Python em `rlm/cli/wizard`
- TUI/live API Python em `rlm/cli/tui`
- configuracao estruturada em `rlm/core/config.py`

### Ainda espalhado e pedindo consolidacao

- leitura de env vars em `rlm/server/api.py`
- aspectos de config entre `core.config` e wizard
- ergonomia de terminal ainda fragmentada entre `output.py`, Rich e codigo de comando

## Decisao de Recriacao

### Nao recriar como pacote Python espelho

- `packages/server`
- `packages/gateway`
- `packages/cli`
- `packages/channels`
- `packages/config`
- `packages/daemon`

Razao: o equivalente funcional ja existe ou o design original nao era a forma correta de expressar o sistema Python-first.

### Reaproveitar apenas comportamento util

- `packages/cli/src/lib/runtime-activity.ts`
  - manter a ideia de normalizacao, mas com contrato Python canonico vindo de `operator_surface.py`
- `packages/terminal`
  - portar so UX pequena que ainda faltar, nunca o pacote inteiro
- `packages/channels/src/allowlist.ts`
  - portar apenas se realmente faltar uma allowlist simples no gateway Python

## Plano Prioritario

### P0

Congelar `packages/server`, `packages/gateway`, `packages/channels`, `packages/config` e `packages/daemon` como legado arquitetural. Eles nao devem voltar ao caminho critico.

### P1

Trocar qualquer dependencia operacional restante de `packages/server` no launcher por caminho Python canonico.

### P2

Consolidar configuracao Python:

- `rlm/core/config.py` como centro,
- wizard escrevendo os mesmos nomes/campos usados pelo runtime,
- reduzir leitura ad hoc de env espalhada no server onde isso for seguro.

### P3

Decidir se vale extrair dois namespaces novos:

- `rlm/daemon/` para lifecycle cross-platform,
- `rlm/cli/terminal.py` para helpers visuais pequenos em cima de `rich`.

### P4

Remover o valor psicologico do TS legado: documentar claramente que `packages/` e referencia historica, nao direcao futura.

## Conclusao Brutal

O erro seria tratar `packages/` como backlog de porta para Python. Isso repete uma arquitetura abandonada.

O movimento correto e outro:

- reconhecer que o Python ja retomou CLI, gateway e server,
- consolidar o que ainda esta espalhado,
- matar as duplicacoes TypeScript que sobraram,
- portar so microcomportamentos de UX ou utilitarios realmente uteis.

Em resumo:

- `packages/cli` -> quase todo ja vive em `rlm/cli`
- `packages/gateway` -> ja vive em `rlm/gateway` + `rlm/server`
- `packages/server` -> virou redundante
- `packages/terminal` -> substituir por `rich` e helpers pequenos
- `packages/config` -> fundir em `rlm/core/config.py`
- `packages/daemon` -> ja existe em `rlm/cli/service_*`
- `packages/channels` -> arquivar; nao era a fundacao real