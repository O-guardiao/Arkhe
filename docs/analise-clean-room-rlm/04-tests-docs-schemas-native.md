# 04. Tests, Docs, Schemas, Native, Packages, Visualizer e Examples

## Escopo

Este documento cobre o material de validacao, contrato e suporte ao produto:

- `tests/`
- `docs/`
- `schemas/`
- `native/`
- `packages/`
- `visualizer/`
- `examples/`

## `tests/`

Arquivos mapeados na raiz:

- `conftest.py`
- `mock_lm.py`
- `README.md`
- `riemann.md`
- `riemann2.md`
- `testeriemann3.md`
- `test_backend_verification.py`
- `test_channels.py`
- `test_channel_bootstrap.py`
- `test_channel_console.py`
- `test_cli.py`
- `test_critical_gateway.py`
- `test_critical_phase10.py`
- `test_critical_phase8.py`
- `test_critical_skills.py`
- `test_critical_skills_new.py`
- `test_critical_subrlm.py`
- `test_critical_subrlm_parallel.py`
- `test_envelope_schema.py`
- `test_execution_policy.py`
- `test_gap_a_b_security.py`
- `test_gap_fixes.py`
- `test_gateway_infra.py`
- `test_handoff.py`
- `test_imports.py`
- `test_infra_phase10.py`
- `test_introspection_tools.py`
- `test_knowledge_base.py`
- `test_live_llm.py`
- `test_live_riemann.py`
- `test_live_riemann_parallel.py`
- `test_local_repl.py`
- `test_local_repl_persistent.py`
- `test_logger_runtime.py`
- `test_mcts.py`
- `test_memory_cohesion.py`
- `test_memory_corrections_s15_17.py`
- `test_message_bus.py`
- `test_multi_turn_integration.py`
- `test_obsidian_bridge.py`
- `test_obsidian_mirror.py`
- `test_optimized_backend_parity.py`
- `test_packaging.py`
- `test_parsing.py`
- `test_phase3_gateway_migration.py`
- `test_phase4_config_crosschannel.py`
- `test_phase5_auth_clients.py`
- `test_phase6_channel_discovery.py`
- `test_prompt_modes.py`
- `test_recursion_daemon.py`
- `test_recursive_accumulator.py`
- `test_role_orchestration.py`
- `test_security.py`
- `test_security_phase94.py`
- `test_service_update.py`
- `test_session_async.py`
- `test_session_contract.py`
- `test_sibling_bus.py`
- `test_sif.py`
- `test_smart_skill_delivery.py`
- `test_telegram_bridge.py`
- `test_ts_cli_shim.py`
- `test_tui.py`
- `test_tui_dual_mode.py`
- `test_types.py`
- `test_ws_gateway_protocol.py`
- `__init__.py`

Subpastas:

- `tests/clients/portkey.py`
- `tests/clients/test_gemini.py`
- `tests/clients/test_openai.py`
- `tests/repl/test_local_repl.py`

Leitura correta:

- `tests/` e um mapa das fases de migracao e dos pontos considerados criticos pelo maintainer
- o fato de existirem arquivos como `test_phase3_gateway_migration.py` e `test_gap_fixes.py` mostra que o repositorio valida milestones historicos, nao apenas unidades isoladas
- ha cobertura para gateway, session contract, role orchestration, security, memory, daemon e TUI
- ainda assim, varios cenarios de concorrencia e integracao real continuam caros ou apenas parcialmente cobertos

## `docs/`

Arquivos mapeados na raiz:

- `docs/.gitignore`
- `docs/analise-packages-ts-para-python.md`
- `docs/analise-recursao-subagentes-geracao-python.md`
- `docs/analise-rlm-velocidade-multiprocess.md`
- `docs/analise-ultimos-3-commits-rlm.md`
- `docs/arquitetura-config-multidevice.md`
- `docs/arquitetura-multichannel-unificada.md`
- `docs/comparativo-original-vs-editado.md`
- `docs/concorrencia-multi-cliente.md`
- `docs/gaps-openclaw.md`
- `docs/GATEWAY_ARCHITECTURE_REFERENCE.md`
- `docs/getting-started.md`
- `docs/horizonte-skills-agentes-sif.md`
- `docs/integracao-python-typescript-rust.md`
- `docs/logging.md`
- `docs/next.config.js`
- `docs/package-lock.json`
- `docs/package.json`
- `docs/padroes-industria-auth-multichamadas.md`
- `docs/plano-comunicacao-recursao.md`
- `docs/postcss.config.js`
- `docs/refactoring-local-repl.md`
- `docs/refactoring-sub-rlm.md`
- `docs/refatoracao_mixin_pos_mortem.md`
- `docs/rlm-runtime-reality-analysis.md`
- `docs/roadmap-mobile-ios-android.md`
- `docs/runtime-workbench-phases.md`
- `docs/seguranca-rede-e-multiconexoes.md`
- `docs/tailwind.config.ts`
- `docs/tsconfig.json`
- `docs/vscode_integration.md`

