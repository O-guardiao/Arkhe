# Plano de Correção de Trajetória do RLM

**Data**: 2026-04-12
**Autor**: Engenheiro Principal / Arquiteto de Software
**Base documental**: analise-clean-room-rlm, analise-clean-room-vscode-main, analise-clean-room-claw-code, analise-clean-room-picoclaw

---

## Mandato Técnico

**Congele expansão. Consolide ownership. Elimine duplicações estruturais. Corrija contratos. Só depois expanda.**

O RLM tem capacidade teórica suficiente para operar como núcleo de alto contexto, multicanal, multimodal e coordenação de dispositivos. O que o impede de ser isso na prática é a ambiguidade estrutural: ownership difuso sobre conceitos centrais, duplicação ativa de implementações, múltiplos caminhos equivalentes de dispatch, sobreposição entre camadas e legado contaminando o caminho crítico.

A missão imediata é: consolidar o cérebro Python, fixar contratos entre camadas, eliminar toda duplicação canônica, alinhar superfícies a projeções oficiais e só então voltar a expandir.

---

## 1. Diagnóstico Brutal da Situação Atual

### 1.1 Split-brain arquitetural

O RLM opera sob três linguagens simultâneas sem ownership inequívoco:
- **Python** (`rlm/`): núcleo real — runtime recursivo, REPL, subagentes, memória, daemon, gateway, servidor.
- **TypeScript** (`packages/`): migração parcial abandonada — `packages/gateway`, `packages/daemon`, `packages/config`, `packages/channels`, `packages/cli`, `packages/server`, `packages/terminal`. Esses módulos não são consumidores reais. São artefatos de uma fase migratória que perdeu centralidade.
- **Rust** (`native/`): aceleradores pontuais (`arkhe-memory`, `arkhe-wire`) e módulos futuros (`arkhe-policy-core`, `arkhe-mcts`, `arkhe-vault`, `arkhe-audit`). Status: 2 entregues, 4 em backlog.

**Impacto**: quem olha o repositório não sabe o que é canônico. O CI não valida a fronteira. Novos contribuidores podem acoplar no legado.

### 1.2 Duplicação de conceitos centrais

| Conceito | Implementação A | Implementação B | Status |
|---|---|---|---|
| Envelope de mensagem | `rlm/gateway/envelope.py` | `rlm/gateway/message_envelope.py` | Duas implementações ativas no mesmo diretório |
| Sibling Bus | `rlm/core/comms/sibling_bus.py` | `rlm/core/orchestration/sibling_bus.py` | Duplicação canônica confirmada |
| Gateway adapters | `rlm/gateway/telegram_gateway.py` etc. | `packages/gateway/` (TS) | Python é canônico, TS é fantasma |
| Config | `rlm/core/config.py` | `packages/config/` (TS) | Python é canônico, TS é fantasma |
| Daemon | `rlm/daemon/` | `packages/daemon/` (TS) | Python é canônico, TS é fantasma |
| CLI | `rlm/cli/` | `packages/cli/` (TS) | Python recebeu controle de volta |
| Auth helpers | `rlm/core/auth.py` + `rlm/core/security/auth.py` + `rlm/gateway/auth_helpers.py` | — | Três pontos de auth sem precedência clara documentada |

**Impacto**: previsibilidade zero. Quando o sistema tem dois donos para o mesmo conceito, bugs se escondem na interface entre eles.

### 1.3 Fronteira de sessão, contexto e memória borrada

Convivem hoje:
- **Sessão do servidor** (`SessionManager` em `rlm/core/session/`)
- **Sessão conversacional** (transcript, session_key, session_label)
- **Ambiente persistente** (`LocalREPL` + warm_runtime no daemon)
- **Memória de trabalho** (`RLMMemory` → `memory_manager.py` → hybrid search)
- **Memória de sessão** (`session_memory_tools.py` + `daemon/memory_access.py`)
- **Knowledge Base** (`knowledge_base.py` + `knowledge_consolidator.py`)

Essas camadas se encaixam o suficiente para funcionar, mas não há um contrato explícito que diga: "sessão termina aqui, contexto começa ali, memória de longo prazo é responsabilidade deste módulo e de nenhum outro".

**Impacto**: cada expansão (novo canal, novo dispositivo, novo tipo de mídia) precisa "adivinhar" onde se conectar.

### 1.4 Múltiplos caminhos de dispatch

Uma requisição pode entrar por:
- `api.py` (REST)
- `webhook_dispatch.py` (webhooks de canais)
- `openai_compat.py` (API compatível OpenAI)
- `brain_router.py` (API interna)
- `ws_gateway_endpoint.py` (WebSocket)
- `operator_bridge.py` (operador)
- `scheduler.py` (tarefas programadas)
- `workbench.py` (TUI)

O esforço recente de convergir todos para `dispatch_runtime_prompt_sync` via daemon melhorou muito, mas a convergência não está completa e não há enforcement.

**Impacto**: cada ingresso que não converge é um ponto de falha autônomo.

### 1.5 Superfícies interpretando o runtime em vez de consumir projeção

