# 03. CLI, Tools, Skills, Utils e Logger

## Escopo

Este documento cobre as superficies de uso local, automacao e extensao:

- `rlm/__init__.py`, `rlm/__main__.py`, `rlm/logging.py`, `rlm/session.py`
- `rlm/cli/`
- `rlm/tools/`
- `rlm/skills/`
- `rlm/utils/`
- `rlm/logger/`

## Arquivos de raiz em `rlm/`

Arquivos mapeados:

- `rlm/logging.py`
- `rlm/session.py`
- `rlm/__init__.py`
- `rlm/__main__.py`

Leitura:

- `__main__.py` e o ponto de entrada compatível para `python -m rlm`
- `session.py` e uma superficie publica de sessao, separada da implementacao em `core/session`
- a coexistencia entre branding Arkhe e namespace `rlm` e uma restricao de compatibilidade, nao um ideal arquitetural

## `rlm/cli/` raiz

Arquivos mapeados:

- `checks.py`
- `command_specs.py`
- `context.py`
- `dispatch.py`
- `json_output.py`
- `launcher_state.py`
- `main.py`
- `output.py`
- `parser.py`
- `service.py`
- `service_installers.py`
- `service_runtime.py`
- `service_update.py`
- `service_wireguard.py`
- `__init__.py`

Leitura correta:

- a CLI nao e so um wrapper de comandos; ela controla setup, status, servicos, wireguard, canais e operacao
- `command_specs.py`, `parser.py` e `dispatch.py` materializam a superficie publica do produto
- `service_runtime.py`, `service_installers.py` e `service_update.py` mostram preocupacao real com instalacao e lifecycle de daemon
- `json_output.py` indica que a CLI tambem serve a automacao, nao apenas ao uso humano

## `rlm/cli/commands/`

Arquivos mapeados:

- `channel.py`
- `client.py`
- `doctor.py`
- `ops.py`
- `peer.py`
- `setup.py`
- `skill.py`
- `token.py`
- `tui.py`
- `version.py`
- `workbench.py`
- `__init__.py`

Contrato observavel:

- comandos como setup, doctor, token, skill, tui e workbench fazem parte do comportamento publico do produto
- a CLI consegue inspecionar e controlar o runtime em vez de apenas disparar um prompt

## `rlm/cli/state/`

Arquivos mapeados:

- `diagnosis.py`
- `launcher.py`
- `pid.py`
- `__init__.py`

Leitura:

- a CLI persiste e consulta estado operacional local
- PID, diagnostico e launcher state sao parte do controle do servico, nao detalhe descartavel

## `rlm/cli/tui/`

Arquivos mapeados:

- `channel_console.py`
- `live_api.py`
- `runtime_factory.py`
- `__init__.py`

Leitura correta:

- a TUI e uma superficie importante do produto, especialmente para VPS e uso local persistente
- `live_api.py` e `runtime_factory.py` mostram o acoplamento entre TUI e o runtime/daemon real
- `channel_console.py` reforca a operacao multichannel dentro da superficie terminal

## `rlm/cli/wizard/`

Arquivos mapeados:

- `channels.py`
- `env_utils.py`
- `onboarding.py`
- `prompter.py`
- `rich_prompter.py`
- `steps.py`
- `__init__.py`

Contrato observavel:

- o produto espera um onboarding estruturado
- setup de provider, modelo, portas, tokens e daemon nao e um README manual; ele e parte da UX oficial

## `rlm/tools/`

Arquivos mapeados:

- `codebase.py`
- `critic.py`
- `embeddings.py`
- `introspection_tools.py`
- `kb_tools.py`
- `memory.py`
- `memory_tools.py`
- `session_memory_tools.py`
- `vault_tools.py`
- `__init__.py`

Leitura correta:

- tools aqui sao adaptadores concretos da infraestrutura de tools do core
- memoria, base de conhecimento, introspeccao, embeddings e vault nao sao extras cosmeticos; eles moldam a utilidade do agente
- `critic.py` sinaliza papel de autoavaliacao/critica operacional dentro do ecossistema

## `rlm/skills/`