Subpastas e arquivos:

- `docs/api/rlm.md`
- `docs/architecture/global_knowledge_base.md`
- `docs/architecture/session-delivery-contract.md`
- `docs/public/teaser.png`
- `docs/public/visualizer.png`
- `docs/refatoracao/runtime-core-fase-0.md`
- `docs/src/app/globals.css`
- `docs/src/app/layout.tsx`
- `docs/src/app/page.tsx`
- `docs/src/app/api/page.tsx`
- `docs/src/app/backends/page.tsx`
- `docs/src/app/environments/page.tsx`
- `docs/src/app/environments/docker/page.tsx`
- `docs/src/app/environments/local/page.tsx`
- `docs/src/app/environments/modal/page.tsx`
- `docs/src/app/trajectories/page.tsx`
- `docs/src/components/Button.tsx`
- `docs/src/components/CodeBlock.tsx`
- `docs/src/components/Sidebar.tsx`
- `docs/src/components/Table.tsx`
- `docs/src/components/Tabs.tsx`
- `docs/src/lib/utils.ts`

Leitura correta:

- `docs/` mistura documentacao viva, notas de arquitetura, planos de refatoracao e ate um site de documentacao em Next.js
- isso e ouro para clean room, porque registra intencao e tradeoffs, mas tambem e armadilha: nem tudo ali e especificacao vigente
- documentos como `GATEWAY_ARCHITECTURE_REFERENCE.md`, `arquitetura-multichannel-unificada.md`, `rlm-runtime-reality-analysis.md` e `integracao-python-typescript-rust.md` sao particularmente valiosos

## `schemas/`

Arquivos mapeados:

- `schemas/envelope.v1.json`
- `schemas/health-report.v1.json`
- `schemas/permission-policy.v1.json`
- `schemas/README.md`
- `schemas/tool-spec.v1.json`
- `schemas/ws-protocol.v1.json`

Contrato observavel:

- esses schemas sao o melhor atalho para reconstruir interfaces externas sem copiar implementacao interna
- envelope, tool-spec, policy e health-report devem ser tratados como contratos de produto

## `native/`

Crates e arquivos mapeados:

### `native/arkhe-audit/`

- `Cargo.toml`
- `src/chain.rs`
- `src/error.rs`
- `src/lib.rs`
- `src/log.rs`

### `native/arkhe-bash/`

- `Cargo.toml`
- `src/bash.rs`
- `src/error.rs`
- `src/lib.rs`
- `src/sandbox.rs`
- `src/validation.rs`

### `native/arkhe-mcts/`

- `Cargo.lock`
- `Cargo.toml`
- `src/archive.rs`
- `src/lib.rs`
- `src/pybridge.rs`
- `src/scoring.rs`
- `src/search_replace.rs`
- `src/selection.rs`

### `native/arkhe-memory/`

- `Cargo.lock`
- `Cargo.toml`
- `src/hnsw.rs`
- `src/lib.rs`
- `src/pybridge.rs`
- `src/sparse.rs`
- `src/vecmath.rs`

### `native/arkhe-policy-core/`

- `Cargo.lock`
- `Cargo.toml`
- `src/contracts.rs`
- `src/lib.rs`
- `src/main.rs`
- `src/policy.rs`

### `native/arkhe-vault/`

- `Cargo.toml`
- `src/encryption.rs`
- `src/error.rs`
- `src/lib.rs`
- `src/vault.rs`

### `native/arkhe-wire/`

- `Cargo.lock`
- `Cargo.toml`
- `src/convert.rs`
- `src/lib.rs`
- `src/pybridge.rs`

Leitura correta:

- `arkhe-memory` e `arkhe-wire` ja aparecem como aceleradores de uso real
- `arkhe-policy-core`, `arkhe-vault` e `arkhe-audit` apontam a direcao do produto, mas nao devem ser tratados como comportamento obrigatorio ja consolidado
- `target/` foi explicitamente descartado como ruido de build

## `packages/`

### `packages/channels/`

- `package-lock.json`
- `package.json`
- `tsconfig.json`
- `vitest.config.ts`
- `src/allowlist.ts`
- `src/index.ts`
- `src/registry.ts`
- `src/state-machine.ts`
- `src/types.ts`
- `src/typing.ts`
- `src/adapters/discord.ts`
- `src/adapters/slack.ts`
- `src/adapters/webchat.ts`
- `src/adapters/whatsapp.ts`
- `tests/registry.test.ts`
- `tests/adapters/discord.test.ts`