Antes do `RuntimeProjection`, CLI e TUI parseavam payload frouxo. O schema `runtime-projection.v1.json` e `operator_surface.py` começaram a corrigir isso. Mas:
- Nem todas as superfícies consomem exclusivamente a projeção.
- O visualizer (Next.js) é uma superfície paralela que não participa dessa disciplina.
- `packages/cli/` (TS) continua existindo como fantasma.

**Impacto**: duas superfícies vendo estados diferentes do mesmo runtime.

### 1.6 Multimodalidade e dispositivos como exceções, não como subsistemas

- `plugins/audio.py` → plugin solto
- `plugins/browser.py` → plugin solto
- `skills/voice/`, `skills/whisper/`, `skills/image_gen/` → skills avulsas
- Não existe `rlm/perception/` nem `rlm/devices/`
- Drones, robôs, câmeras, ESP32 e sensores não têm domínio próprio

**Impacto**: cada novo tipo de mídia ou dispositivo entra como remendo, aumentando acoplamento.

### 1.7 Legado no caminho crítico

- `rlm/core/_refactor_rlm.py` → artefato histórico no core
- `rlm/core/rlm copy.md` → nota perdida no core
- `rlm/core/engine/sub_rlm.py.bak`, `sub_rlm.py.new` → backups no engine
- `tests/test_ts_cli_shim.py` → importa módulo inexistente
- `packages/` inteiro → surface morta exceto conveniência legada

**Impacto**: peso cognitivo e confusão estrutural.

### 1.8 Testes orientados a fase, não a invariante

A suite de testes reflete o histórico de migrações:
- `test_phase3_gateway_migration.py`
- `test_phase4_config_crosschannel.py`
- `test_phase5_auth_clients.py`
- `test_phase6_channel_discovery.py`
- `test_critical_phase8.py`
- `test_critical_phase10.py`

Isso é útil para rastreabilidade, mas ruim como bússola: os testes validam milestones do passado, não invariantes do produto atual.

---

## 2. Decisões Arquiteturais Obrigatórias

A partir deste plano, as seguintes decisões passam a ser **regra fixa**:

| # | Decisão | Justificativa |
|---|---------|---------------|
| D1 | **Python é o dono canônico do runtime recursivo, sessão, memória, daemon e gateway** | A suíte clean room do RLM confirma: `rlm/` é a base canônica. |
| D2 | **Rust entra apenas para hot path medido ou domínio de alta garantia (policy, vault, audit)** | Dois crates entregues (`arkhe-memory`, `arkhe-wire`) comprovam o modelo. Sem terceira linguagem como espinha dorsal. |
| D3 | **`packages/` sai do caminho crítico imediatamente** | Nenhum módulo TS em `packages/` tem consumidores internos reais. |
| D4 | **Uma única fonte de verdade para cada conceito central** | Envelope, session identity, dispatch path, config, sibling bus, runtime projection — um dono, zero duplicação. |
| D5 | **Superfícies humanas são consumidoras do runtime, não intérpretes informais dele** | CLI, TUI, dashboard, visualizer consomem `RuntimeProjection`. Ponto. |
| D6 | **Todo contrato cross-layer nasce com schema, testes e versionamento** | Aprendizado direto do VS Code: protocolo versionado com reducers puros e tipagem forte. |
| D7 | **Multimodalidade, canais humanos e dispositivos são subsistemas explícitos** | Aprendizado do PicoClaw: gateway como coordenador de serviços, não como wrapper. |
| D8 | **Nenhuma feature nova antes de consolidar** | Aprendizado do Claw-Code: workspace Rust só progrediu porque manteve fronteiras por crate. |

---

## 3. Arquitetura Alvo Executável

### 3.1 Camadas e fronteiras

