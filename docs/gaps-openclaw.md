# RLM vs OpenClaw — Gaps e Roadmap

Documento de referência para medir o que ainda separa o RLM de uma experiência
"instalar e usar" no estilo OpenClaw.

Última auditoria: 2026-03-12

---

## Leitura correta do estado atual

O documento anterior estava desatualizado. O RLM atual já tem mais casca de
produto do que o texto sugeria. O erro principal era tratar recursos já
existentes como se ainda fossem apenas ideia.

### Gaps já corrigidos desde a versão anterior do documento

| Item | Antes | Estado real agora | Evidência principal |
|---|---|---|---|
| WebChat | Ausente | ✅ Implementado | `rlm/server/webchat.py`, `rlm/static/webchat.html`, README |
| Discord | Ausente | ✅ Gateway + plugin | `rlm/server/discord_gateway.py`, `rlm/plugins/discord.py` |
| WhatsApp | Ausente | ✅ Gateway + plugin | `rlm/server/whatsapp_gateway.py`, `rlm/plugins/whatsapp.py` |
| Slack | Ausente | ✅ Gateway + plugin | `rlm/server/slack_gateway.py`, `rlm/plugins/slack.py` |
| `rlm doctor` | Ausente | ✅ Implementado | `rlm/cli/main.py` |
| `rlm skill install` | Ausente | ✅ Implementado | `rlm/cli/main.py` |
| `rlm channel list` | Ausente | ✅ Implementado | `rlm/cli/main.py` |
| `rlm update` | Ausente | ✅ Implementado nesta rodada | `rlm/cli/main.py`, `rlm/cli/service.py` |

Conclusão: o RLM já não é só engine. Ele já tem bootstrap, servidor, webchat,
gateways, health checks básicos e instalação remota de skills. O que falta é
reduzir atrito, endurecer operação e completar a camada de produto.

---

## Gap 0 — Instalação, distribuição e baseline de runtime

**Status:** continua crítico, mas melhor mapeado.

O bloqueio mais primário para "instalar e usar" não é mais ausência de
feature. É baseline de execução previsível. Se o ambiente sobe com Python errado,
sem `uv` ou com setup parcial de `.env`, o produto falha antes de o usuário
chegar às capacidades reais do RLM.

### O que já existe

```
pyproject.toml        → exige Python >= 3.11
rlm/cli/wizard.py     → wizard de setup e geração de .env
rlm/cli/main.py       → `rlm setup`, `rlm doctor`, `rlm update`
```

### O que esta rodada fechou parcialmente

| Item | Estado atual | Motivo |
|---|---|---|
| Checagem de versão Python no `doctor` | ✅ | Agora acusa runtime incompatível logo no início |
| Checagem de `uv` no `doctor` | ✅ | Torna explícito quando setup/update automático ficará capado |
| Diagnóstico mais acionável do `.env` e canais | ✅ parcial | Já diferencia configurado, ausente e incompleto |
| Bloqueio de `start/setup` fora do baseline | ✅ | `rlm start` e `rlm setup` agora recusam runtime < 3.11 |

### O que ainda falta

| Item | Prioridade | Motivo |
|---|---|---|
| Instalação totalmente reproduzível por plataforma | 🔴 Alta | Ainda depende demais do ambiente local já estar razoável |
| Pacote/distribuição mais opinado | 🟡 Média | Ainda é checkout-first, não experiência de produto empacotado |

### Juízo prático

Sem resolver esse gap, qualquer avanço em canal, UI ou skill vira custo de
suporte. A primeira prioridade operacional continua sendo: máquina certa,
Python certo, `.env` consistente e bootstrap repetível.

---

## Onde o RLM já tem paridade ou vantagem