Arquivos mapeados:

- `browser/SKILL.md`
- `calendar/SKILL.md`
- `channels/SKILL.md`
- `coding_agent/SKILL.md`
- `cross_channel_send/SKILL.md`
- `discord/SKILL.md`
- `email/SKILL.md`
- `filesystem/SKILL.md`
- `github/SKILL.md`
- `image_gen/SKILL.md`
- `maps/SKILL.md`
- `memory/SKILL.md`
- `notion/SKILL.md`
- `playwright/SKILL.md`
- `shell/SKILL.md`
- `skill_creator/SKILL.md`
- `slack/SKILL.md`
- `sqlite/SKILL.md`
- `summarize/SKILL.md`
- `telegram_bot/SKILL.md`
- `telegram_get_updates/SKILL.md`
- `travel/SKILL.md`
- `twitter/SKILL.md`
- `voice/SKILL.md`
- `weather/SKILL.md`
- `web_search/SKILL.md`
- `whatsapp/SKILL.md`
- `whisper/SKILL.md`

Leitura correta:

- skills aqui registram capacidades empacotadas, exemplos e instrucoes operacionais
- o runtime diferencia claramente implementacao de tool e empacotamento da habilidade em skill
- varias skills apontam para uso multichannel, memoria, navegacao, browser e produtividade

## `rlm/utils/`

Arquivos mapeados:

- `code_tools.py`
- `languages.py`
- `parsing.py`
- `prompts.py`
- `rlm_utils.py`
- `token_utils.py`
- `__init__.py`

Leitura:

- `prompts.py` e `token_utils.py` sao particularmente sensiveis para o comportamento do loop e do budget de contexto
- `languages.py` e `code_tools.py` mostram que o runtime foi pensado para tarefas de codigo, nao apenas conversa livre

## `rlm/logger/`

Arquivos mapeados:

- `rlm_logger.py`
- `verbose.py`
- `__init__.py`

Contrato observavel:

- logging estruturado e verbose output sao parte do produto e da operacao de QA
- trajetorias e eventos podem ser consumidos por ferramentas e por humanos

## Comportamento publico relevante

Superficies publicas observadas:

- `arkhe setup`
- `arkhe start`
- `arkhe stop`
- `arkhe status`
- `arkhe doctor`
- `arkhe update`
- `arkhe token rotate`
- `arkhe skill list/install`
- `arkhe tui`
- alias de compatibilidade `rlm`
- `python -m rlm`

Contrato observavel:

- a CLI precisa funcionar tanto de forma humana quanto scriptavel
- ha onboarding, diagnostico e operacao de servico reais
- branding Arkhe convive com namespace/env vars `rlm` por compatibilidade

Riscos de QA:

- divergencia entre TUI e estado real do daemon
- update/install quebrando ambientes por ordem errada de dependencias
- skills conflitando por nomes ou funcoes sobrepostas
- vazamento de segredos em output JSON/verbose/logs
- doctor/setup aceitando configuracao parcial demais e mascarando problema real

## Testes relacionados

Cobertura observada relevante:

- `tests/test_cli.py`
- `tests/test_tui.py`
- `tests/test_tui_dual_mode.py`
- `tests/test_channel_console.py`
- `tests/test_service_update.py`
- `tests/test_sif.py`
- `tests/test_introspection_tools.py`
- `tests/test_smart_skill_delivery.py`
- `tests/test_logger_runtime.py`
- `tests/test_ts_cli_shim.py` (marcado como codigo morto/legado pelo contexto do repositorio)

## O que preservar numa reimplementacao clean room

- uma superficie de setup e diagnostico de primeira classe
- CLI/TUI capazes de operar o runtime persistente
- separacao entre tool implementation e skill packaging
- logging e trajetorias como parte do contrato operacional
- compatibilidade de automacao via saida estruturada

## O que nao copiar

- os nomes `arkhe`, `rlm`, `doctor`, `workbench`, `skill_creator`, `channel_console`
- a divisao exata entre `wizard`, `commands`, `state` e `tui`
- a historia de migracao parcial para TypeScript refletida em aliases e shims