```
┌─────────────────────────────────────────────────────┐
│  SUPERFÍCIES HUMANAS                                │
│  CLI · TUI · Dashboard · Visualizer · Operator      │
│  ← Consomem RuntimeProjection (schema versionado)   │
│  ← Não importam core, daemon, gateway diretamente   │
└──────────────────────┬──────────────────────────────┘
                       │ RuntimeProjection v1
┌──────────────────────┴──────────────────────────────┐
│  CAMADA OPERACIONAL                                 │
│  Daemon · SessionManager · Scheduler · Auth ·       │
│  Config · Health · Drain · Backpressure ·           │
│  Telemetria · Lifecycle                             │
│  ← Expõe contratos estáveis para superfícies        │
│  ← Protege núcleo contra dispersão operacional      │
└──────┬────────────┬────────────┬────────────────────┘
       │            │            │
┌──────┴──────┐ ┌───┴──────┐ ┌──┴──────────────────┐
│  CANAIS     │ │ PERCEPÇÃO│ │ DISPOSITIVOS        │
│  Telegram   │ │ Audio    │ │ DeviceRegistry      │
│  Discord    │ │ Image    │ │ CapabilityContract  │
│  Slack      │ │ OCR/ASR  │ │ Command/Telemetry   │
│  WhatsApp   │ │ Vision   │ │ Drone/Robot/Camera  │
│  WebChat    │ │ Browser  │ │ ESP32/Edge/Actuator │
│  WebSocket  │ │ MediaPipe│ │ Policy/Timeout/Ack  │
│  MCP Bridge │ │          │ │                     │
└──────┬──────┘ └───┬──────┘ └──┬──────────────────┘
       │            │            │
       └────────────┴────────────┘
                    │ Envelope canônico + ChannelEvent
┌───────────────────┴─────────────────────────────────┐
│  DISPATCH UNIFICADO                                 │
│  Um único ponto de entrada para o runtime           │
│  ← Normaliza ingresso                              │
│  ← Resolve sessão e identidade                     │
│  ← Aplica routing policy                           │
│  ← Delega para LLMGate → Daemon → Runtime          │
└───────────────────┬─────────────────────────────────┘
                    │
┌───────────────────┴─────────────────────────────────┐
│  NÚCLEO DE RACIOCÍNIO E CONTEXTO                    │
│  RLM Engine · Loop Recursivo · REPL Persistente ·   │
│  SubAgentes · Memória · Knowledge Base ·            │
│  Compaction · MCTS · Role Orchestration ·           │
│  ContextBlock · Ledger de Tarefas                   │
│  ← Não conhece webhook, CLI, TUI, canal, device    │
│  ← Não importa UI, gateway nem dashboard            │
└─────────────────────────────────────────────────────┘
```

### 3.2 Responsabilidades por camada

#### Núcleo de Raciocínio e Contexto (`rlm/core/`)
- **Possui**: loop recursivo, REPL, spawn de subagentes, composição de contexto, memória de trabalho, recall, knowledge base, compaction, loop detection, control flow, hooks, role orchestration, MCTS, supervisor, session journal.
- **Não possui**: webhook, CLI, TUI, canal externo, dispositivo específico, auth de transporte, rendering de UI.
- **Pode depender de**: `rlm/clients/`, `native/arkhe-memory`, `native/arkhe-wire`.
- **Não pode depender de**: `rlm/cli/`, `rlm/server/`, `rlm/gateway/`, `rlm/daemon/`, `rlm/plugins/`, `packages/`.

#### Camada de Sessão e Identidade (`rlm/core/session/`)
- **Possui**: SessionIdentity, session_key, session_label, client_registry, model_overrides, send_policy, transcript.
- **Não possui**: transporte, rendering, canal específico.
- **Pode depender de**: `rlm/core/types`, `rlm/core/config`.
- **Não pode depender de**: `rlm/gateway/`, `rlm/cli/`, `rlm/daemon/`.

#### Camada de Canais e Conversa (`rlm/gateway/` + `rlm/core/comms/`)
- **Possui**: normalização de ingressos, resolução de sessão de transporte, roteamento, chunking, retry, outbox, delivery, adapters (Telegram, Discord, Slack, WhatsApp, WebChat, WebSocket, MCP bridge).
- **Não possui**: semântica interna do REPL, device orchestration, percepção, rendering de UI.
- **Pode depender de**: `rlm/core/comms/envelope`, `rlm/core/session/`, `rlm/core/config`.
- **Não pode depender de**: `rlm/core/engine/`, `rlm/cli/`, `packages/`.

#### Camada de Percepção Multimodal (a criar: `rlm/perception/`)
- **Possui**: ingestão de áudio, imagem, OCR, ASR, visão, browser parsing, armazenamento de artefato de mídia, análise, extração estruturada, publicação de `MediaArtifact` para o núcleo.
- **Não possui**: transporte humano, device commands, rendering.
- **Pode depender de**: `rlm/core/types`, `rlm/core/memory/`.
- **Não pode depender de**: `rlm/gateway/`, `rlm/cli/`, `rlm/daemon/`.

#### Camada de Orquestração de Dispositivos (a criar: `rlm/devices/`)
- **Possui**: registry de capabilities, catálogo de dispositivos, contratos de telemetria (`DeviceTelemetry`), comandos idempotentes (`DeviceCommand`), confirmação, timeout, segurança, política operacional.
- **Não possui**: chat humano, UI, percepção de mídia (consume `MediaArtifact` se necessário).
- **Pode depender de**: `rlm/core/types`, `rlm/core/comms/message_bus`.
- **Não pode depender de**: `rlm/gateway/` (adapters de chat), `rlm/cli/`.

#### Camada Operacional (`rlm/daemon/` + `rlm/server/`)
- **Possui**: RecursionDaemon, LLMGate, WarmRuntime, SessionManager, Scheduler, Health, Drain, Backpressure, Auth de transporte, Config de runtime, Lifecycle, Observabilidade (`operator_surface.py`).
- **Não possui**: lógica do REPL, composição de contexto, rendering de UI.
- **Pode depender de**: `rlm/core/`, `rlm/gateway/`, `rlm/perception/`, `rlm/devices/`.
- **Não pode depender de**: `rlm/cli/`, `packages/`.