| Capacidade | OpenClaw | RLM | Notas |
|---|---|---|---|
| Execução de código | ✅ REPL local | ✅ REPL local + Docker + Modal + Daytona | RLM tem mais backends |
| Raciocínio multi-passo | ✅ loop linear | ✅ `sub_rlm()` + MCTS | Vantagem do RLM |
| Decomposição de tarefas | ✅ tools/skills | ✅ `sub_rlm()` + `sub_rlm_parallel()` | Vantagem do RLM |
| Navegação web | ✅ | ✅ `browser.py` | Paridade funcional |
| MCP | ✅ | ✅ `mcp_client.py` | Paridade |
| Scheduler | ✅ | ✅ `scheduler.py` | Paridade |
| Multi-provider LLM | ✅ | ✅ OpenAI, Anthropic, Gemini, Azure, Portkey, LiteLLM | Paridade |
| Telegram | ✅ | ✅ | Paridade |
| Discord | ✅ | ✅ | Existe gateway webhook-first |
| WhatsApp | ✅ | ✅ | Existe gateway Meta Cloud API |
| Slack | ✅ | ✅ | Existe gateway Events API |
| WebChat | ✅ | ✅ | Existe frontend integrado com SSE |
| OpenAI-compatible API | ✅ | ✅ `/v1/chat/completions` | Paridade |
| Doctor básico | ✅ | ✅ | Já existe comando de diagnóstico |
| Update básico | ✅ | ✅ | Implementado com git fast-forward + uv sync |
| Sanitização de memória/web | parcial | ✅ | RLM está mais blindado |

---

## Gap 1 — Onboarding e operação dos canais

**Status:** continua crítico.

O gap não é mais “ter canal”. O gap agora é conseguir colocar os canais em pé
sem arqueologia manual, com validação clara e com mensagens de erro úteis.

### O que já existe

```
rlm/plugins/channel_registry.py  → roteamento unificado de saída
rlm/server/telegram_gateway.py   → Telegram
rlm/server/discord_gateway.py    → Discord Interactions
rlm/server/whatsapp_gateway.py   → WhatsApp Cloud API
rlm/server/slack_gateway.py      → Slack Events API
rlm/server/webchat.py            → WebChat integrado
rlm/cli/main.py                  → doctor + channel list
```

### O que ainda falta

| Item | Prioridade | Motivo |
|---|---|---|
| Pairing/setup guiado por canal | 🔴 Alta | Hoje ainda depende de editar `.env` manualmente |
| Checks profundos por canal no `rlm doctor` | 🔴 Alta | O doctor atual valida presença de env e saúde geral, mas não faz handshake rico por canal |
| Mensagens operacionais mais acionáveis | 🟡 Média | Falhas de assinatura/webhook ainda exigem leitura de código/log |
| Streaming parcial fora do WebChat | 🟡 Média | WebChat já entrega SSE; outros canais ainda são mais fechados |
| Novos canais (Signal, Matrix) | 🟢 Baixa | Expansão, não bloqueio |

### Juízo prático

Se o objetivo é “usar estilo OpenClaw”, o trabalho rentável agora é UX
operacional dos canais já existentes, não criar mais integrações por criar.

---

## Gap 2 — UI de produto, não só WebChat

**Status:** gap parcialmente fechado.

O WebChat mínimo já existe. O que falta não é “ter alguma interface”; é ter uma
interface que mostre execução, artefatos e estado do agente de forma útil.

### O que já existe

```
rlm/server/webchat.py        → endpoints webchat + SSE
rlm/static/webchat.html      → frontend single-page
rlm/server/openai_compat.py  → backend compatível com OpenAI SDK
rlm/server/api.py            → FastAPI + docs
```

### O que falta

| Item | Prioridade | Motivo |
|---|---|---|
| Artifacts/canvas | 🔴 Alta | Não há camada visual rica para tabelas, arquivos, gráficos, previews |
| Timeline de execução | 🟡 Média | Ainda falta uma UI que mostre eventos, tools, subagentes e estado |
| Streaming visual rico | 🟡 Média | O WebChat simula streaming de saída final, mas não expõe o processo inteiro |
| Apps móveis nativos | 🟢 Baixa | Útil, mas não é o gargalo atual |

---

## Gap 3 — Ecossistema de skills

**Status:** gap parcialmente fechado.

Instalação remota já existe. O que falta é transformar isso em ecossistema
confiável, versionado e publicável.

