# Arquitetura de Configuração Multi-Dispositivo — RLM-Main

> **Data:** Março 2026  
> **Status:** Documento de referência e roadmap arquitetural  
> **Contexto:** Diagnóstico do estado atual + plano para suportar escritório, casa (ESP32), celular e carro simultâneos

---

## Índice

1. [Estado atual](#1-estado-atual)
2. [Problema de segurança imediato](#2-problema-de-segurança-imediato)
3. [Por que `.env` não escala para multi-dispositivo](#3-por-que-env-não-escala-para-multi-dispositivo)
4. [Arquitetura proposta em 4 camadas](#4-arquitetura-proposta-em-4-camadas)
5. [Camada 1 — `rlm.toml` (config estruturada)](#5-camada-1--rlmtoml-config-estruturada)
6. [Camada 2 — `.env` (apenas secrets)](#6-camada-2--env-apenas-secrets)
7. [Camada 3 — SQLite `clients` (identidade dinâmica)](#7-camada-3--sqlite-clients-identidade-dinâmica)
8. [Camada 4 — JWT por requisição (contexto portátil)](#8-camada-4--jwt-por-requisição-contexto-portátil)
9. [Plano de migração incremental](#9-plano-de-migração-incremental)
10. [Alinhamento com código existente](#10-alinhamento-com-código-existente)

---

## 1. Estado Atual

### Variáveis de ambiente (`.env`)

```dotenv
# Secrets LLM
OPENAI_API_KEY=sk-proj-...   ← chave real, único ponto de falha

# Runtime
RLM_MODEL=gpt-4o-mini
RLM_WS_TOKEN=7da159b8...     ← token único para TODOS os clientes WebSocket
RLM_HOOK_TOKEN=36223f0d...   ← token único para TODOS os webhooks

# Opcionais (canais)
DISCORD_APP_PUBLIC_KEY=...
WHATSAPP_VERIFY_TOKEN=...
SLACK_BOT_TOKEN=...
SLACK_SIGNING_SECRET=...
TELEGRAM_BOT_TOKEN=...
```

**Limitação central:** um único `RLM_WS_TOKEN` autentica qualquer cliente.
Escritório, ESP32, celular e carro usam o mesmo token — não há diferenciação.

### Schema SQLite atual

Arquivo: `rlm/core/session.py` (linhas 97–123)

```sql
-- Sessões de agente
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    client_id         TEXT NOT NULL,         -- identifica o cliente ("default", "user_abc")
    status            TEXT DEFAULT 'idle',   -- idle|running|completed|aborted|error
    created_at        TEXT NOT NULL,
    last_active       TEXT NOT NULL,
    state_dir         TEXT NOT NULL,         -- ./rlm_states/<session_id>/
    total_completions INTEGER DEFAULT 0,
    total_tokens_used INTEGER DEFAULT 0,
    last_error        TEXT DEFAULT '',
    metadata          TEXT DEFAULT '{}'      -- JSON blob livre
);

CREATE INDEX IF NOT EXISTS idx_sessions_client_id ON sessions(client_id);

-- Log de eventos por sessão
CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
```

### Autenticação atual

| Ponto de entrada | Arquivo | Método |
|---|---|---|
| WebSocket | `rlm/server/ws_server.py:151–210` | `hmac.compare_digest(token, RLM_WS_TOKEN)` |
| Webhook | `rlm/server/webhook_dispatch.py:146–182` | `hmac.compare_digest(token, RLM_HOOK_TOKEN)` |
| OpenAI-compat API | `rlm/server/openai_compat.py` | `hmac.compare_digest(token, RLM_API_TOKEN)` |

Todos os pontos usam **um único token global** sem diferenciação de cliente.

### Variáveis consumidas em runtime

Declaradas e lidas em `rlm/server/api.py` (linhas 82–183):

| Variável | Default | Tipo esperado |
|---|---|---|
| `RLM_DB_PATH` | `rlm_sessions.db` | path |
| `RLM_STATE_ROOT` | `./rlm_states` | path |
| `RLM_MODEL` | `gpt-4o-mini` | string |
| `RLM_MAX_ITERATIONS` | `30` | int |
| `RLM_TIMEOUT` | `120` | int (segundos) |
| `RLM_MAX_ERRORS` | `5` | int |
| `RLM_EXEC_APPROVAL_TIMEOUT` | `60` | int (segundos) |
| `RLM_EXEC_APPROVAL_REQUIRED` | `false` | bool |
| `RLM_SKILLS_DIR` | `../skills` | path |

**Problema:** todos lidos com `os.getenv()` retornando `str | None`. Um `RLM_TIMEOUT=abc`
falha com `ValueError` 200 linhas depois da leitura, sem mensagem clara da causa.

---

## 2. Problema de Segurança Imediato

> ⚠️ **AÇÃO NECESSÁRIA AGORA**

O arquivo `.env` contém `OPENAI_API_KEY=sk-proj-...` com uma chave real.
Se este repositório for commitado (mesmo em repo privado), a chave fica exposta
em histórico de git e pode ser extraída de backups, forks, ou logs de CI/CD.

**Ação imediata:**

```bash
# 1. Revogar a chave atual em: https://platform.openai.com/api-keys

# 2. Garantir que .env está no .gitignore
echo ".env" >> .gitignore

# 3. Criar .env.example com placeholders (comitar este)
cp .env .env.example
# editar .env.example: trocar valores reais por placeholders
# ex: OPENAI_API_KEY=sk-proj-SUA_CHAVE_AQUI
```

**Verificar se chave já foi commitada:**
```bash
git log --all --full-history -- .env
git grep "sk-proj-" $(git rev-list --all)
```

---

## 3. Por que `.env` Não Escala para Multi-Dispositivo

### O que está sendo descrito

```
Escritório (PC) ──────────────────┐
Casa (ESP32 ×N) ──────────────────┤──→  rlm-main daemon  ──→  LLM
Celular (iOS/Android) ────────────┤      (processo único)
Carro (sistema embarcado) ────────┘
```

Cada origem tem necessidades **diferentes e simultâneas**:

| Cliente | Contexto | Permissões ideais | Resposta esperada |
|---|---|---|---|
| Escritório | Trabalho, código, documentos | Tudo | Elaborada, técnica |
| ESP32 sala | Sensor de temperatura/umidade | Só leitura de sensores | Curta, estruturada (JSON) |
| ESP32 jardim | Controle de irrigação | Leitura + GPIO output | Comando direto |
| Celular | Uso pessoal mobile | Search, reminder, agenda | Concisa, rápida |
| Carro | Navegação, mãos livres | Apenas áudio/voz | Mínima, segura |

### Por que `.env` não representa isso

**1. Sem hierarquia — tudo é string plana**
```bash
# Como descrever que ESP32-sala pode ler sensores mas não executar código?
# Como garantir que o cliente "carro" só recebe respostas curtas?
# Não há como. Tudo vira gambiarra:
ESP32_SALA_TOKEN=abc         # token diferente? Mas o código só lê RLM_WS_TOKEN
ESP32_SALA_PERMISSIONS=read  # string que o código ignora completamente
```

**2. Sem arrays — múltiplos dispositivos do mesmo tipo impossível**
```bash
# 3 ESP32s? Não há sintaxe padronizada para isso em .env:
ESP32_1_TOKEN=abc
ESP32_2_TOKEN=def
ESP32_3_TOKEN=ghi
# Quantos existem? O código precisa tentar ESP32_1..N até parar de encontrar.
# Isso não é config — é um protocolo inventado sem garantias.
```

**3. Sem tipagem — erros silenciosos**
```python
# .env retorna sempre string
timeout = os.getenv("RLM_TIMEOUT")  # → "abc" se alguém errou
# 200 linhas depois:
await asyncio.wait_for(coro, timeout=int(timeout))  # ValueError aqui
# Onde está o problema? No .env. Quando apareceu? Muito depois.
```

**4. Token único — sem isolamento de sessão por dispositivo**
```
ESP32-sala  ──→ token: 7da159b8  ──→ client_id: "default"
Escritório  ──→ token: 7da159b8  ──→ client_id: "default"
```
Ambos chegam com o mesmo `client_id: "default"` e **compartilham a mesma sessão**.
O agente usa o contexto misturado dos dois — o que é ambíguo e incorreto.

---

## 4. Arquitetura Proposta em 4 Camadas

```
┌─────────────────────────────────────────────────────────────────┐
│  CAMADA 4 — JWT por requisição                                  │
│  "Quem é esta requisição e o que pode fazer"                    │
│  Gerado na emissão do token do dispositivo, validado no handler │
├─────────────────────────────────────────────────────────────────┤
│  CAMADA 3 — SQLite: tabela `clients`                            │
│  "Quais dispositivos existem e suas configurações"              │
│  Dinâmico: adicionar ESP32 = 1 INSERT, sem restart              │
├─────────────────────────────────────────────────────────────────┤
│  CAMADA 2 — `.env` (apenas secrets)                             │
│  "Chaves que nunca entram no controle de versão"                │
│  OPENAI_API_KEY, JWT_SECRET, RLM_MASTER_TOKEN                   │
├─────────────────────────────────────────────────────────────────┤
│  CAMADA 1 — `rlm.toml` (config estruturada)                     │
│  "Como o sistema se comporta por padrão e por perfil"           │
│  Pode ser commitado (sem secrets), versionado, revisado em PR   │
└─────────────────────────────────────────────────────────────────┘
```

**Regra de sobrescrita:** camadas superiores sobrescrevem as inferiores.
A requisição JWT de um ESP32 sobrescreve o perfil padrão do `rlm.toml`.

---

## 5. Camada 1 — `rlm.toml` (config estruturada)

### Por que TOML e não JSON5 ou YAML

| Critério | TOML | JSON5 | YAML |
|---|---|---|---|
| Built-in Python 3.11+ | ✓ (`tomllib`) | ✗ (dep. `json5`) | ✗ (dep. `pyyaml`) |
| Legível por humanos | ✓ | ✓ | ✓ |
| Suporta comentários | ✓ | ✓ | ✓ |
| Arrays de objetos | ✓ (`[[array]]`) | ✓ | ✓ |
| Ambiguidade de indentação | ✗ | ✗ | ✓ (problema) |
| Pode ser commitado | ✓ (sem secrets) | ✓ | ✓ |

### Schema proposto — `rlm.toml`

```toml
# rlm.toml — Configuração estruturada do RLM-Main
# NÃO colocar secrets aqui. Secrets ficam no .env
# Este arquivo PODE ser commitado no repositório.

# ─────────────────────────────────────────────────────────────────
# Servidor
# ─────────────────────────────────────────────────────────────────
[server]
host    = "0.0.0.0"
port    = 8000
db_path = "rlm_sessions.db"      # pode ser path absoluto ou relativo ao cwd
state_root = "./rlm_states"
skills_dir = "./rlm/skills"

# ─────────────────────────────────────────────────────────────────
# Agente — defaults globais (aplicados a todos os perfis)
# ─────────────────────────────────────────────────────────────────
[agent]
model          = "gpt-4o-mini"
max_iterations = 30
timeout        = 120              # segundos
max_errors     = 5

# ─────────────────────────────────────────────────────────────────
# Aprovação de execução (gate de segurança)
# ─────────────────────────────────────────────────────────────────
[exec_approval]
required = false
timeout  = 60

# ─────────────────────────────────────────────────────────────────
# Perfis de cliente
# Cada [[profiles]] define um tipo de cliente e suas características.
# Dispositivos no banco `clients` referenciam um perfil por nome.
# ─────────────────────────────────────────────────────────────────

[[profiles]]
name        = "desktop"
description = "PC de trabalho — acesso completo"
model       = "gpt-4o"            # sobrescreve [agent].model
max_iterations = 50
context_hint = "Ambiente de trabalho profissional. Respostas detalhadas e técnicas são preferidas."
permissions  = [
    "code_exec", "file_read", "file_write",
    "search", "email", "calendar",
    "memory_read", "memory_write"
]
response_style = "detailed"       # detailed | concise | minimal | structured_json

[[profiles]]
name        = "iot"
description = "Dispositivos ESP32 e embarcados"
model       = "gpt-4o-mini"
max_iterations = 10               # respostas mais rápidas
timeout     = 30
context_hint = "Dispositivo IoT embarcado. Responda SEMPRE em JSON válido. Sem markdown, sem texto livre."
permissions  = ["sensor_read", "gpio_control", "mqtt_publish"]
response_style = "structured_json"
max_response_tokens = 200         # mantém resposta curta para memória limitada

[[profiles]]
name        = "mobile"
description = "Celular — uso pessoal móvel"
model       = "gpt-4o-mini"
max_iterations = 20
context_hint = "Uso móvel. Respostas concisas. Sem blocos de código longos."
permissions  = ["search", "reminder", "memory_read", "memory_write"]
response_style = "concise"

[[profiles]]
name        = "vehicle"
description = "Sistema embarcado de carro — mãos livres"
model       = "gpt-4o-mini"
max_iterations = 5
timeout     = 15
context_hint = "Usuário dirigindo. Respostas em 1-2 frases. Nunca liste itens. Nunca peça confirmação."
permissions  = ["navigation", "search"]
response_style = "minimal"
exec_approval_required = true     # qualquer ação requer aprovação explícita

# ─────────────────────────────────────────────────────────────────
# Canais (habilitados quando ENV vars correspondentes existem)
# ─────────────────────────────────────────────────────────────────
[channels]
# Cada canal é ativado condicionalmente pelo api.py quando as ENVs
# correspondentes existem. Este bloco documenta os parâmetros opcionais.

[channels.telegram]
disabled = false

[channels.discord]
disabled = false

[channels.whatsapp]
disabled = false

[channels.slack]
disabled = false

[channels.webchat]
disabled = false
cors_origins = ["*"]              # em produção: restringir ao domínio
```

### Leitura no código (Python 3.11+)

```python
# rlm/core/config.py — módulo a criar
import os
import tomllib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AgentConfig:
    model: str = "gpt-4o-mini"
    max_iterations: int = 30
    timeout: int = 120
    max_errors: int = 5

@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    db_path: str = "rlm_sessions.db"
    state_root: str = "./rlm_states"
    skills_dir: str = "./rlm/skills"

@dataclass
class ProfileConfig:
    name: str
    description: str = ""
    model: str = "gpt-4o-mini"
    max_iterations: int = 30
    timeout: int = 120
    context_hint: str = ""
    permissions: list[str] = field(default_factory=list)
    response_style: str = "detailed"
    max_response_tokens: int = 2048
    exec_approval_required: bool = False

@dataclass
class RLMConfig:
    server: ServerConfig
    agent: AgentConfig
    profiles: dict[str, ProfileConfig]       # indexado por name
    raw: dict[str, Any] = field(default_factory=dict)

def load_config(toml_path: str = "rlm.toml") -> RLMConfig:
    path = Path(toml_path)
    raw: dict = {}

    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)             # built-in Python 3.11+

    # Aplica .env sobre o TOML (layer 2 sobrescreve layer 1)
    server_raw = raw.get("server", {})
    agent_raw  = raw.get("agent", {})

    server = ServerConfig(
        host       = os.getenv("RLM_HOST",       server_raw.get("host",       "0.0.0.0")),
        port       = int(os.getenv("RLM_PORT",   str(server_raw.get("port",   8000)))),
        db_path    = os.getenv("RLM_DB_PATH",    server_raw.get("db_path",    "rlm_sessions.db")),
        state_root = os.getenv("RLM_STATE_ROOT", server_raw.get("state_root", "./rlm_states")),
        skills_dir = os.getenv("RLM_SKILLS_DIR", server_raw.get("skills_dir", "./rlm/skills")),
    )
    agent = AgentConfig(
        model          = os.getenv("RLM_MODEL",          agent_raw.get("model",          "gpt-4o-mini")),
        max_iterations = int(os.getenv("RLM_MAX_ITERATIONS", str(agent_raw.get("max_iterations", 30)))),
        timeout        = int(os.getenv("RLM_TIMEOUT",        str(agent_raw.get("timeout",        120)))),
        max_errors     = int(os.getenv("RLM_MAX_ERRORS",     str(agent_raw.get("max_errors",     5)))),
    )

    profiles = {}
    for p in raw.get("profiles", []):
        pc = ProfileConfig(**{k: v for k, v in p.items() if k in ProfileConfig.__dataclass_fields__})
        profiles[pc.name] = pc

    # Garante perfil "default" sempre presente
    if "default" not in profiles:
        profiles["default"] = ProfileConfig(name="default")

    return RLMConfig(server=server, agent=agent, profiles=profiles, raw=raw)
```

---

## 6. Camada 2 — `.env` (Apenas Secrets)

Após a migração, o `.env` deve conter **exclusivamente** valores que nunca devem
aparecer em código ou histórico de git:

```dotenv
# .env — NUNCA commitar. Adicionado ao .gitignore.

# ── Secrets de API ──────────────────────────────────────────────
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...       # se usar Claude

# ── Secret de assinatura JWT ────────────────────────────────────
# Gerado com: python -c "import secrets; print(secrets.token_hex(32))"
RLM_JWT_SECRET=<64 chars hex>

# ── Token mestre (emite tokens de cliente via CLI) ───────────────
RLM_MASTER_TOKEN=<64 chars hex>

# ── Secrets de canais ────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=...
DISCORD_BOT_TOKEN=...
WHATSAPP_API_TOKEN=...
SLACK_BOT_TOKEN=...
SLACK_SIGNING_SECRET=...
```

**Tudo que é configuração comportamental** (model, timeouts, permissões) vai para
`rlm.toml` — que pode ser commitado e revisado em pull requests.

---

## 7. Camada 3 — SQLite `clients` (Identidade Dinâmica)

### Por que SQLite e não mais uma tabela no `.env`

- Adicionar um novo ESP32 é um `INSERT` — sem editar arquivo, sem restart do daemon
- Revogar um dispositivo é um `UPDATE active=0` — imediato, sem deploy
- Auditoria de `last_seen` por dispositivo incluída naturalmente
- Já existe conexão SQLite no projeto (`rlm_sessions.db`)

### Schema proposto — adicionar ao `session.py`

```sql
-- Identidade de cada dispositivo/cliente
CREATE TABLE IF NOT EXISTS clients (
    id              TEXT PRIMARY KEY,      -- "esp32-sala", "iphone-demet", "pc-escritorio"
    token_hash      TEXT NOT NULL,         -- SHA-256(token_raw) — nunca guardar o token em claro
    profile         TEXT NOT NULL,         -- referencia [[profiles]] no rlm.toml
    description     TEXT DEFAULT '',       -- "ESP32 sala de estar — DHT22 + relay"
    context_hint    TEXT DEFAULT '',       -- sobrescreve o context_hint do perfil
    permissions     TEXT DEFAULT '[]',     -- JSON array — sobrescreve perfil se não vazio
    active          INTEGER DEFAULT 1,     -- 0 = revogado (não deleta para manter auditoria)
    created_at      TEXT NOT NULL,
    last_seen       TEXT,                  -- atualizado a cada requisição autenticada
    metadata        TEXT DEFAULT '{}'      -- JSON livre: {"location": "sala", "firmware": "1.2.3"}
);

CREATE INDEX IF NOT EXISTS idx_clients_active ON clients(active);
```

### Autenticação com a tabela `clients`

```python
# rlm/core/auth.py — módulo a criar
import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass

@dataclass
class ClientIdentity:
    client_id: str
    profile: str
    context_hint: str
    permissions: list[str]

def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()

def authenticate_client(db_path: str, raw_token: str) -> ClientIdentity | None:
    """
    Verifica o token contra a tabela clients.
    Retorna ClientIdentity se válido e ativo, None caso contrário.
    Atualiza last_seen automaticamente.
    """
    token_hash = _hash_token(raw_token)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, profile, context_hint, permissions
            FROM clients
            WHERE token_hash = ? AND active = 1
            """,
            (token_hash,)
        ).fetchone()

        if row is None:
            return None

        client_id, profile, context_hint, perms_json = row
        permissions = json.loads(perms_json or "[]")

        # Atualiza last_seen sem abrir nova conexão
        conn.execute(
            "UPDATE clients SET last_seen = datetime('now') WHERE id = ?",
            (client_id,)
        )
        conn.commit()

    return ClientIdentity(
        client_id=client_id,
        profile=profile,
        context_hint=context_hint,
        permissions=permissions,
    )
```

### Gerenciamento via CLI

```bash
# Adicionar novo dispositivo
rlm client add esp32-sala \
    --profile iot \
    --description "ESP32 sala de estar — DHT22 + relay" \
    --context "Sensor de temperatura (DHT22) e controle de relay para ventilador"

# Saída:
# ✓ Cliente 'esp32-sala' criado
# Token: rlm_c1k_7f3a9b2e...   ← copie agora, não será exibido novamente

# Listar dispositivos ativos
rlm client list

# Revogar sem deletar histórico
rlm client revoke esp32-sala

# Ver última atividade
rlm client status esp32-sala
```

---

## 8. Camada 4 — JWT por Requisição (Contexto Portátil)

### Quando JWT faz sentido

Para clientes que fazem **muitas requisições pequenas e independentes** (ESP32 enviando
leitura de sensor a cada 30s), consultar o SQLite em cada requisição adiciona latência.
O JWT resolve isso: o contexto viaja **dentro do token**, o servidor só precisa verificar
a assinatura criptográfica — sem I/O.

### Estrutura do JWT para o RLM

```python
# Header (padrão)
{
    "alg": "HS256",
    "typ": "JWT"
}

# Payload — claims do dispositivo
{
    "sub":         "esp32-sala",          # client_id
    "profile":     "iot",                 # perfil de comportamento
    "permissions": ["sensor_read", "gpio_control"],
    "context":     "ESP32 sala de estar, sensor DHT22, relay ventilador",
    "iss":         "rlm-main",            # quem emitiu
    "iat":         1709856000,            # issued at (unix timestamp)
    "exp":         1709942400             # expira em 24h (configurável por perfil)
}
```

### Validação no handler

```python
# rlm/core/auth.py (continuação)
import jwt                  # PyJWT — adicionar ao pyproject.toml
import os

def validate_jwt(token: str) -> ClientIdentity | None:
    """
    Valida JWT assinado com RLM_JWT_SECRET.
    Não acessa banco — contexto está nos claims.
    """
    secret = os.environ["RLM_JWT_SECRET"]
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["sub", "profile", "iss", "exp"]},
        )
    except jwt.ExpiredSignatureError:
        return None           # token expirado — cliente deve renovar
    except jwt.InvalidTokenError:
        return None

    return ClientIdentity(
        client_id=payload["sub"],
        profile=payload["profile"],
        context_hint=payload.get("context", ""),
        permissions=payload.get("permissions", []),
    )
```

### Fluxo completo de autenticação

```
ESP32              rlm-main              SQLite
  │                   │                    │
  │──── POST /auth ──→│                    │
  │  {token: "abc"}   │──── SELECT ───────→│
  │                   │←── ClientIdentity ─│
  │←── JWT (24h) ─────│                    │
  │                   │                    │
  │  (próximas 24h)   │                    │
  │──── POST / ──────→│                    │
  │  Authorization:   │── jwt.decode() ──→ │ (sem I/O)
  │  Bearer <jwt>     │← ClientIdentity ──　│
  │                   │                    │
  │  (após expirar)   │                    │
  │──── POST / ──────→│                    │
  │                   │── 401 Expired ─→   │
  │←── 401 ───────────│                    │
  │  (renovar via /auth)                   │
```

---

## 9. Plano de Migração Incremental

> Regra: cada etapa é independente e não quebra o que existe.

### Etapa 0 — AGORA (urgente, 30 minutos)

```bash
# 1. Revogar OPENAI_API_KEY atual em platform.openai.com/api-keys
# 2. Gerar nova chave e atualizar .env
# 3. Criar .env.example com placeholders
# 4. Confirmar .env no .gitignore
echo ".env" >> .gitignore
git rm --cached .env 2>/dev/null || true   # remove do index se já commitado
```

### Etapa 1 — Backup do `.env` (2 horas)

Adicionar ao `cmd_doctor` o backup automático antes de qualquer escrita no `.env`:

```python
# Sempre que algo for escrever no .env:
import shutil
from pathlib import Path

def backup_env(env_path: str = ".env") -> None:
    p = Path(env_path)
    if p.exists():
        shutil.copy2(p, p.with_suffix(".env.bak"))
```

### Etapa 2 — Validação Pydantic na inicialização (1 dia)

```python
# rlm/core/config.py
from pydantic import BaseModel, field_validator
import os

class EnvConfig(BaseModel):
    openai_api_key: str
    rlm_model: str = "gpt-4o-mini"
    rlm_ws_token: str = ""
    rlm_hook_token: str = ""
    rlm_timeout: int = 120
    rlm_max_iterations: int = 30

    @field_validator("rlm_ws_token", "rlm_hook_token")
    @classmethod
    def token_long_enough(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("Token deve ter ao menos 32 caracteres")
        return v

    @classmethod
    def from_env(cls) -> "EnvConfig":
        return cls(
            openai_api_key    = os.environ["OPENAI_API_KEY"],    # KeyError c/ mensagem clara
            rlm_model         = os.getenv("RLM_MODEL", "gpt-4o-mini"),
            rlm_ws_token      = os.getenv("RLM_WS_TOKEN", ""),
            rlm_hook_token    = os.getenv("RLM_HOOK_TOKEN", ""),
            rlm_timeout       = int(os.getenv("RLM_TIMEOUT", "120")),
            rlm_max_iterations= int(os.getenv("RLM_MAX_ITERATIONS", "30")),
        )

# Em api.py, no lifespan startup:
try:
    env_config = EnvConfig.from_env()
except (KeyError, ValueError) as e:
    print(f"[FATAL] Configuração inválida: {e}")
    raise SystemExit(1)
```

### Etapa 3 — Migrar para `rlm.toml` (1 semana)

1. Criar `rlm.toml` com os perfis acima
2. Criar `rlm/core/config.py` com `load_config()`
3. Substituir `os.getenv()` espalhados pelo `api.py` por `cfg = load_config()`
4. Testes: verificar que 847 testes continuam passando

### Etapa 4 — Tabela `clients` no SQLite (1-2 semanas)

1. Adicionar migration em `session.py` para criar tabela `clients`
2. Criar `rlm/core/auth.py` com `authenticate_client()`
3. Modificar `ws_server.py` e `webhook_dispatch.py` para usar `authenticate_client()`
   em vez de comparar com token global único
4. Adicionar comandos `rlm client add/list/revoke/status` ao CLI

### Etapa 5 — JWT (quando tiver 3+ dispositivos ativos)

1. Adicionar `pyjwt` ao `pyproject.toml`
2. Adicionar `POST /auth` endpoint que recebe token de cliente e retorna JWT
3. Modificar handlers para aceitar tanto token direto quanto JWT
4. Configurar TTL de JWT por perfil no `rlm.toml`

---

## 10. Alinhamento com Código Existente

### O que muda nos arquivos atuais

| Arquivo | Mudança | Etapa |
|---|---|---|
| `rlm/core/session.py` | Adicionar tabela `clients` + migration | 4 |
| `rlm/server/api.py` | Substituir `os.getenv()` por `load_config()` | 3 |
| `rlm/server/ws_server.py` | `hmac.compare_digest` → `authenticate_client()` | 4 |
| `rlm/server/webhook_dispatch.py` | idem | 4 |
| `rlm/cli/main.py` | Adicionar comandos `client` + backup `.env` | 1, 4 |

### O que NÃO muda

- Nenhum canal (Discord, WhatsApp, Slack, Telegram, WebChat) precisa ser modificado
- O schema da tabela `sessions` permanece igual
- A lógica do agente (`rlm/core/rlm.py`) não é afetada
- Todos os 847 testes continuam válidos

### Novas dependências

| Dependência | Etapa | Tamanho | Alternativa |
|---|---|---|---|
| `pydantic>=2.0` | 2 | ~3MB | `dataclasses` + validação manual |
| `pyjwt` | 5 | ~50KB | `python-jose` (mais completo) |
| Nenhuma para TOML | 3 | — | Built-in `tomllib` Python 3.11+ |

---

## Referências

- Código atual de autenticação: `rlm/server/ws_server.py:151–210`
- Código atual de sessões: `rlm/core/session.py:97–123`
- Variáveis de ambiente consumidas: `rlm/server/api.py:82–183`
- Mecanismo de referência (openclaw): `openclaw-main/src/config/io.ts`, `src/config/zod-schema.ts`
- Backup rotation (openclaw): `openclaw-main/src/config/backup-rotation.ts`
- Doctor openclaw: `openclaw-main/src/commands/doctor.ts`