#### Superfícies Humanas (`rlm/cli/`)
- **Possui**: CLI, TUI, channel_console, wizard, setup, doctor, status, operator display.
- **Não possui**: runtime execution, gateway logic, device orchestration.
- **Pode depender de**: `RuntimeProjection` (schema), `rlm/cli/tui/live_api.py` (HTTP client para server).
- **Não pode depender de**: `rlm/core/engine/`, `rlm/daemon/recursion_daemon.py`, `rlm/gateway/` (diretamente).

### 3.3 Contratos obrigatórios

| Contrato | Arquivo canônico | Schema |
|---|---|---|
| `RuntimeProjection` | `rlm/runtime/contracts.py` | `schemas/runtime-projection.v1.json` |
| `Envelope` | `rlm/core/comms/envelope.py` | `schemas/envelope.v1.json` |
| `SessionIdentity` | `rlm/core/session/session_key.py` | A criar: `schemas/session-identity.v1.json` |
| `ContextBlock` | `rlm/core/types.py` | A criar: `schemas/context-block.v1.json` |
| `MediaArtifact` | `rlm/perception/types.py` (a criar) | A criar: `schemas/media-artifact.v1.json` |
| `DeviceCommand` | `rlm/devices/contracts.py` (a criar) | A criar: `schemas/device-command.v1.json` |
| `DeviceTelemetry` | `rlm/devices/contracts.py` (a criar) | A criar: `schemas/device-telemetry.v1.json` |
| `ToolSpec` | `rlm/core/tools/specs.py` | `schemas/tool-spec.v1.json` |
| `PermissionPolicy` | `rlm/core/security/execution_policy.py` | `schemas/permission-policy.v1.json` |
| `HealthReport` | `rlm/server/health_monitor.py` | `schemas/health-report.v1.json` |
| `WSProtocol` | `rlm/server/ws_server.py` | `schemas/ws-protocol.v1.json` |

---

## 4. Mapa de Consolidação

### MANTER

- Núcleo Python em `rlm/core/engine/`, `rlm/core/memory/`, `rlm/environments/`
- Daemon persistente em `rlm/daemon/` (recursion_daemon, llm_gate, warm_runtime, task_agents, memory_access)
- MessageBus, Outbox, DeliveryWorker, channel_bootstrap em `rlm/core/comms/`
- Projeção operacional em `rlm/core/observability/operator_surface.py`
- Schemas versionados em `schemas/`
- Crates Rust entregues: `native/arkhe-memory/`, `native/arkhe-wire/`
- Suite de testes com cobertura de daemon, TUI, gateway, session, memory

### REFATORAR

| Problema | Ação | Área |
|---|---|---|
| Ownership de sessão borrado | Fixar `SessionIdentity` como tipo canônico, eliminar resolução ad hoc | `rlm/core/session/`, `rlm/daemon/`, `rlm/server/` |
| Convergência de ingressos incompleta | Forçar 100% dos ingressos pelo dispatch unificado do daemon | `rlm/server/api.py`, `webhook_dispatch.py`, `openai_compat.py`, `brain_router.py` |
| Auth com 3 pontos sem precedência | Unificar em `rlm/core/security/auth.py` com prioridade documentada | `rlm/core/auth.py`, `rlm/core/security/auth.py`, `rlm/gateway/auth_helpers.py` |
| Tools vs Skills vs Plugins sobrepostos | Tools = implementação, Skills = empacotamento, Plugins = channel adapters. Sem sobreposição. | `rlm/tools/`, `rlm/skills/`, `rlm/plugins/` |
| Projeção do runtime com fallbacks legados | Eliminar todos os fallbacks; superfícies só leem `RuntimeProjection` | `rlm/cli/commands/workbench.py` |

### EXTRAIR

| O que | De onde | Para onde |
|---|---|---|
| Subsistema de dispositivos | Não existe formalmente | `rlm/devices/` (novo) |
| Subsistema de percepção multimodal | `rlm/plugins/audio.py`, `rlm/plugins/browser.py`, skills de voz/imagem | `rlm/perception/` (novo) |
| Schemas de SessionIdentity, ContextBlock, MediaArtifact, DeviceCommand | Inline ou inexistentes | `schemas/` |
| Validador de fronteiras de camada | Não existe | CI check inspirado no valid-layers do VS Code |

### CONSOLIDAR

| Duplicação | Sobrevivente | Morto |
|---|---|---|
| Sibling Bus | `rlm/core/comms/sibling_bus.py` | `rlm/core/orchestration/sibling_bus.py` |
| Envelope | `rlm/core/comms/envelope.py` (alinhado ao schema) | `rlm/gateway/message_envelope.py`, `rlm/gateway/envelope.py` (merge para um) |
| Auth | `rlm/core/security/auth.py` (com precedência fixa) | `rlm/core/auth.py` (merge), `rlm/gateway/auth_helpers.py` (helpers movem para security) |
| Config | `rlm/core/config.py` | `packages/config/` (aposentar) |
| Variáveis de porta | Uma única: `RLM_API_PORT` | `RLM_PORT`, `PORT` (deprecar) |

### APOSENTAR