### O que já existe

```
rlm/skills/               → skills locais
rlm/core/skill_loader.py  → carregamento automático
rlm/cli/main.py           → `rlm skill list` e `rlm skill install`
```

### O que falta

| Item | Prioridade | Motivo |
|---|---|---|
| `rlm skill publish` | 🟡 Média | Ainda não há fluxo de publicação |
| Registry público | 🟡 Média | GitHub cobre instalação mínima, não descoberta nativa |
| Verificação de integridade | 🔴 Alta | Instalação remota sem hash/assinatura é superfície de risco |
| Update de skills | 🟡 Média | Não há fluxo explícito de upgrade de skill instalada |

---

## Gap 4 — Multi-user e permissões

**Status:** aberto.

O sistema já isola sessão por `client_id`, mas ainda não é multi-tenant de
verdade no sentido operacional.

### O que já existe

```
rlm/core/session.py            → SessionManager por client_id
rlm/server/telegram_gateway.py → allowlist básica
rlm/server/ws_server.py        → autenticação por token global
```

### O que falta

| Item | Prioridade | Motivo |
|---|---|---|
| Tokens por usuário | 🔴 Alta se houver exposição externa | Hoje o token é essencialmente global |
| Quotas por usuário | 🟡 Média | Não há rate limit individual robusto |
| Perfis de permissão | 🔴 Alta | Faltam políticas finas para código, FS e tools |
| Pairing flow | 🔴 Alta | Ainda não há fluxo de pareamento “produto” |

---

## Gap 5 — Lifecycle por sessão e teardown operacional

**Status:** parcialmente resolvido nesta rodada.

O RLM já isolava sessão em nível lógico. O buraco era operacional: recursos
criados durante a sessão precisavam morrer com a sessão, e não apenas no
shutdown global do processo.

### O que já existe agora

```
rlm/core/session.py       → callbacks de fechamento por sessão
rlm/core/skill_loader.py  → `deactivate_scope(...)` para MCP por escopo
rlm/plugins/mcp.py        → fechamento por cache key e por scope
rlm/server/api.py         → teardown MCP ligado ao `session.session_id`
```

### O que isso resolve

| Item | Estado atual | Motivo |
|---|---|---|
| MCP escopado por sessão | ✅ | Cada sessão pode ativar namespaces próprios |
| Teardown ao fechar sessão | ✅ | As skills MCP daquele escopo agora são fechadas no close da sessão |
| Shutdown global | ✅ | Continua existindo como rede de segurança |

### O que ainda falta

| Item | Prioridade | Motivo |
|---|---|---|
| Health check/reconnect de subprocessos MCP | ✅ parcial | Cliente MCP agora tenta reconnect e expõe health check básico |
| Telemetria explícita de vazamento por sessão | 🟡 Média | Não há visão consolidada de recursos vivos por sessão |
| Teardown mais amplo além de MCP | 🟡 Média | Outros recursos transitórios ainda dependem de disciplina de implementação |

### Juízo prático

Esse fechamento era necessário para sair do estágio “funciona” e entrar em
“fica de pé por horas ou dias sem acumular lixo operacional”. Ainda não é o fim
do trabalho, mas elimina um vazamento estrutural real.

---

## Gap 6 — Doctor mais profundo e manutenção contínua

**Status:** parcialmente resolvido.

Nesta rodada, `rlm update` foi implementado para cobrir o buraco mais óbvio de
manutenção. Ele faz validação de checkout git, `fetch`, `pull --ff-only`,
`uv sync` e restart opcional dos serviços.

### O que já existe agora

