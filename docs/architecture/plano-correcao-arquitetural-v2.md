# Plano de Correção Arquitetural RLM — v2

> Documento executivo. Produzido a partir de auditoria de código real contra  
> clean-room de 4 repositórios (RLM, VS Code, claw-code, PicoClaw).  
> Baseline: 241 arquivos Python em `rlm/`, 2 134 testes coletados, 0 violações de camada.

---

## 1. Mandato Técnico

**Congele expansão. Consolide ownership. Elimine duplicações estruturais. Corrija contratos. Só depois expanda.**

Nenhuma feature nova de superfície, nenhum novo canal, nenhum novo plugin de dispositivo até que as Fases 0–3 estejam concluídas. Toda energia vai para consolidar o que existe. O RLM já provou (refactor de `local_repl.py`, refactor de `sub_rlm.py`, fix do flood Telegram) que simplificação estrutural não quebra capacidade — **aumenta confiabilidade**. Generalize essa disciplina para toda a base.

---

## 2. Decisões Arquiteturais Obrigatórias

| # | Decisão | Justificativa |
|---|---------|---------------|
| D1 | **Python é o dono canônico do runtime recursivo**. Não existe co-ownership com TS. | O TypeScript em `packages/` é legado. O runtime recursivo, REPL, subagentes, memória — tudo é Python. |
| D2 | **Rust entra SOMENTE para hot-path medido ou domínio de alta garantia** (policy, vault, audit). | claw-code inverte isso (Rust canônico, Python auxiliar). RLM é o oposto: Python canônico, Rust pontual. Não criar terceira arquitetura. |
| D3 | **`packages/` é legado explícito, fora do caminho crítico**. | 193 fontes TS/JS/Python (excl. node_modules) que não ampliam capacidade. LEGACY.md já existe mas código continua acessível. |
| D4 | **Um único dono canônico por conceito central**: sessão, envelope, dispatch, config, projeção de runtime, sibling bus. | Auditoria confirmou: 3 envelopes, 2 sibling_bus, 2+ caminhos de dispatch, porta padrão conflitante (5000 vs 8000). |
| D5 | **Superfícies humanas (CLI, TUI, dashboard, operator) são consumidoras do runtime — não intérpretes dele.** | brain_api.py + ws_gateway_endpoint.py usam `Envelope` (gateway); api.py + webhook_dispatch.py usam `dispatch_runtime_prompt_sync` direto. Precisa convergir. |
| D6 | **Canais humanos, multimodalidade e dispositivos são subsistemas explícitos, não mistura informal.** | Hoje `plugins/browser.py` + `skills/browser/` coexistem. `tools/memory.py` + `skills/memory/` coexistem. Sem fronteira formal. |
| D7 | **Todo contrato cross-layer nasce com schema JSON, testes e versionamento.** | `schemas/` já tem 7 schemas v1. Falta SessionIdentity, DeviceCommand, DeviceTelemetry, MediaArtifact. |
| D8 | **Todo shim de compatibilidade tem prazo de remoção e não pode bloquear teste verde.** | `comms/sibling_bus.py` (shim) gera DeprecationWarning em 1 teste. `gateway/operator_bridge.py` (shim) importa de `server`. |

---

## 3. Arquitetura Alvo Executável

### L0 — Núcleo de Raciocínio e Contexto (`rlm.core`)

| Aspecto | Especificação |
|---------|---------------|
| **Possui** | Loop recursivo, REPL persistente, spawn de subagentes, ledger de tarefas/branches, composição de contexto, memória de trabalho, recall, políticas de execução, SiblingBus canônico, MessageBus, Outbox, DeliveryWorker |
| **NÃO possui** | Webhook, CLI, TUI, canal externo, dispositivo específico, UI |
| **Pode depender de** | `rlm.runtime` (contratos), bibliotecas Python (stdlib, pydantic, etc.) |
| **NÃO pode depender de** | `rlm.gateway`, `rlm.server`, `rlm.daemon`, `rlm.cli`, `rlm.plugins`, `rlm.skills` |

### L0.5 — Sessão e Identidade (`rlm.core.session`)

| Aspecto | Especificação |
|---------|---------------|
| **Possui** | SessionManager canônico, SessionIdentity (tipo formal), SessionRecord, ciclo de vida de sessão, transcript, client_registry, send_policy |
| **NÃO possui** | Lógica de transporte, formatação de mensagem, UI |
| **Pode depender de** | `rlm.core` (config, auth, types) |
| **NÃO pode depender de** | `rlm.gateway`, `rlm.server` (VERIFICADO: já correto) |