| Artefato | Tipo | Justificativa |
|---|---|---|
| `packages/gateway/` | Legado TS | Duplica `rlm/gateway/` |
| `packages/daemon/` | Legado TS | Duplica `rlm/daemon/` |
| `packages/config/` | Legado TS | Duplica `rlm/core/config.py` |
| `packages/channels/` | Legado TS | Sem consumidor |
| `packages/server/` | Legado TS | Duplica `rlm/server/` |
| `packages/terminal/` | Legado TS | Absorvido por rich/output |
| `rlm/core/_refactor_rlm.py` | Artefato histórico | Não é contrato |
| `rlm/core/rlm copy.md` | Nota perdida | Não é contrato |
| `rlm/core/engine/sub_rlm.py.bak` | Backup | Não é contrato |
| `rlm/core/engine/sub_rlm.py.new` | Backup | Não é contrato |
| `tests/test_ts_cli_shim.py` | Teste morto | Importa módulo inexistente |
| `_migrated_to_ts/` | Snapshot de migração | Fase abandonada |

---

## 5. Sequência de Execução por Fases

### Fase 0: Congelamento de Direção

- **Objetivo**: Declarar dono canônico de cada domínio. Fim do split-brain.
- **Problema que resolve**: Ambiguidade estratégica sobre o que é canônico.
- **Artefatos afetados**: `AGENTS.md`, `README.md`, `docs/`, CI, `packages/` marker.
- **Risco**: Baixo. É decisão documental e organizacional.
- **Ganho esperado**: Zero ambiguidade sobre quem manda no quê.
- **Condição para começar**: Nenhuma. É a primeira ação.
- **Critério de pronto**: Toda documentação, onboarding e CI afirmam: Python é canônico, `packages/` é legado, Rust é aceleração pontual. Marker `LEGACY.md` em `packages/`.

### Fase 1: Unificação de Contratos

- **Objetivo**: Criar/formalizar `SessionIdentity`, `ContextBlock`, contratos de device e mídia. Mover envelope para um único dono.
- **Problema que resolve**: Inferência ad hoc, payload frouxo, sobreposição semântica.
- **Artefatos afetados**: `schemas/`, `rlm/core/comms/envelope.py`, `rlm/core/session/`, `rlm/runtime/contracts.py`, `rlm/gateway/`.
- **Risco**: Médio. Schemas errados travam tudo.
- **Ganho esperado**: Fronteiras claras entre camadas. Superfícies externas param de parsear payload genérico.
- **Condição para começar**: Fase 0 concluída.
- **Critério de pronto**: Cada contrato tem schema JSON, dataclass Python, testes de validação e zero superfície consumindo payload sem tipagem.

### Fase 2: Consolidação do Kernel

- **Objetivo**: Reorganizar ownership entre runtime, sessão, contexto e memória. Eliminar duplicações (sibling bus, auth, config). Resolver artefatos históricos.
- **Problema que resolve**: Núcleo pesado, pouco nítido, com donos duplos.
- **Artefatos afetados**: `rlm/core/comms/sibling_bus.py`, `rlm/core/orchestration/sibling_bus.py`, `rlm/core/auth.py`, `rlm/core/security/auth.py`, `rlm/gateway/auth_helpers.py`, artefatos `.bak`/`.new`/`_refactor`.
- **Risco**: Alto. Mexe em módulos do caminho crítico. Requer cobertura de testes rigorosa.
- **Ganho esperado**: Um único dono por conceito. Menos acoplamento. Menos bugs invisíveis.
- **Condição para começar**: Fase 1 concluída (contratos existem para validar).
- **Critério de pronto**: Zero duplicação canônica. Testes de sessão, memória e daemon verdes. Import graph limpo de artefatos.

### Fase 3: Unificação de Ingressos e Superfícies

- **Objetivo**: 100% dos ingressos passam pelo mesmo dispatcher. 100% das superfícies leem `RuntimeProjection`.
- **Problema que resolve**: Múltiplos pipelines equivalentes, superfícies com versões concorrentes do runtime.
- **Artefatos afetados**: `rlm/server/api.py`, `webhook_dispatch.py`, `openai_compat.py`, `brain_router.py`, `operator_bridge.py`, `scheduler.py`, `ws_gateway_endpoint.py`, `rlm/cli/commands/workbench.py`.
- **Risco**: Médio-alto. Qualquer ingresso que quebre afeta um canal inteiro.
- **Ganho esperado**: Previsibilidade operacional total. Qualquer entrada → mesmo caminho → mesmo snapshot.
- **Condição para começar**: Fase 2 concluída (kernel tem donos claros).
- **Critério de pronto**: Nenhum ingresso bypassa o dispatcher unificado. Nenhuma superfície faz engenharia reversa do estado interno. CI valida com teste de integração por ingresso.

### Fase 4: Explicitação de Multimodalidade e Dispositivos