```bash
`rlm doctor         # diagnóstico de runtime, .env, LLM, servidor e canais
rlm update         # git fetch + pull --ff-only + uv sync + restart opcional
rlm update --check # só verifica se há commits remotos pendentes
```

Além do básico, o `doctor` agora já acusa:

- Python incompatível com o baseline do projeto
- ausência de `uv` para setup/update automatizado
- canais com configuração parcial em vez de apenas “tem/não tem env”
- falta de `RLM_WS_TOKEN` quando integrações dependem do WebSocket interno
- handshake real por canal quando isso é tecnicamente viável

Hoje o `doctor` já faz tentativas reais de validação em vez de só olhar env:

- Telegram via `getMe`
- Slack via `auth.test` ou `url_verification` local
- WhatsApp via `hub.challenge` local ou Graph API
- Discord via API com `DISCORD_BOT_TOKEN` ou `PING` local quando `skip verify` permite

### O que ainda falta

| Item | Prioridade | Motivo |
|---|---|---|
| `doctor` por backend LLM | 🟡 Média | Hoje o teste ativo é mais forte com OpenAI do que com os demais |
| `doctor` por canal com assinatura completa | 🟡 Média | Ainda faltam checks que simulem fluxos assinados completos, especialmente Discord com Ed25519 real |
| `update` com canais stable/beta/dev | 🟢 Baixa | Útil, mas não é bloqueio imediato |
| `doctor` em formato máquina | 🟢 Baixa | JSON ajudaria automação e UI futura |

---

## Prioridade realista daqui para frente

```text
Prioridade 1: INSTALAÇÃO E ONBOARDING
  - baseline rígido de runtime/distribuição
  - aprofundar `rlm doctor`
  - pairing/setup guiado por canal
  - mensagens operacionais melhores

Prioridade 2: LIFECYCLE E OPERAÇÃO CONTÍNUA
  - reconnect de MCP mais observável e menos heurístico
  - teardown ampliado por sessão
  - observabilidade operacional por escopo

Prioridade 3: UI DE PRODUTO
  - artifacts/canvas
  - timeline de execução
  - streaming visual mais rico

Prioridade 4: SKILLS E SEGURANÇA DE DISTRIBUIÇÃO
  - `rlm skill publish`
  - hash/assinatura
  - update de skills

Prioridade 5: MULTI-USER REAL
  - tokens por usuário
  - quotas
  - permissões finas
  - pairing flow
```

---

## O que já foi resolvido nesta rodada

| Ação | Resultado |
|---|---|
| Auditoria do documento | gaps antigos corrigidos por evidência de código |
| `rlm update` | implementado na CLI |
| Fluxo de update seguro | git limpo obrigatório, fast-forward only, `uv sync`, restart opcional |
| Teardown MCP por sessão | implementado com cleanup por escopo no fechamento da sessão |
| `rlm doctor` mais acionável | agora acusa runtime incompatível, falta de `uv` e canais incompletos |
| Handshake real no `doctor` | implementado com validação ativa por canal quando possível |
| Runtime guard em `start/setup` | comandos agora recusam Python abaixo do baseline do projeto |
| Reconnect MCP | cliente MCP tenta recuperar transporte degradado e expõe health check básico |
| Cobertura de testes | parser, dispatch e service update cobertos |

---

## O que o RLM tem que o OpenClaw não tem

Para não degradar a estratégia: o RLM não deve sacrificar seus diferenciais em
troca de paridade superficial.

| Capacidade RLM | Por que importa |
|---|---|
| Recursão real com `max_depth > 1` | divide e conquista tarefas grandes |
| MCTS | explora ramos antes de decidir |
| `sub_rlm()` explícito no REPL | delegação controlada pelo próprio agente |
| Sanitização de memória | reduz prompt injection vindo do banco de memória |
| Sanitização de conteúdo web | reduz prompt injection vindo da web |
| Ambientes remotos de sandbox | execução isolada além do host local |
| SIF + seleção dinâmica de skills | injeção de skills por relevância, não só configuração estática |

O trabalho correto daqui em diante é produto e operação, não amputação do que
já faz o RLM ser tecnicamente mais forte.

---

## Documento relacionado

Para a trilha específica de iPhone e Android, incluindo PWA, app companion e
futuro on-device, ver [docs/roadmap-mobile-ios-android.md](c:\Users\demet\Desktop\agente proativo\RLM_OpenClaw_Engine\rlm-main\docs\roadmap-mobile-ios-android.md).