### L1 — Gateway: Canais e Conversa (`rlm.gateway`)

| Aspecto | Especificação |
|---------|---------------|
| **Possui** | Normalização de ingressos (Telegram, Discord, Slack, WhatsApp, webchat, WebSocket), um único `InboundMessage` canônico, chunking, retry, resolução de identidade de sessão |
| **NÃO possui** | Semântica interna do REPL, dispatch para o runtime, endpoint HTTP, operator bridge |
| **Pode depender de** | `rlm.core` (contratos de sessão, envelope) |
| **NÃO pode depender de** | `rlm.server`, `rlm.daemon`, `rlm.cli` |

### L2 — Server: Runtime Operacional (`rlm.server`)

| Aspecto | Especificação |
|---------|---------------|
| **Possui** | FastAPI app, `dispatch_runtime_prompt_sync` (ponto único de dispatch), runtime_pipeline, brain_api, brain_router, webhook_dispatch, openai_compat, operator_bridge, ws_server, health_monitor, backpressure, drain, scheduler |
| **NÃO possui** | Lógica de normalização de canal, lógica de core engine, UI |
| **Pode depender de** | `rlm.core`, `rlm.gateway` |
| **NÃO pode depender de** | `rlm.daemon`, `rlm.cli` |

### L3 — Daemon: Persistência e Lifecycle (`rlm.daemon`)

| Aspecto | Especificação |
|---------|---------------|
| **Possui** | recursion_daemon, warm_runtime, llm_gate, task_agents, channel_subagents, memory_access, daemon contracts |
| **NÃO possui** | HTTP server, CLI, UI |
| **Pode depender de** | `rlm.core`, `rlm.gateway`, `rlm.server` (apenas contratos) |
| **NÃO pode depender de** | `rlm.cli` |

### L4 — CLI e Superfícies Humanas (`rlm.cli`)

| Aspecto | Especificação |
|---------|---------------|
| **Possui** | Comandos CLI, TUI/workbench, wizard, service_runtime |
| **NÃO possui** | Lógica de engine, lógica de sessão, normalizadores de canal |
| **Pode depender de** | Todas as camadas abaixo |
| **Consome** | `RuntimeProjection` via contrato oficial (não parsing ad-hoc) |

### Subsistemas Transversais (FUTUROS — Fase 4)

| Subsistema | Localização Alvo | Responsabilidade |
|------------|------------------|------------------|
| **Percepção e Mídia** | `rlm.perception/` | Pipeline: ingestão → armazenamento → análise → extração estruturada → publicação para core. Áudio, imagem, OCR, ASR, visão. |
| **Dispositivos e Automação** | `rlm.devices/` | Registry de capabilities, catálogo de dispositivos, contratos de telemetria, comandos idempotentes, timeout, segurança. Drones, robôs, câmeras, ESP32, atuadores. |

---

## 4. Mapa de Consolidação — Evidência Real

### 4.1 MANTER

| Item | Localização | Razão |
|------|-------------|-------|
| Núcleo Python recursivo | `rlm/core/engine/` | É o motor. 20+ módulos coesos após refactor de sub_rlm. |
| MessageBus + Outbox + DeliveryWorker | `rlm/core/comms/` | Pipeline multichannel funcional confirmado |
| RuntimeProjection + operator_surface | `rlm/core/observability/operator_surface.py` → `rlm/runtime/contracts.py` | Contrato v1 já estabelecido, schema versionado |
| SessionManager canônico | `rlm/core/session/_impl.py:87` | Classe `SessionManager` com SQL, lifecycle, transcript. Funcional. |
| Layer checker CI | `scripts/check_layer_imports.py` | 0 violações. Rodando em CI. |
| Schemas versionados | `schemas/` (7 schemas v1) | envelope, health-report, permission-policy, runtime-projection, tool-spec, ws-protocol |
| Daemon persistente | `rlm/daemon/` | 8 módulos, contratos definidos, warm_runtime funcional |

### 4.2 REFATORAR