- **Objetivo**: Criar `rlm/perception/` e `rlm/devices/` como subsistemas formais.
- **Problema que resolve**: Áudio, imagem, browser, sensores e atuadores são plugins soltos. Drones e robôs não têm domínio próprio.
- **Artefatos afetados**: `rlm/plugins/audio.py`, `rlm/plugins/browser.py`, skills de voz/imagem, future device adapters.
- **Risco**: Médio. Extração de domínio, não reescrita.
- **Ganho esperado**: Capacidade de adicionar câmera, drone, sensor ou novo tipo de mídia sem colapsar complexidade.
- **Condição para começar**: Fase 3 concluída (dispatch unificado funciona).
- **Critério de pronto**: Um fluxo de câmera/áudio e um fluxo de drone/dispositivo passam pela mesma disciplina de contratos, policy e observabilidade que um fluxo de chat.

### Fase 5: Deleção e Hardening

- **Objetivo**: Remover legado crítico, testes mortos, shims. Adicionar validação de camadas em CI.
- **Problema que resolve**: Peso estrutural remanescente, custo cognitivo, risco de reacoplamento.
- **Artefatos afetados**: `packages/`, artefatos `.bak`/`.new`/`_migrated_to_ts`, testes mortos, import graph.
- **Risco**: Médio. Deleção é irreversível (mas com git, recuperável).
- **Ganho esperado**: Redução real de volume. Zero dependência de legado em runtime canônico. CI com check de camada.
- **Condição para começar**: Fases 0-4 concluídas.
- **Critério de pronto**: `packages/` fora do import graph canônico. Zero artefato histórico no caminho crítico. CI falha em violação de fronteira de camada.

---

## 6. Backlog Inicial de Execução: 15 Tarefas Concretas

### T01 — Publicar ADR de ownership canônico
- **Objetivo técnico**: Documento curto no topo do repo declarando: Python é dono da recursão, `packages/` não volta ao caminho crítico, Rust só entra por benchmark ou segurança.
- **Área afetada**: `docs/`, `AGENTS.md`, `README.md`.
- **Dependência**: Nenhuma.
- **Risco**: Zero.
- **Resultado**: Ambiguidade estratégica eliminada.

### T02 — Inventariar conceitos com dono duplicado
- **Objetivo técnico**: Tabela fechada: session identity, envelope, sibling bus, config, auth precedence, operator snapshot, channel registry — quem é canônico, quem morre.
- **Área afetada**: Transversal.
- **Dependência**: T01.
- **Risco**: Baixo.
- **Resultado**: Mapa de duplicações pronto para execução.

### T03 — Unificar sibling bus
- **Objetivo técnico**: Mover toda funcionalidade para `rlm/core/comms/sibling_bus.py`. Eliminar `rlm/core/orchestration/sibling_bus.py`. Ajustar imports.
- **Área afetada**: `rlm/core/comms/`, `rlm/core/orchestration/`.
- **Dependência**: T02.
- **Risco**: Médio. Precisa de testes de sibling bus passando.
- **Resultado**: -1 implementação duplicada.

### T04 — Consolidar envelope em um único módulo
- **Objetivo técnico**: `rlm/core/comms/envelope.py` vira o dono canônico, alinhado ao schema `envelope.v1.json`. `rlm/gateway/message_envelope.py` e `rlm/gateway/envelope.py` são eliminados ou viram imports thin.
- **Área afetada**: `rlm/core/comms/`, `rlm/gateway/`.
- **Dependência**: T02.
- **Risco**: Médio.
- **Resultado**: -1 a -2 implementações duplicadas.

### T05 — Consolidar auth em um único módulo com precedência documentada
- **Objetivo técnico**: `rlm/core/security/auth.py` vira o dono. `rlm/core/auth.py` faz import ou é removido. `rlm/gateway/auth_helpers.py` move helpers necessários para security.
- **Área afetada**: `rlm/core/auth.py`, `rlm/core/security/auth.py`, `rlm/gateway/auth_helpers.py`.
- **Dependência**: T02.
- **Risco**: Médio. Precedência de token errada quebra acesso.
- **Resultado**: Precedência fixa: `RLM_ADMIN_TOKEN > RLM_API_TOKEN > RLM_WS_TOKEN > RLM_INTERNAL_TOKEN`.

### T06 — Criar schema de SessionIdentity
- **Objetivo técnico**: `schemas/session-identity.v1.json` + dataclass em `rlm/core/session/`. Campos: user_id, channel, device_id, session_key, client_id, scope.
- **Área afetada**: `rlm/core/session/`, `schemas/`.
- **Dependência**: T02.
- **Risco**: Baixo.
- **Resultado**: Canais, dispositivos e clientes param de misturar identidade informalmente.

### T07 — Marcar `packages/` como legado explícito
- **Objetivo técnico**: Criar `packages/LEGACY.md` com status. Adicionar CI check que falha se qualquer arquivo em `rlm/` importar de `packages/`. Remover `packages/` do install path.
- **Área afetada**: `packages/`, CI.
- **Dependência**: T01.
- **Risco**: Baixo.
- **Resultado**: Legado isolado do caminho crítico.