### `packages/cli/`

- `package-lock.json`
- `package.json`
- `tsconfig.json`
- `tsup.config.ts`
- `vitest.config.ts`
- `src/checks.ts`
- `src/client.ts`
- `src/context.ts`
- `src/format.ts`
- `src/index.test.ts`
- `src/index.ts`
- `src/json-output.ts`
- `src/service-installers.ts`
- `src/service-runtime.ts`
- `src/service-update.ts`
- `src/service-wireguard.ts`
- `src/service.ts`
- `src/commands/channel.ts`
- `src/commands/client.ts`
- `src/commands/doctor.ts`
- `src/commands/health.ts`
- `src/commands/ops.ts`
- `src/commands/peer.ts`
- `src/commands/prompt.ts`
- `src/commands/repl.ts`
- `src/commands/session.ts`
- `src/commands/setup.ts`
- `src/commands/skill.ts`
- `src/commands/token.ts`
- `src/commands/tools.ts`
- `src/commands/version.ts`
- `src/lib/runtime-activity.ts`
- `src/lib/ws-client.ts`
- `src/state/diagnosis.ts`
- `src/state/launcher.ts`
- `src/state/pid.ts`
- `src/tui/activity-contract.test.ts`
- `src/tui/activity-contract.ts`
- `src/tui/ansi.ts`
- `src/tui/app.test.ts`
- `src/tui/app.ts`
- `src/tui/branch-tree.test.ts`
- `src/tui/branch-tree.ts`
- `src/tui/channel-console.ts`
- `src/tui/channel-panel.ts`
- `src/tui/events-panel.ts`
- `src/tui/footer.ts`
- `src/tui/header-panel.ts`
- `src/tui/live-api.ts`
- `src/tui/messages-panel.ts`
- `src/tui/workbench.ts`
- `src/wizard/channels.ts`
- `src/wizard/env-utils.ts`
- `src/wizard/onboarding.ts`
- `src/wizard/prompter.ts`
- `src/wizard/steps.ts`
- `tests/client.test.ts`

### `packages/config/`

- `package.json`
- `tsconfig.json`
- `vitest.config.ts`
- `src/defaults.ts`
- `src/env.ts`
- `src/index.ts`
- `src/io.ts`
- `src/legacy.ts`
- `src/schema.ts`
- `src/types.ts`
- `tests/schema.test.ts`

### `packages/daemon/`

- `package-lock.json`
- `package.json`
- `tsconfig.json`
- `vitest.config.ts`
- `src/audit.ts`
- `src/detect.ts`
- `src/diagnostics.ts`
- `src/exec-file.ts`
- `src/index.ts`
- `src/launchd.ts`
- `src/paths.ts`
- `src/schtasks.ts`
- `src/systemd.ts`
- `src/types.ts`
- `tests/paths.test.ts`
- `tests/systemd.test.ts`

### `packages/gateway/`

- `package-lock.json`
- `package.json`
- `tsconfig.json`
- `vitest.config.ts`
- `src/auth.ts`
- `src/backoff.ts`
- `src/backpressure.ts`
- `src/chunker.ts`
- `src/dedup.ts`
- `src/drain.ts`
- `src/envelope.ts`
- `src/events-ws.ts`
- `src/health.ts`
- `src/heartbeat.ts`
- `src/index.ts`
- `src/logger.ts`
- `src/openai-compat.ts`
- `src/operator.ts`
- `src/registry.ts`
- `src/scheduler.ts`
- `src/server.ts`
- `src/state-machine.ts`
- `src/webhooks.ts`
- `src/ws-bridge.ts`
- `src/adapters/discord.ts`
- `src/adapters/interface.ts`
- `src/adapters/slack.ts`
- `src/adapters/telegram.ts`
- `src/adapters/whatsapp.ts`
- `src/channels/discord.ts`
- `src/channels/slack.ts`
- `src/channels/telegram.ts`
- `src/channels/webchat.ts`
- `src/channels/whatsapp.ts`
- `tests/backoff.test.ts`
- `tests/chunker.test.ts`
- `tests/envelope.test.ts`

### `packages/server/`

- `package-lock.json`
- `package.json`
- `tsconfig.json`
- `src/app.ts`
- `src/auth.ts`
- `src/bootstrap-gateway.ts`
- `src/channel-admin.ts`
- `src/compat.ts`
- `src/http-proxy.ts`
- `src/index.ts`
- `src/logger.ts`
- `tests/app.test.ts`

### `packages/terminal/`