| Item | Problema | Ação |
|------|----------|------|
| **Ownership de dispatch** | `api.py` chama `dispatch_runtime_prompt_sync` direto (L629). `brain_api.py` tem `dispatch_prompt` que usa `Envelope`. `ws_gateway_endpoint.py` chama `brain_api.dispatch_prompt`. `webhook_dispatch.py` chama `dispatch_runtime_prompt_sync`. `telegram_gateway.py` chama via SessionManager. → **5 caminhos para o mesmo runtime.** | Convergir para um único `dispatch_runtime_prompt_sync` com builder de contexto padronizado. brain_api.dispatch_prompt deve ser wrapper que converte Envelope → parâmetros padronizados. |
| **Porta padrão** | `rlm/core/config.py:124` → fallback 8000. `rlm/server/api.py:1162` → default 5000. `rlm/cli/context.py:138` → default 5000. **Três defaults conflitantes.** | Um único default (5000, que é o mais usado) em `core/config.py`. Todos os outros leem de lá. |
| **tools/ vs skills/ vs plugins/** | `tools/memory.py` + `skills/memory/` + `plugins/browser.py` + `skills/browser/`. Implementação, empacotamento e descoberta se sobrepõem. | Definir: tools = funções do core, skills = pacotes instaláveis, plugins = adaptadores de integração. Eliminar sobreposições de nome. |
| **session_id + client_id ad-hoc** | Passados como strings soltas em 30+ pontos. Sem tipo formal `SessionIdentity`. | Criar `SessionIdentity` dataclass em `core/session/session_key.py` (arquivo já existe). Tipar todos os call sites. |

### 4.3 EXTRAIR

| Item | De onde | Para onde |
|------|---------|-----------|
| Percepção multimodal | `plugins/audio.py`, `plugins/browser.py`, `skills/voice/`, `skills/browser/`, `skills/whisper/`, `skills/image_gen/` | `rlm/perception/` — subsistema com pipeline formal |
| Orquestração de dispositivos | Hoje não existe como subsistema | `rlm/devices/` — registry, capabilities, commands, telemetry |
| Schemas de contrato novos | Espalhados como dicts | `schemas/session-identity.v1.json`, `schemas/device-command.v1.json`, `schemas/media-artifact.v1.json` |

### 4.4 CONSOLIDAR

| Conceito | Estado Atual | Ação |
|----------|-------------|------|
| **SiblingBus** | `core/comms/sibling_bus.py` (SHIM, deprecated) + `core/orchestration/sibling_bus.py` (canônico). 22 imports usam o canônico. 1 teste usa o shim. | **Deletar o shim.** Corrigir o 1 teste (`test_local_repl_persistent.py:11`). |
| **Envelope** | 3 tipos: `core/comms/envelope.py` (bus routing: Direction, Envelope, MessageType), `gateway/message_envelope.py` (InboundMessage: normalização), `gateway/envelope.py` (schema v1: WS bridge TypeScript). | **Manter 2, aposentar 1.** `InboundMessage` → ingresso. `comms.Envelope` → bus routing. `gateway/envelope.py` → absorver em `core/comms/envelope.py` quando TS gateway morrer. Documentar papel de cada um. |
| **operator_bridge** | `gateway/operator_bridge.py` (SHIM) → re-export de `server/operator_bridge.py` | **Deletar o shim.** Atualizar 4 imports em testes (`test_tui_dual_mode.py:179,195,206,219`). |
| **Config source of truth** | `core/config.py` é canônico. Porta default conflitante entre config (8000), api.py (5000), cli (5000). | **Unificar em 5000 em `core/config.py`.** Todos os outros leem de `load_config()`. |
| **webhook_dispatch ownership** | Existe em `rlm/gateway/webhook_dispatch.py` (canônico main) E `rlm/server/webhook_dispatch.py` (worktree). | **Confirmar canônico em `server/` conforme hierarquia de camadas.** `gateway/` não deve ter dispatch — apenas normalização. |

### 4.5 APOSENTAR

| Item | Localização | Razão |
|------|-------------|-------|
| **packages/ inteiro** | `packages/` (193 fontes TS/JS/Python excl. node_modules) | LEGACY.md já existe. Nenhum import canônico vem daqui. Cortar do CI e .gitignore de linting. |
| **Arquivos .bak/.new** | `rlm/core/engine/sub_rlm.py.bak`, `sub_rlm.py.new`, `rlm/environments/local_repl.py.bak` | **Deletar imediatamente.** Não são contrato, não são referência, contaminam a leitura. |
| **comms/sibling_bus.py shim** | `rlm/core/comms/sibling_bus.py` | Shim com DeprecationWarning. Ninguém do runtime usa. |
| **gateway/operator_bridge.py shim** | `rlm/gateway/operator_bridge.py` | Shim para `server/operator_bridge.py`. |
| **Testes de migração/fase** | Testes nomeados `test_phase*` que testam transições já completadas | Avaliar caso a caso na Fase 5. Se o teste cobre invariante real, renomear. Se só testa transição morta, deletar. |

---

## 5. Sequência de Execução por Fases

### Fase 0 — Congelamento de Direção

| Campo | Valor |
|-------|-------|
| **Objetivo** | Declarar dono canônico de cada domínio. Parar split-brain. |
| **Problema que resolve** | Ambiguidade estratégica: o que é Python, o que é TS, o que é Rust. |
| **Artefatos afetados** | `docs/`, `README.md`, `CONTRIBUTING.md`, ADRs, `LEGACY.md` |
| **Risco** | Baixo. É governança, não código. |
| **Ganho** | Fim da ambiguidade. Toda decisão futura tem critério. |
| **Condição para começar** | Nenhuma. Pode começar agora. |
| **Critério de pronto** | Documentação publicada. ADR-004 declarando Python canônico, packages legado, Rust pontual. |

### Fase 1 — Eliminação de Duplicações Canônicas

| Campo | Valor |
|-------|-------|
| **Objetivo** | Deletar shims, .bak/.new, e unificar conceitos com dono duplicado. |
| **Problema que resolve** | 2 sibling_bus, 2 operator_bridge shims, 3 .bak/.new, porta conflitante. |
| **Artefatos afetados** | `core/comms/sibling_bus.py`, `gateway/operator_bridge.py`, `core/engine/sub_rlm.py.bak`, `.new`, `environments/local_repl.py.bak`, `core/config.py`, `server/api.py` |
| **Risco** | Baixo-médio. Shims podem ter consumidores ocultos (grep confirma que não). |
| **Ganho** | Menos ruído cognitivo. Zero DeprecationWarning em testes. |
| **Condição para começar** | Fase 0 declarada. |
| **Critério de pronto** | 0 shims, 0 .bak/.new, porta padrão unificada, testes verdes, 0 violações de camada. |

### Fase 2 — Unificação de Contratos

| Campo | Valor |
|-------|-------|
| **Objetivo** | Criar `SessionIdentity`, documentar papel de cada Envelope, criar schemas faltantes. |
| **Problema que resolve** | `session_id` + `client_id` como strings soltas. 3 tipos de envelope sem documentação formal de papel. Contratos de dispositivo e mídia inexistentes. |
| **Artefatos afetados** | `core/session/session_key.py`, `schemas/`, `core/comms/envelope.py`, `gateway/message_envelope.py`, `gateway/envelope.py` |
| **Risco** | Médio. Muitos call sites a atualizar para `SessionIdentity`. |
| **Ganho** | Fronteiras claras. Superfícies param de inferir. |
| **Condição para começar** | Fase 1 concluída. |
| **Critério de pronto** | `SessionIdentity` tipado em todos os call sites. Cada envelope com docstring declarando papel. Schemas novos em `schemas/`. |

### Fase 3 — Consolidação de Dispatch e Ingressos

| Campo | Valor |
|-------|-------|
| **Objetivo** | Convergir os 5 caminhos de dispatch para o runtime em um único pipeline padronizado. |
| **Problema que resolve** | `api.py` → direto, `brain_api.py` → via Envelope, `ws_gateway_endpoint` → via brain_api, `webhook_dispatch` → direto, `telegram_gateway` → via SessionManager. Mesma operação, 5 caminhos. |
| **Artefatos afetados** | `server/api.py`, `server/brain_api.py`, `server/webhook_dispatch.py`, `server/ws_gateway_endpoint.py`, `gateway/telegram_gateway.py`, `server/runtime_pipeline.py` |
| **Risco** | Alto. Toca o caminho crítico em produção. |
| **Ganho** | Previsibilidade operacional. Todo ingresso → mesmo pipeline → mesmo contexto → mesmo dispatch. |
| **Condição para começar** | Fase 2 concluída. SessionIdentity disponível como tipo. |
| **Critério de pronto** | Toda entrada passa por `dispatch_runtime_prompt_sync` com `SessionIdentity` + contexto padronizado. Testes de integração cobrem todos os 5 pontos de entrada. |

### Fase 4 — Explicitação de Multimodalidade e Dispositivos

| Campo | Valor |
|-------|-------|
| **Objetivo** | Criar `rlm/perception/` e `rlm/devices/` como subsistemas formais. |
| **Problema que resolve** | Áudio, imagem, browser, sensores vivem como plugins soltos em `plugins/` e `skills/`. Não há distinção entre transporte humano, percepção e automação. |
| **Artefatos afetados** | `plugins/audio.py`, `plugins/browser.py`, `skills/voice/`, `skills/browser/`, `skills/whisper/`, `skills/image_gen/` |
| **Risco** | Médio. Movimentação de código + atualização de imports. |
| **Ganho** | Câmera, drone, robô, áudio e imagem entram com a mesma disciplina de contratos que chat. |
| **Condição para começar** | Fases 0–3 concluídas. |
| **Critério de pronto** | `rlm/perception/` existe com pipeline formal. `rlm/devices/` existe com registry + contracts. Schemas de `DeviceCommand`, `DeviceTelemetry`, `MediaArtifact` publicados. |

### Fase 5 — Deleção de Legado e Hardening

| Campo | Valor |
|-------|-------|
| **Objetivo** | Remover `packages/` do repositório (ou mover para branch `legacy/`), limpar testes de migração, reforçar validação de camadas. |
| **Problema que resolve** | 193 fontes TS/JS/Python que não entregam capacidade mas custam cognição e CI time. |
| **Artefatos afetados** | `packages/`, `tests/test_phase*`, `.github/workflows/`, layer checker |
| **Risco** | Médio. Deletar 193 arquivos requer confirmação. |
| **Ganho** | Redução real de volume. CI mais rápido. Menos ruído. |
| **Condição para começar** | Fases 0–4 concluídas. Zero dependência canônica em `packages/`. |
| **Critério de pronto** | `packages/` removido de main. Testes renomeados de `test_phase*` para nomes de invariante. CI com check de camada cobrindo novos subsistemas. |

---

## 6. Backlog Inicial de Execução — 15 Tarefas

### T01 — Publicar ADR-004: Python Canônico

| Campo | Valor |
|-------|-------|
| Objetivo | Declarar: Python dono do runtime, packages legado, Rust pontual |
| Área | `docs/architecture/adrs/` |
| Dependência | Nenhuma |
| Risco | Zero |
| Resultado | Base autoritativa para todas as decisões futuras |

### T02 — Deletar arquivos .bak/.new

| Campo | Valor |
|-------|-------|
| Objetivo | Remover `sub_rlm.py.bak`, `sub_rlm.py.new`, `local_repl.py.bak` |
| Área | `rlm/core/engine/`, `rlm/environments/` |
| Dependência | Nenhuma |
| Risco | Zero (não são importados por nada) |
| Resultado | Diretório limpo, sem artefatos de trabalho em caminho crítico |

### T03 — Deletar shim `core/comms/sibling_bus.py`

| Campo | Valor |
|-------|-------|
| Objetivo | Remover shim deprecated. Atualizar 1 teste (`test_local_repl_persistent.py:11`) para importar de `core/orchestration/sibling_bus` |
| Área | `rlm/core/comms/`, `tests/` |
| Dependência | T01 |
| Risco | Baixo (grep confirma: 0 imports canônicos desse caminho) |
| Resultado | Zero DeprecationWarning. Um único SiblingBus. |

### T04 — Deletar shim `gateway/operator_bridge.py`

| Campo | Valor |
|-------|-------|
| Objetivo | Remover shim. Atualizar 4 imports em `test_tui_dual_mode.py` (L179,195,206,219) para `rlm.server.operator_bridge` |
| Área | `rlm/gateway/`, `tests/` |
| Dependência | T01 |
| Risco | Baixo |
| Resultado | Sem re-export cross-layer desnecessário |

### T05 — Unificar porta padrão

| Campo | Valor |
|-------|-------|
| Objetivo | Fixar default 5000 em `core/config.py`. Eliminar fallback `RLM_PORT` → `8000`. Fazer `server/api.py:1162` ler de `load_config()`. |
| Área | `rlm/core/config.py`, `rlm/server/api.py` |
| Dependência | T01 |
| Risco | Baixo (5000 já é o default operacional nos scripts) |
| Resultado | Uma única fonte de verdade para porta |

### T06 — Inventário fechado de donos duplicados

| Campo | Valor |
|-------|-------|
| Objetivo | Produzir tabela: conceito → arquivo canônico → arquivo duplicado → status → ação. Cobrir: session, envelope, dispatch, config, auth, sibling_bus, operator_bridge, channel_registry |
| Área | `docs/architecture/` |
| Dependência | T01 |
| Risco | Zero |
| Resultado | Mapa completo de duplicações para guiar T07–T15 |

### T07 — Documentar papel de cada Envelope

| Campo | Valor |
|-------|-------|
| Objetivo | Adicionar docstrings formais em cada envelope declarando: (1) `InboundMessage` = ingresso normalizador, (2) `comms.Envelope` = bus routing, (3) `gateway/envelope.py` = WS protocol TS↔Python. Criar ADR-005 Envelope Pipeline. |
| Área | `gateway/message_envelope.py`, `core/comms/envelope.py`, `gateway/envelope.py`, `docs/` |
| Dependência | T06 |
| Risco | Baixo |
| Resultado | Clareza sobre quem é quem no pipeline de mensagem |

### T08 — Criar tipo SessionIdentity

| Campo | Valor |
|-------|-------|
| Objetivo | Criar `SessionIdentity` dataclass em `core/session/session_key.py` com campos: `session_id: str`, `client_id: str`, `user_id: str | None`, `channel: str | None`, `device_id: str | None`. Publicar schema em `schemas/session-identity.v1.json`. |
| Área | `rlm/core/session/`, `schemas/` |
| Dependência | T06 |
| Risco | Médio (muitos call sites) |
| Resultado | Tipo formal para identidade de sessão |

### T09 — Migrar `gateway/webhook_dispatch.py` → `server/webhook_dispatch.py`

| Campo | Valor |
|-------|-------|
| Objetivo | Confirmar ownership canônico em `server/` (L2), conforme hierarquia. `gateway/` (L1) só normaliza. Dispatch pertence ao server. |
| Área | `rlm/gateway/`, `rlm/server/` |
| Dependência | T06 |
| Risco | Médio (imports em api.py e testes usam `rlm.gateway.webhook_dispatch`) |
| Resultado | Dispatch no layer correto |

### T10 — Congelar `packages/` em CI

| Campo | Valor |
|-------|-------|
| Objetivo | Adicionar regra no CI: qualquer import de `packages/` em código canônico (`rlm/`) falha o build. Atualizar LEGACY.md com data de sunset. |
| Área | `.github/workflows/`, `packages/LEGACY.md` |
| Dependência | T01 |
| Risco | Baixo |
| Resultado | packages/ isolado formalmente |

### T11 — Mapear todos os caminhos de dispatch

| Campo | Valor |
|-------|-------|
| Objetivo | Documentar os 5 caminhos confirmados: (1) api.py:629 → dispatch direto, (2) brain_api.dispatch_prompt → via Envelope, (3) ws_gateway_endpoint:294 → via brain_api, (4) webhook_dispatch:346 → dispatch direto, (5) telegram_gateway → via SessionManager. Propor convergência. |
| Área | `docs/architecture/` |
| Dependência | T06 |
| Risco | Zero (é documentação) |
| Resultado | Mapa visual do dispatch. Base para refatoração da Fase 3. |

### T12 — Criar suíte de invariantes

| Campo | Valor |
|-------|-------|
| Objetivo | 4 trilhas de teste: (1) alto contexto persistente, (2) multicanal com retry, (3) rotas determinísticas vs LLM, (4) coordenação simultânea. Estes testes NÃO são de fase — são de invariante do produto. |
| Área | `tests/test_invariants_*.py` |
| Dependência | T01 |
| Risco | Baixo |
| Resultado | Bússola de qualidade baseada em comportamento, não em migração |

### T13 — Resolver sobreposição tools/memory vs skills/memory

| Campo | Valor |
|-------|-------|
| Objetivo | `tools/memory.py` = funções do core para LLM (function calling). `skills/memory/` = pacote instalável. Definir fronteira e eliminar duplicação de capacidade. |
| Área | `rlm/tools/`, `rlm/skills/memory/` |
| Dependência | T06 |
| Risco | Médio |
| Resultado | Uma única cadeia de ownership para memória exposta ao LLM |

### T14 — Resolver sobreposição plugins/browser vs skills/browser

| Campo | Valor |
|-------|-------|
| Objetivo | `plugins/browser.py` = adaptador de integração. `skills/browser/` = pacote instalável. Definir: quem implementa a lógica? Quem registra? |
| Área | `rlm/plugins/`, `rlm/skills/browser/` |
| Dependência | T06 |
| Risco | Médio |
| Resultado | Uma única cadeia de ownership para browser |

### T15 — Publicar RuntimeProjection como README de uso

| Campo | Valor |
|-------|-------|
| Objetivo | Documentar exatamente quais campos de `RuntimeProjection` cada superfície consome hoje. CLI, TUI, dashboard, operator. Se alguma superfície precisa de campo que não existe, é gap do contrato — não inferência da superfície. |
| Área | `docs/contracts/`, `rlm/runtime/contracts.py` |
| Dependência | T06 |
| Risco | Zero |
| Resultado | Toda expansão de superfície parte do contrato, não do parsing |

---

## 7. Critérios de Corte e Simplificação

| Pergunta | Regra |
|----------|-------|
| **Quando dois módulos fazem "a mesma coisa", qual fica?** | Fica o que está na camada correta (L0 > L1 > L2) E tem mais consumidores canônicos. O outro vira shim com data de sunset ou é deletado. |
| **Quando um legado deixa de ser tolerável?** | Quando gera DeprecationWarning em testes, quando tem 0 consumidores canônicos, ou quando está na mesma árvore que código vivo e confunde grep/IDE. |
| **Quando uma camada está vazando responsabilidade?** | Quando importa de camada acima (verificar via layer checker) OU quando contém lógica de domínio que pertence a outra camada (ex: `gateway/` fazendo dispatch). |
| **Quando uma superfície está inferindo demais?** | Quando constrói estado do runtime a partir de parsing de payload em vez de consumir `RuntimeProjection` ou outro contrato tipado. |
| **Quando uma feature deve ser bloqueada até consolidação?** | Sempre que a feature toca um conceito com dono duplicado (sessão, envelope, dispatch). A duplicação tem que ser resolvida primeiro. |
| **Quando cortar é melhor que manter?** | Quando o custo cognitivo de manter (confusão, grep noise, CI time) supera o custo de re-implementar se necessário. Para 99% do legado TS em `packages/`, o custo de manter é puro desperdício. |

---

## 8. Invariantes Obrigatórios

Estes invariantes NÃO podem ser violados após a reorganização:

| # | Invariante | Verificação |
|---|-----------|-------------|
| I1 | **Um único dono canônico por conceito central** (sessão, envelope, dispatch, config, projeção) | Grep: conceito aparece em 1 módulo canônico. Shims não contam. |
| I2 | **Um único caminho de dispatch por ingresso** | Todo ingresso → `dispatch_runtime_prompt_sync` com contexto padronizado. |
| I3 | **Uma única RuntimeProjection oficial** | Superfícies leem `runtime/contracts.py` → `schemas/runtime-projection.v1.json`. |
| I4 | **Núcleo (L0) sem dependência de UI, canal ou superfície** | `check_layer_imports.py` → 0 violações. |
| I5 | **Canais sem semântica de dispositivo** | `rlm/gateway/` não importa de `rlm/devices/`. |
| I6 | **Multimodalidade fora do transporte humano** | Percepção tem pipeline próprio, não vive embarcada em adaptador de canal. |
| I7 | **Dispositivos como domínio próprio** | `rlm/devices/` com registry, capabilities, contratos. Não são "chat adapters". |
| I8 | **Contratos cross-layer com schema, testes e versionamento** | Todo contrato tem JSON schema em `schemas/`, validação em CI, versão no nome. |
| I9 | **Zero artefatos de trabalho (.bak, .new, .old) no caminho crítico** | CI falha se encontrar esses globs. |
| I10 | **Zero import de packages/ em código canônico rlm/** | CI cheque explícito. |

---

## 9. Métricas de Eficiência Arquitetural

| Métrica | Como medir | Baseline atual | Meta pós-Fase 3 |
|---------|-----------|----------------|------------------|
| **Duplicação estrutural** | Conceitos com >1 dono canônico | 5 (sibling_bus, operator_bridge, envelope overlap, porta, webhook_dispatch) | 0 |
| **Caminhos de dispatch redundantes** | Paths distintos ingresso→runtime | 5 confirmados | 1 (unificado) |
| **Shims ativos** | Arquivos com DeprecationWarning | 2 (sibling_bus, operator_bridge) | 0 |
| **Artefatos arqueológicos** | .bak, .new, .old em rlm/ | 3 | 0 |
| **Acoplamento cross-layer** | Violações em check_layer_imports.py | 0 (já correto) | 0 (manter) |
| **Legado em caminho crítico** | Imports de packages/ em rlm/ | 0 (parece limpo, confirmar com CI) | 0 (enforced) |
| **Testes coletados** | pytest --co | 2 134 | ≥ 2 134 (não regredir) |
| **Cobertura de invariantes** | Testes de invariante vs testes de migração | ~0% invariante, ~100% fase/migração | 50%+ invariante |
| **SessionIdentity tipada** | Call sites usando SessionIdentity vs strings soltas | 0% | 100% |
| **Previsibilidade de superfícies** | Superfícies que consomem RuntimeProjection vs parsing ad-hoc | ~30% (operador sim, CLI parcial, TUI parcial) | 100% |

---

## 10. Ordem Executiva Final

### PARE IMEDIATAMENTE:
1. Qualquer expansão de feature antes de concluir Fases 0–3.
2. Qualquer novo canal, plugin ou dispositivo como "mais um arquivo em plugins/".
3. Qualquer criação de novo caminho de dispatch paralelo.
4. Qualquer uso de `packages/` como referência de implementação.

### CORRIJA ANTES DE QUALQUER EXPANSÃO:
1. Elimine os 2 shims (sibling_bus, operator_bridge).
2. Elimine os 3 arquivos .bak/.new.
3. Unifique porta padrão (5000).
4. Crie SessionIdentity como tipo formal.
5. Documente papel de cada Envelope.
6. Mapeie e unifique os 5 caminhos de dispatch.

### TRANSFORME EM CONTRATO FIXO:
1. `SessionIdentity` → schema versionado.
2. `RuntimeProjection` → já é contrato. Documentar consumidores.
3. `Envelope Pipeline` → ADR-005 com papel de cada tipo.
4. `DeviceCommand` + `DeviceTelemetry` → schemas antes de qualquer device code.
5. `MediaArtifact` → schema antes de qualquer perception pipeline.

### SEQUÊNCIA QUE NÃO PODE SER QUEBRADA:
```
Fase 0 (direção) → Fase 1 (deletar duplicações) → Fase 2 (contratos)
→ Fase 3 (dispatch unificado) → Fase 4 (multimodal + devices) → Fase 5 (hardening)
```

Cada fase é pré-condição da seguinte. Pular fases repete o erro atual. A sequência é inflexível.

---

## Apêndice A — Evidência de Código

### Duplicação sibling_bus
- Shim: `rlm/core/comms/sibling_bus.py` — 30 linhas, re-export com DeprecationWarning
- Canônico: `rlm/core/orchestration/sibling_bus.py` — implementação real
- Consumidores:  `core/engine/sub_rlm.py:637,871`, 20+ pontos em testes — todos usam `orchestration`
- Shim consumidor: `tests/test_local_repl_persistent.py:11` — ÚNICO

### 3 tipos de Envelope
- `rlm/core/comms/envelope.py` — `Direction`, `Envelope`, `MessageType` (bus routing)
  - Consumidores: `core/comms/message_bus.py:23`, `server/api.py:1071`, `tests/test_phase3*.py`, `tests/test_phase4*.py`, `tests/test_message_bus.py`
- `rlm/gateway/message_envelope.py` — `InboundMessage` (normalização de canal, frozen)
  - Consumidores: `server/api.py:57`, `tests/test_gateway_infra.py`, `tests/test_phase4*.py`, `tests/test_message_bus.py`
- `rlm/gateway/envelope.py` — `Envelope` schema v1 (WS bridge TS↔Python)
  - Consumidores: `server/ws_gateway_endpoint.py:38`, `server/brain_api.py:9`, `gateway/__init__.py:19`, `tests/test_envelope_schema.py`

### 5 caminhos de dispatch ao runtime
1. `server/api.py:629` → `dispatch_runtime_prompt_sync(...)` direto
2. `server/brain_api.py` → `dispatch_prompt(envelope)` via Envelope + _build_services
3. `server/ws_gateway_endpoint.py:294` → `brain_api.dispatch_prompt(envelope)`
4. `server/webhook_dispatch.py:346` → `dispatch_runtime_prompt_sync(...)` direto
5. `gateway/telegram_gateway.py:364` → via SessionManager → dispatch

### 3 arquivos .bak/.new no caminho crítico
1. `rlm/core/engine/sub_rlm.py.bak`
2. `rlm/core/engine/sub_rlm.py.new`
3. `rlm/environments/local_repl.py.bak`

### Porta padrão conflitante
- `core/config.py:124`: `os.getenv("RLM_API_PORT") or os.getenv("RLM_PORT") or str(srv.get("port", 8000))`
- `server/api.py:1162`: `int(os.environ.get("RLM_API_PORT", "5000"))`
- `cli/context.py:138`: `self.env.get("RLM_API_PORT", "5000")`

---

*Documento gerado por auditoria real de código. Todo número de linha, import path e contagem foi verificado contra o repositório.*