### T08 — Remover artefatos históricos do core
- **Objetivo técnico**: Deletar `rlm/core/_refactor_rlm.py`, `rlm/core/rlm copy.md`, `rlm/core/engine/sub_rlm.py.bak`, `rlm/core/engine/sub_rlm.py.new`.
- **Área afetada**: `rlm/core/`.
- **Dependência**: Nenhuma.
- **Risco**: Zero (git preserva história).
- **Resultado**: Core limpo de ruído.

### T09 — Eliminar test morto `test_ts_cli_shim.py`
- **Objetivo técnico**: Deletar `tests/test_ts_cli_shim.py`.
- **Área afetada**: `tests/`.
- **Dependência**: Nenhuma.
- **Risco**: Zero.
- **Resultado**: -1 teste que referencia módulo inexistente.

### T10 — Forçar convergência de todos os ingressos para o dispatcher unificado
- **Objetivo técnico**: Auditar `api.py`, `webhook_dispatch.py`, `openai_compat.py`, `brain_router.py`, `scheduler.py`, `operator_bridge.py`. Qualquer path que bypass o daemon deve ser eliminado ou convertido.
- **Área afetada**: `rlm/server/`, `rlm/gateway/`.
- **Dependência**: T03, T04.
- **Risco**: Médio-alto. Cada ingresso quebrado afeta canal.
- **Resultado**: Um único caminho de dispatch. Zero bypass.

### T11 — Criar stub de `rlm/perception/`
- **Objetivo técnico**: Criar `rlm/perception/__init__.py`, `rlm/perception/types.py` (MediaArtifact), `rlm/perception/pipeline.py` (ingestão → análise → publicação). Mover `plugins/audio.py` e `plugins/browser.py` como primeiros consumidores.
- **Área afetada**: `rlm/perception/` (novo), `rlm/plugins/`.
- **Dependência**: T06.
- **Risco**: Baixo. É extração, não reescrita.
- **Resultado**: Multimodalidade tem domínio próprio.

### T12 — Criar stub de `rlm/devices/`
- **Objetivo técnico**: Criar `rlm/devices/__init__.py`, `rlm/devices/contracts.py` (DeviceCommand, DeviceTelemetry, DeviceCapability), `rlm/devices/registry.py`. Schema em `schemas/`.
- **Área afetada**: `rlm/devices/` (novo), `schemas/`.
- **Dependência**: T06.
- **Risco**: Baixo.
- **Resultado**: Dispositivos têm domínio próprio, separado de canais humanos.

### T13 — Criar suíte de invariantes do produto
- **Objetivo técnico**: 4 trilhas: (1) alto contexto persistente multi-turno, (2) multicanal com retry e outbox, (3) rotas determinísticas vs LLM, (4) coordenação simultânea de múltiplos dispositivos (mock).
- **Área afetada**: `tests/`.
- **Dependência**: T03, T04, T10.
- **Risco**: Baixo.
- **Resultado**: Testes orientados a invariante, não a milestone histórico.

### T14 — Unificar variáveis de porta
- **Objetivo técnico**: `RLM_API_PORT` vira a única variável canônica. `RLM_PORT` e `PORT` viram aliases documentados com deprecation warning.
- **Área afetada**: `rlm/core/config.py`, `rlm/cli/context.py`, `rlm/server/`, docs.
- **Dependência**: T05.
- **Risco**: Baixo.
- **Resultado**: Zero confusão de porta.

### T15 — Criar layer check no CI
- **Objetivo técnico**: Script que parseia import graph e falha se: core importar cli/server/gateway, cli importar core.engine diretamente, gateway importar core.engine diretamente.
- **Área afetada**: CI, `scripts/`.
- **Dependência**: T07.
- **Risco**: Baixo.
- **Resultado**: Enforcement automático de fronteiras.

---

## 7. Critérios de Corte e Simplificação

### Quando dois módulos fazem "a mesma coisa", qual fica?
Fica o que está no caminho canônico Python e tem testes. Se ambos têm testes, fica o que está mais próximo do núcleo. O outro morre ou vira import thin.

### Quando um legado deixa de ser tolerável?
Quando qualquer arquivo em `rlm/` importa dele, quando novos contribuidores o confundem com código ativo, ou quando CI não o distingue de código canônico.

### Quando uma camada está vazando responsabilidade?
Quando `rlm/core/engine/` importa algo de `rlm/gateway/` ou `rlm/cli/`. Quando `rlm/cli/` importa diretamente de `rlm/core/engine/` em vez de consumir `RuntimeProjection`. Quando `rlm/gateway/` resolve semântica de sessão ou memória por conta própria.

### Quando uma superfície está inferindo demais o runtime?
Quando a superfície parseia dicts sem schema, acessa `.state` internos do daemon, ou precisa conhecer nomes de classes do engine para funcionar.

### Quando uma feature deve ser bloqueada até consolidação?
Quando ela requer um novo caminho de dispatch, um novo tipo de sessão, um novo contrato de envelope, ou uma nova integração que cruza camadas — e as camadas ainda não têm fronteiras fixas.

---

## 8. Invariantes Obrigatórios

Após a reorganização, os seguintes invariantes não podem ser violados:

| # | Invariante |
|---|-----------|
| I1 | Um único dono canônico por conceito central (sessão, envelope, dispatch, config, projeção, sibling bus) |
| I2 | Um único caminho de dispatch por ingresso |
| I3 | Uma única `RuntimeProjection` oficial consumida por todas as superfícies |
| I4 | Núcleo (`rlm/core/engine/`) com zero import de UI, zero import de canal, zero dependência de surface parsing |
| I5 | Canais sem semântica de dispositivo |
| I6 | Dispositivos sem dependência direta de UI — falam por `DeviceCommand` e `DeviceTelemetry` |
| I7 | Multimodalidade (percepção) fora do transporte humano |
| I8 | Todo contrato cross-layer tem schema JSON, dataclass Python, testes de validação e versionamento |
| I9 | Rust só entra para hot path medido ou domínio de alta garantia — nunca como terceira espinha dorsal |
| I10 | `packages/` fora do import graph canônico — zero dependência em runtime |
| I11 | Toda nova contribuição passa por check automático de fronteira de camada em CI |
| I12 | Nenhum `.bak`, `.new`, shim ou artefato de migração no caminho crítico |

---

## 9. Métricas de Eficiência Arquitetural

| Métrica | Como medir | Meta |
|---------|-----------|------|
| Duplicação estrutural | Contar conceitos com >1 implementação ativa | 0 |
| Caminhos de dispatch redundantes | Contar entrypoints que não convergem para o dispatcher unificado | 0 |
| Acoplamento core→surface | Contar imports em `rlm/core/engine/` que referenciam `rlm/cli/`, `rlm/server/`, `rlm/gateway/` | 0 |
| Linhas em domínios duplicados | LOC em módulos aposentados que ainda existem | 0 no caminho crítico |
| Pontos de falha por fluxo | Contar outboxes, policies de retry e identidades distintas por requisição | 1 outbox, 1 retry policy, 1 identity |
| Previsibilidade entre superfícies | CLI, TUI, dashboard retornam mesmo estado para mesmo instante | 100% convergência |
| Ownership por módulo | Todo módulo responde "qual conceito possuo / qual não possuo" | 100% documentado |
| Validação de fronteiras | CI falha em violação de dependência | Automático |
| Volume de legado ativo | Arquivos em `packages/` no import graph | 0 |
| Artefatos históricos no core | Contagem de `.bak`, `.new`, `_refactor`, `copy.md` | 0 |

---

## 10. Ordem Executiva Final

### O que deve parar imediatamente

1. **Tratar `packages/` TypeScript como direção futura.** É legado. Marcar e isolar.
2. **Deixar UI ou gateway inferirem a semântica do runtime.** Toda superfície consome `RuntimeProjection`. Sem exceção.
3. **Manter duplicações ativas de conceitos centrais.** Sibling bus duplicado, envelope duplicado, auth fragmentado — resolver agora.
4. **Expandir features antes de consolidar ownership.** Nenhuma feature nova de superfície enquanto sessão, contexto, memória e dispatch não tiverem dono único.
5. **Aceitar coexistência indefinida de múltiplas implementações do mesmo conceito.** Se há dois, escolhe um. Se há três, mata dois.

### O que deve ser corrigido antes de qualquer expansão

1. Dono do runtime: `rlm/core/engine/` (Python canônico).
2. Dono da sessão: `rlm/core/session/` com `SessionIdentity`.
3. Dono do envelope: `rlm/core/comms/envelope.py` alinhado ao schema.
4. Dono da projeção operacional: `rlm/core/observability/operator_surface.py` + `RuntimeProjection`.
5. Separação formal entre conversa (canais), percepção (multimodal) e automação (dispositivos).

### O que deve virar contrato fixo

- `RuntimeProjection` v1 (já existe)
- `Envelope` v1 (já existe, consolidar implementações)
- `SessionIdentity` v1 (criar)
- `ContextBlock` v1 (criar)
- `MediaArtifact` v1 (criar quando `rlm/perception/` nascer)
- `DeviceCommand` v1 + `DeviceTelemetry` v1 (criar quando `rlm/devices/` nascer)

### Sequência que não pode ser quebrada

```
Fase 0 (Direção) → Fase 1 (Contratos) → Fase 2 (Kernel) → Fase 3 (Ingressos/Superfícies) → Fase 4 (Multi/Devices) → Fase 5 (Deleção/Hardening)
```

Qualquer tentativa de pular para a Fase 4 ou 5 antes de concluir 0-3 repete o erro que trouxe o RLM até aqui.

---

## Nota Final

O RLM não precisa de mais ambição. Precisa de mais disciplina. A capacidade está lá: alto contexto, multicanal, multimodalidade, coordenação de dispositivos, interface humano-máquina. O que faltava era a estrutura para sustentar tudo isso sem colapsar.

Este plano não inventa um produto novo. Ele recupera, organiza e consolida o que o RLM já deveria ser. Se executado na ordem correta, o RLM não perde nenhuma capacidade. Ele finalmente ganha forma.