- `package-lock.json`
- `package.json`
- `tsconfig.json`
- `vitest.config.ts`
- `src/ansi.ts`
- `src/health-style.ts`
- `src/index.ts`
- `src/links.ts`
- `src/note.ts`
- `src/palette.ts`
- `src/progress-coordinator.ts`
- `src/progress-line.ts`
- `src/prompt-style.ts`
- `src/restore.ts`
- `src/safe-text.ts`
- `src/stream-writer.ts`
- `src/table.ts`
- `src/theme.ts`
- `tests/ansi.test.ts`
- `tests/progress-coordinator.test.ts`
- `tests/prompt-style.test.ts`
- `tests/restore.test.ts`
- `tests/safe-text.test.ts`
- `tests/table.test.ts`

Leitura correta:

- `packages/cli/` e `packages/terminal/` ainda tem valor operacional
- `packages/gateway/`, `packages/config/` e `packages/daemon/` aparecem mais como legado parcial ou trilha de migracao
- `packages/channels/` captura contratos uteis, mas nao substitui a implementacao canonica Python

## `visualizer/`

Arquivos mapeados:

- `visualizer/.gitignore`
- `visualizer/components.json`
- `visualizer/eslint.config.mjs`
- `visualizer/next.config.ts`
- `visualizer/package-lock.json`
- `visualizer/package.json`
- `visualizer/postcss.config.mjs`
- `visualizer/README.md`
- `visualizer/tsconfig.json`
- `visualizer/public/file.svg`
- `visualizer/public/globe.svg`
- `visualizer/public/next.svg`
- `visualizer/public/vercel.svg`
- `visualizer/public/window.svg`
- `visualizer/src/app/favicon.ico`
- `visualizer/src/app/globals.css`
- `visualizer/src/app/layout.tsx`
- `visualizer/src/app/page.tsx`
- `visualizer/src/components/AsciiGlobe.tsx`
- `visualizer/src/components/CodeBlock.tsx`
- `visualizer/src/components/CodeWithLineNumbers.tsx`
- `visualizer/src/components/Dashboard.tsx`
- `visualizer/src/components/ExecutionPanel.tsx`
- `visualizer/src/components/FileUploader.tsx`
- `visualizer/src/components/IterationTimeline.tsx`
- `visualizer/src/components/LogViewer.tsx`
- `visualizer/src/components/StatsCard.tsx`
- `visualizer/src/components/SyntaxHighlight.tsx`
- `visualizer/src/components/ThemeProvider.tsx`
- `visualizer/src/components/ThemeToggle.tsx`
- `visualizer/src/components/TrajectoryPanel.tsx`
- `visualizer/src/components/ui/accordion.tsx`
- `visualizer/src/components/ui/badge.tsx`
- `visualizer/src/components/ui/button.tsx`
- `visualizer/src/components/ui/card.tsx`
- `visualizer/src/components/ui/collapsible.tsx`
- `visualizer/src/components/ui/dropdown-menu.tsx`
- `visualizer/src/components/ui/resizable.tsx`
- `visualizer/src/components/ui/scroll-area.tsx`
- `visualizer/src/components/ui/separator.tsx`
- `visualizer/src/components/ui/tabs.tsx`
- `visualizer/src/components/ui/tooltip.tsx`
- `visualizer/src/lib/parse-logs.ts`
- `visualizer/src/lib/types.ts`
- `visualizer/src/lib/utils.ts`

Leitura correta:

- o visualizer e util para QA e observabilidade, mas nao deve virar referencia da arquitetura do motor
- trata-se de superficie exploratoria para trajetorias e logs

## `examples/`

Arquivos mapeados:

- `examples/daytona_repl_example.py`
- `examples/docker_repl_example.py`
- `examples/lm_in_prime_repl.py`
- `examples/lm_in_repl.py`
- `examples/modal_repl_example.py`
- `examples/prime_repl_example.py`
- `examples/quickstart.py`
- `examples/riemann_research.py`

Leitura correta:

- os exemplos mostram cenarios minimos e tambem casos demonstrativos mais ambiciosos
- `quickstart.py` e o melhor ponto de apoio para reproduzir a API como biblioteca
- `riemann_research.py` mostra o estilo de uso recursivo mais extremo

## O que preservar numa reimplementacao clean room

- uma suite de testes que valide comportamento e slices criticos
- contratos externos formais como JSON Schema
- exemplos minimos de uso e instalacao reproduzivel
- documentacao que diferencie claramente arquitetura vigente de plano futuro

## O que nao copiar

- a historia de migracao parcial para TypeScript espalhada em `packages/`
- a organizacao exata dos docs de refatoracao e analises internas
- visuais, componentes e topologia do visualizer
- backlog Rust ainda nao consolidado como comportamento obrigatorio
