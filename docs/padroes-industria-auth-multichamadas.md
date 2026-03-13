# Padrões da Indústria: Multi-Chamadas e Credenciais — Diagnóstico RLM

> **Data:** Março 2026  
> **Pergunta central:** O que a indústria usa para resolver multi-chamadas e credenciais,  
> e o RLM vai precisar de uma saída autoral?

---

## Índice

1. [O que o RLM já tem — inventário honesto](#1-o-que-o-rlm-já-tem--inventário-honesto)
2. [Padrão 1 — Autenticação: tokens, JWT, OAuth](#2-padrão-1--autenticação-tokens-jwt-oauth)
3. [Padrão 2 — Multi-chamadas: rate limiting e throttling](#3-padrão-2--multi-chamadas-rate-limiting-e-throttling)
4. [Padrão 3 — Identidade de dispositivo: mTLS e device tokens](#4-padrão-3--identidade-de-dispositivo-mtls-e-device-tokens)
5. [Padrão 4 — Multi-agente: orquestração e filas](#5-padrão-4--multi-agente-orquestração-e-filas)
6. [Padrão 5 — Credenciais LLM: API key management](#6-padrão-5--credenciais-llm-api-key-management)
7. [Como empresas reais resolvem este problema](#7-como-empresas-reais-resolvem-este-problema)
8. [Diagnóstico: RLM precisa de saída autoral?](#8-diagnóstico-rlm-precisa-de-saída-autoral)
9. [O que copiar, o que adaptar, o que inventar](#9-o-que-copiar-o-que-adaptar-o-que-inventar)
10. [Plano concreto de implementação](#10-plano-concreto-de-implementação)

---

## 1. O que o RLM Já Tem — Inventário Honesto

Antes de buscar o que falta, é preciso reconhecer o que já existe.

### Autenticação — melhor do que parece

```python
# rlm/server/webhook_dispatch.py
def _validate_token(received: str, expected: str) -> bool:
    return hmac.compare_digest(
        received.encode("utf-8"),
        expected.encode("utf-8")
    )
```

`hmac.compare_digest` é **timing-safe** — resiste a timing attacks onde um atacante
mede diferenças de microsegundos para adivinhar o token caractere por caractere.
Muitos sistemas profissionais esquecem disso e usam `==`.

```python
# rlm/server/webhook_dispatch.py — extração de token em 3 locais
def _extract_token(request, path_token=None):
    # 1. Header X-Hook-Token (preferido — não aparece em logs)
    if t := request.headers.get("X-Hook-Token"):
        return t
    # 2. Authorization: Bearer (padrão OAuth)
    if auth := request.headers.get("Authorization", ""):
        if auth.startswith("Bearer "):
            return auth[7:]
    # 3. Path URL (deprecated — vaza em access logs)
    if path_token:
        logger.warning("Token in URL path is deprecated...")
        return path_token
```

Suporte a 3 métodos de entrega de token com hierarquia correta é sofisticado.

```python
# rlm/server/ws_server.py — WebSocket auth
async def _process_request(path, headers):
    # Query param: ?token=... ou Header: Authorization: Bearer ...
    token = _get_token_from_request(path, headers)
    if ws_token and not hmac.compare_digest(token or "", ws_token):
        return http.HTTPStatus.UNAUTHORIZED, [], b"Unauthorized\n"
```

Auth **antes** do handshake WebSocket — rejeição no nível HTTP, sem desperdiçar
uma conexão WebSocket para depois rejeitar.

### Rate limiting — sliding window implementada

```python
# rlm/server/webhook_dispatch.py
class _RateLimiter:
    """Sliding window rate limiter por IP. Thread-safe via GIL."""
    def __init__(self, max_requests=60, window_seconds=60):
        self._windows: dict[str, deque] = defaultdict(deque)

    def is_allowed(self, client_ip: str) -> bool:
        now = time.monotonic()
        window = self._windows[client_ip]
        # Remove timestamps fora da janela
        while window and window[0] < now - self._window:
            window.popleft()
        if len(window) >= self._max:
            return False
        window.append(now)
        return True
```

Sliding window por IP é o algoritmo correto (melhor que fixed window que tem
burst no reset). Falta: sliding window por `client_id` (autenticado), não só por IP.

### Segurança de input — módulo dedicado sofisticado

```python
# rlm/core/security.py — 21 padrões de detecção
_INJECTION_PATTERNS = [
    ("high",   r"ignore (all )?previous instructions"),
    ("high",   r"you are now (?:DAN|jailbreak|evil)"),
    ("high",   r"reveal (?:your |the )?(?:system prompt|instructions)"),
    ("medium", r"execute.*\bos\.system\b"),
    ("medium", r"import subprocess"),
    ...
]

class EnvVarShield:
    """Redacta variáveis de ambiente contendo KEY/TOKEN/SECRET/PASSWORD
    para que nunca apareçam em logs ou outputs do agente."""
```

`EnvVarShield` resolve um problema real e frequentemente ignorado: o agente
não deve "ver" credenciais do sistema mesmo que acesse `os.environ`.

### Proteção de execução de código

```python
# rlm/core/security.py — REPLAuditor
class REPLAuditor:
    def audit_code(self, code: str):
        tree = ast.parse(code)    # AST — não regex, não contornável com strings
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in BLOCKED_IMPORTS:
                        raise SecurityViolation(f"Blocked: {alias.name}")
            if isinstance(node, ast.Call):
                # bloqueia os.system(), os.popen(), shutil.rmtree()...
```

AST-based (não regex-based) — a diferença é crítica. Um atacante pode contornar
`if "os.system" in code` com `getattr(os, 'sys' + 'tem')("cmd")`. AST walk pega
a estrutura, não o texto.

### O que está faltando (diagnóstico objetivo)

| Componente | Status |
|---|---|
| Token único global por canal | ❌ Um `RLM_WS_TOKEN` para todos os clientes |
| Identidade por dispositivo | ❌ `client_id` é derivado do canal, não do dispositivo |
| Rate limiting por cliente autenticado | ❌ Só por IP |
| Expiração de token | ❌ Tokens não expiram |
| Rotação de credenciais sem restart | ❌ Restart necessário para trocar token |
| Audit log de autenticação | ❌ Nenhum log de quem autenticou quando |
| Revogação imediata de acesso | ❌ Não há como revogar um token sem restart |

---

## 2. Padrão 1 — Autenticação: Tokens, JWT, OAuth

### O espectro da indústria

A autenticação para APIs e dispositivos tem 4 níveis de sofisticação, cada um
adicionando capacidade ao custo de complexidade:

```
Nível 1 — Static Secret (o que o RLM tem hoje)
  Um segredo compartilhado. Funciona para sistemas simples.
  Problema: não expira, não identifica quem é, difícil revogar.

Nível 2 — API Key por cliente (o que o RLM precisa a seguir)
  Um segredo por cliente. Revogação individual possível.
  Usado por: OpenAI, Stripe, Twilio, GitHub.
  Problema: ainda não expira, requer banco para lookup.

Nível 3 — JWT (JSON Web Token)
  Token assinado que carrega claims (quem é, o que pode, quando expira).
  Não requer consulta a banco para validar — a assinatura criptográfica basta.
  Usado por: Auth0, Firebase, a maioria dos sistemas modernos.
  Problema: revogação antes da expiração requer blocklist.

Nível 4 — OAuth 2.0 / OIDC
  Delegação de autorização. Um usuário autoriza um app a agir em seu nome.
  Usado por: login social, integrações entre serviços (Slack, Discord API).
  Problema: complexidade real — servidor de autorização, refresh tokens, scopes.
```

### O que o RLM precisa versus o que existe

O RLM **não é um serviço público** com usuários desconhecidos. É um sistema pessoal/
doméstico com um conjunto fixo e pequeno de dispositivos conhecidos. Isso muda
completamente o que faz sentido:

```
OAuth 2.0 completo  → sobreengenharia total para este caso de uso
API Key por dispositivo + JWT →  CORRETO para este caso
mTLS                → relevante para ESP32 (hardware sem teclado)
```

### JWT aplicado ao RLM — sem biblioteca extra pesada

```python
# O JWT é apenas: base64(header).base64(payload).assinatura_HMAC_SHA256
import base64, hmac, hashlib, json, time, os

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def issue_token(client_id: str, profile: str, permissions: list, ttl_hours: int = 24) -> str:
    secret = os.environ["RLM_JWT_SECRET"].encode()
    header  = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "sub":  client_id,
        "prf":  profile,
        "prm":  permissions,
        "iat":  int(time.time()),
        "exp":  int(time.time()) + ttl_hours * 3600,
    }).encode())
    sig = _b64(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"

def verify_token(token: str) -> dict | None:
    secret = os.environ["RLM_JWT_SECRET"].encode()
    try:
        header, payload, sig = token.split(".")
    except ValueError:
        return None
    expected = _b64(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None           # assinatura inválida
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    if claims.get("exp", 0) < time.time():
        return None           # expirado
    return claims
```

**Zero dependências novas.** `hmac`, `hashlib`, `base64`, `json`, `time` são stdlib.
A biblioteca `pyjwt` faz o mesmo com 3 linhas — mas não é necessária.

---

## 3. Padrão 2 — Multi-Chamadas: Rate Limiting e Throttling

### O que a indústria usa

```
Token Bucket         — cada cliente tem um "balde" de tokens que recarrega a
                       uma taxa fixa. Permite burst controlado.
                       Usado por: AWS API Gateway, Cloudflare, Nginx (burst).

Sliding Window Log   — registra timestamp de cada chamada, conta as últimas N.
                       Mais justo que fixed window. Custo: memória por cliente.
                       Usado por: Redis (ZADD + ZREMRANGEBYSCORE), o RLM JÁ FAZ ISSO.

Leaky Bucket         — fila com saída a taxa constante. Elimina burst completamente.
                       Usado por: sistemas de telco, conformidade com SLAs.

Adaptive Rate Limit  — ajusta limite baseado em carga do servidor.
                       Usado por: OpenAI (429 com Retry-After dinâmico).
```

### O RLM tem sliding window por IP — o gap é por cliente autenticado

```python
# HOJE: rate limit por IP
def is_allowed(self, client_ip: str) -> bool:
    window = self._windows[client_ip]
    ...

# NECESSÁRIO: rate limit por client_id (após autenticação)
def is_allowed(self, key: str) -> bool:  # key = IP pre-auth, client_id post-auth
    window = self._windows[key]
    ...
```

**Problema concreto sem essa mudança:**
- ESP32 e escritório no mesmo NAT/IP doméstico → compartilham o mesmo bucket de rate limit
- ESP32 enviando leituras a cada 30s pode esgotar o limite do escritório
- IP dinâmico (celular em 4G diferente cada hora) → rate limit não identifica o cliente

### O que implementar

```python
# rlm/server/webhook_dispatch.py — extensão do _RateLimiter existente
class _RateLimiter:
    def __init__(self, max_requests=60, window_seconds=60,
                 per_client_max=30):      # ← clientes autenticados têm limite próprio
        self._ip_windows: dict[str, deque] = defaultdict(deque)
        self._client_windows: dict[str, deque] = defaultdict(deque)
        self._per_client_max = per_client_max

    def is_allowed_ip(self, ip: str) -> bool:
        """Pré-autenticação: limita por IP."""
        return self._check(self._ip_windows, ip, self._max)

    def is_allowed_client(self, client_id: str) -> bool:
        """Pós-autenticação: limita por cliente identificado."""
        return self._check(self._client_windows, client_id, self._per_client_max)

    def _check(self, store, key, limit):
        now = time.monotonic()
        w = store[key]
        while w and w[0] < now - self._window:
            w.popleft()
        if len(w) >= limit:
            return False
        w.append(now)
        return True
```

---

## 4. Padrão 3 — Identidade de Dispositivo: mTLS e Device Tokens

### O problema específico do ESP32

ESP32 é hardware embarcado. Não tem teclado para digitar token. Não tem sistema de
arquivos seguro para armazenar segredo longo. O token precisa ser:
- Embutido no firmware no momento do flash
- Diferente por dispositivo (não um segredo global)
- Revogável remotamente sem reflash

### O que a indústria usa para IoT

```
mTLS (Mutual TLS)    — cada dispositivo tem um certificado X.509 emitido pela
                       sua CA própria. A conexão TLS valida ambos os lados.
                       Usado por: AWS IoT Core, Azure IoT Hub, Google Cloud IoT.
                       Problema: complexidade operacional alta. CA, certificados,
                       rotação. Justificado para frotas de 1000+ devices.

Device Tokens        — um segredo por dispositivo, armazenado no flash do MCU.
                       Bootstrap: o device recebe o token uma vez na configuração.
                       Revogação: marca o token como inativo no servidor.
                       Usado por: Particle IoT, ESP32 projetos hobbyist, SmartThings.
                       CORRETO para o caso do RLM.

Pre-shared Keys      — variante de mTLS sem infraestrutura de CA completa.
                       Negociação TLS com segredo pré-combinado (ESP32 suporta via
                       mbedTLS que está embutido no ESP-IDF).
```

### Para o RLM — Device Token é o correto

```
Fluxo de bootstrap de um ESP32 novo:

1. Administrador roda no PC:
   $ rlm client add esp32-sala --profile iot --description "DHT22 + relay"
   Token gerado: rlm_iot_7f3a9b2e4c1d...   ← 32 bytes hex

2. Administrador grava o token no firmware:
   // esp32_config.h
   #define RLM_TOKEN "rlm_iot_7f3a9b2e4c1d..."
   #define RLM_SERVER "https://meu-rlm.local"
   #define RLM_CLIENT_ID "esp32-sala"

3. ESP32 faz requisição:
   POST /webhook/esp32-sala
   X-Hook-Token: rlm_iot_7f3a9b2e4c1d...
   Content-Type: application/json
   {"text": "temp=23.4,umidade=65"}

4. RLM valida contra tabela clients:
   SELECT id, profile FROM clients WHERE token_hash = SHA256(token) AND active = 1
   → client_id="esp32-sala", profile="iot"
   → resposta formatada como JSON, não markdown
```

**Para revogar** (ESP32 comprometido ou descartado):
```sql
UPDATE clients SET active = 0 WHERE id = 'esp32-sala';
-- Imediato, sem restart do servidor, sem reflash do device
```

---

## 5. Padrão 4 — Multi-Agente: Orquestração e Filas

### O que a indústria usa para múltiplos agentes simultâneos

```
Message Queue        — pedidos entram numa fila. Workers consomem.
                       Back-pressure natural: fila cheia → cliente recebe 503.
                       Usado por: sistemas de alta escala (Celery + Redis/RabbitMQ).
                       SOBREENGENHARIA para uso pessoal.

Worker Pool          — N workers fixos. Pedido espera worker disponível.
                       É exatamente o que Python's ThreadPoolExecutor faz.
                       O RLM JÁ USA ISSO via run_in_executor.

Actor Model          — cada agente é um ator com mailbox. Sem estado compartilhado.
                       Usado por: Erlang/Elixir, Akka (JVM).
                       Analogia: o RLM JÁ SEGUE este padrão (RLMSession por client_id).

Circuit Breaker      — se serviço externo (LLM API) começa a falhar, para de
                       tentar por N segundos. Evita cascade failure.
                       Usado por: sistemas distribuídos (Netflix Hystrix, Resilience4j).
                       O RLM tem detecção de error_loop no Supervisor — é um circuit
                       breaker rudimentar.
```

### O padrão de orquestração de LLMs em 2025-2026

O problema específico de orquestrar múltiplas chamadas a LLMs gerou uma classe
de ferramentas:

| Ferramenta | Abordagem | Relevância para RLM |
|---|---|---|
| **LangGraph** | Grafo de estados para agentes | Similar ao que o RLM faz internamente em `rlm.py` |
| **CrewAI** | Múltiplos agentes com roles | Suporte a sub-agentes: `rlm/core/sub_rlm.py` já existe |
| **AutoGen (Microsoft)** | Agentes conversam entre si | Conceito similar ao `sub_rlm` |
| **OpenAI Assistants API** | Thread + Run abstractions | SessionManager do RLM é análogo |
| **LiteLLM** | Proxy unificado para múltiplos provedores LLM | `rlm/core/lm_handler.py` é análogo |

**Observação crítica:** o RLM já reimplementou os conceitos centrais de todas
essas ferramentas de forma própria. Isso não é um problema — é uma escolha
arquitetural deliberada que garante controle total.

### O problema de multi-chamada que o RLM pode ter

```
Cenário: 3 dispositivos enviam pedidos ao mesmo tempo.
         O 3º chega quando os outros 2 ainda estão executando.

Hoje:    O 3º é imediatamente despachado para uma thread livre (se houver).
         Sem feedback ao cliente sobre posição na fila.

Problema com sessão única por cliente:
  Celular envia "qual o tempo?" → RLM_celular inicia (status=running)
  Celular imediatamente manda "e amanhã?" → ERRO: "Session already running"
  → Usuário precisa esperar e reenviar manualmente

Solução da indústria: request queue por sessão
  Pedido 1: "qual o tempo?"  → executa imediatamente
  Pedido 2: "e amanhã?"      → enfileirado (HTTP 202 Accepted)
  Pedido 2 executa quando pedido 1 termina → callback/webhook de notificação
```

---

## 6. Padrão 5 — Credenciais LLM: API Key Management

### O problema

O `OPENAI_API_KEY` no `.env` é um segredo de altíssimo valor. Se vazar:
- Custos podem ser gerados por terceiros na sua conta
- Acesso a todo histórico de conversas (via OpenAI Playground)
- Sem rastreabilidade de qual sistema usou

### O que a indústria usa

```
Secret Manager      — cofre criptografado para segredos.
                       AWS Secrets Manager, HashiCorp Vault, Azure Key Vault.
                       Segredo nunca toca o filesystem. App solicita via API.
                       Rotação automática possível.
                       OVERHEAD: requer infraestrutura adicional.

Environment injection — CI/CD injeta secrets como env vars em runtime.
                        GitHub Actions Secrets, Railway Secrets, Fly.io secrets.
                        O segredo nunca está no código nem no filesystem em prod.
                        CORRETO para o caso do RLM em deploy.

Proxy de API         — um serviço intermediário que tem a chave. Seus apps chamam
                        o proxy sem ter acesso à chave original.
                        LiteLLM Proxy, one-api, portkey.ai.
                        O RLM já tem portkey-ai no pyproject.toml.

Key per environment  — chave diferente para dev, staging, prod.
                        Rate limits separados. Comprometimento isolado.
                        MÍNIMO ACEITÁVEL.
```

### O que o RLM precisa hoje

```python
# Mínimo: validar na inicialização, não em runtime
# rlm/server/api.py — no lifespan startup
api_key = os.environ.get("OPENAI_API_KEY", "")
if not api_key.startswith("sk-"):
    raise RuntimeError(
        "OPENAI_API_KEY inválida ou ausente. "
        "Verifique o .env antes de iniciar o servidor."
    )
# (falha na inicialização, não durante uma sessão de usuário)
```

```bash
# Para deploy: NUNCA no filesystem — sempre injeção por ambiente
# Fly.io:
fly secrets set OPENAI_API_KEY=sk-proj-...
# Railway:
railway variables set OPENAI_API_KEY=sk-proj-...
# Docker:
docker run -e OPENAI_API_KEY=sk-proj-... rlm-main
```

---

## 7. Como Empresas Reais Resolvem Este Problema

### Para sistemas similares (daemon pessoal + IoT + mobile)

**Home Assistant** (100M+ instâncias, open source, Python/FastAPI-like)

```
Modelo de auth:
  - Um token por "integration" (dispositivo, automação, usuário)
  - JWT de longa duração (anos) por padrão, revogável na UI
  - Long-lived access tokens + OAuth 2.0 para OAuth providers
  - Webhook tokens independentes dos tokens de sessão
  - Todos armazenados em SQLite com hash

Multi-device:
  - "Areas" (sala, quarto, jardim) → mapeiam para client_id do tipo location
  - "Devices" dentro de areas → sub-identidades
  - State machine rigorosa: entity_id.state (ligado/desligado/etc.)
```

O Home Assistant resolveu exatamente o problema do RLM (IoT + apps + automações)
com uma arquitetura que emergiu dos mesmos constraint: Python, SQLite, FastAPI-like,
single-process by default.

**Telegram Bot API** (o RLM já se integra a ele)

```
Modelo de auth: 1 token por bot. O bot autentica com a Telegram API.
Múltiplos usuários: user_id vem no update, É o "client_id" do RLM.
Rate limiting: 30 mensagens/segundo por bot, 1 mensagem/segundo por chat.
Filas: o RLM recebe updates sequencialmente via long polling ou webhook.
```

O Telegram já resolve o multi-dispositivo do lado deles: um usuário pode mandar
mensagem do celular e do PC ao mesmo tempo — ambas chegam com o mesmo `user_id`.

**Notion AI / Linear AI / Cursor** (agentes embarcados em produto)

```
Padrão comum:
  - Session token de curta duração (15 min a 2h)
  - Refresh token de longa duração
  - Cada "workspace" ou "document" tem sua identidade
  - Execuções longas: imediato 202 Accepted + SSE para progresso
  - Cancelamento: DELETE /runs/{id}
  - Resultado: GET /runs/{id} ou webhook de callback
```

---

## 8. Diagnóstico: RLM Precisa de Saída Autoral?

### Resposta direta: em grande parte, não

Para os problemas de autenticação e multi-chamadas, os padrões da indústria
são bem estabelecidos e adequados. O RLM não precisa inventar nada novo para
resolver esses problemas.

**O que é padrão bem resolvido:**

| Problema | Solução padrão | Custo de implementação |
|---|---|---|
| Token por dispositivo | API Key + SQLite | Baixo |
| Token que expira | JWT com `exp` claim | Baixo (stdlib) |
| Revogação imediata | `active=0` no banco | Baixo |
| Rate limit por cliente | Sliding window por `client_id` | Baixíssimo (extensão do que existe) |
| Credencial do LLM segura | Env injection no deploy | Zero (é prática operacional) |
| IoT sem teclado | Device token pré-gravado | Baixo |

**O que pode precisar de adaptação:**

| Problema | Por que não é off-the-shelf |
|---|---|
| Perfil de comportamento por dispositivo | Específico da arquitetura RLM (perfis que afetam o prompt do agente, não só permissões de API) |
| Contexto por dispositivo no prompt system | Sem equivalente direto — como injetar "você está falando com um ESP32, responda em JSON" automaticamente |
| Memória cross-device por user_id | Requer entender a semântica de "memória" no contexto do RLM especificamente |

**O que é genuinamente específico do RLM:**

A parte que não tem precedente exato na indústria não é autenticação nem
multi-chamadas — é **a camada de significado** entre a identidade do dispositivo
e o comportamento do agente.

```
Sistemas padrão:       autenticação → autorização → recurso
                       "quem é"    → "o que pode" → "o que acessa"

RLM:                   autenticação → perfil → comportamento do LLM
                       "quem é"    → "como age" → "como responde"
```

A diferença: no sistema padrão, permissão é binária (pode/não pode). No RLM,
a identidade do cliente modifica o **prompt do agente, o modelo escolhido, o
estilo de resposta, o contexto injetado**. Isso não tem biblioteca pronta porque
é específico de sistemas de agentes LLM.

---

## 9. O que Copiar, o que Adaptar, o que Inventar

### Copiar diretamente (padrões maduros, zero inovação necessária)

```
✓ JWT com stdlib Python (sem pyjwt se quiser zero deps)
✓ API Key por dispositivo + SHA-256 hash no banco
✓ Rate limiting por client_id (extensão do que já existe)
✓ Revogação via flag active=0 no SQLite
✓ Audit log de autenticação em tabela separada
✓ Secrets via env injection (ops, não código)
✓ Device token para ESP32 (pré-gravado no firmware)
```

### Adaptar (padrão existe, mas precisa ajuste para contexto RLM)

```
~ mTLS para ESP32: padrão de IoT enterprise, mas a complexidade de CA
  pode ser reduzida com self-signed cert por device (sem CA real).
  Alternativa mais simples: HTTPS com token (suficiente para uso doméstico).

~ Request queue por sessão: o padrão (202 Accepted + poll/webhook) existe,
  mas a semântica de "fila por sessão RLM" tem nuances do SessionManager.

~ SSE streaming para todos os canais: SSE existe (já implementado no WebChat),
  mas integrar com o loop síncrono do RLM core requer bridge específica.
```

### Inventar (sem equivalente direto)

```
★ Context injection por perfil de dispositivo:
  O mecanismo de ler o perfil do cliente e injetar no system prompt do agente
  de forma transparente não tem precedente em frameworks de agentes.
  Home Assistant não faz isso. LangGraph não faz. Precisa ser construído.

★ Memory scoping cross-device:
  "Escritório e celular do mesmo usuário compartilham memória de projeto,
   mas não contexto de conversa." A granularidade certa é específica do RLM.

★ Behavior profiles (não permission profiles):
  A diferença entre "pode executar código" (permissão binária) e
  "responde em JSON de no máximo 200 tokens sem markdown" (comportamento)
  é o que torna o RLM diferente de um API gateway comum.
  Isso precisa de uma linguagem de descrição própria.
```

---

## 10. Plano Concreto de Implementação

### Fase 1 — Segurança básica (1 semana)

**Objetivo:** fechar as lacunas de autenticação sem reescrever nada.

```python
# 1a. Tabela clients no SQLite (adicionar a session.py)
CREATE TABLE IF NOT EXISTS clients (
    id          TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL,     -- SHA-256 do token raw
    profile     TEXT DEFAULT 'default',
    active      INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL,
    last_seen   TEXT
);

# 1b. Modificar _validate_token em webhook_dispatch.py e ws_server.py
# De: hmac.compare_digest(token, GLOBAL_TOKEN)
# Para: lookup no banco + hmac.compare_digest(SHA256(token), stored_hash)

# 1c. Adicionar ao cmd_doctor:
#   - verifica se .env.bak existe (criado antes de qualquer escrita)
#   - verifica se token tem > 32 chars
#   - conta e lista clientes ativos
```

### Fase 2 — Identidade por dispositivo (2 semanas)

**Objetivo:** ESP32, celular e escritório têm identidades separadas.

```bash
# Novos comandos CLI
rlm client add <id> --profile <perfil> [--description "..."]
rlm client list
rlm client revoke <id>
rlm client token <id>    # reemite token (invalida anterior)
```

### Fase 3 — Perfis de comportamento (2-3 semanas)

**Objetivo:** ESP32 recebe JSON, carro recebe 1 frase, escritório recebe markdown.

```python
# Context injection no ponto de execução (api.py ou supervisor.py)
profile = get_client_profile(client_id)    # lê da tabela clients
system_prompt = build_system_prompt(
    base_prompt=RLM_SYSTEM_PROMPT,
    context_hint=profile.context_hint,    # "você está respondendo a um ESP32..."
    response_style=profile.response_style, # "structured_json" | "concise" | "detailed"
    max_tokens=profile.max_response_tokens,
)
result = supervisor.execute(session, prompt, system_prompt_override=system_prompt)
```

### Fase 4 — Memória cross-device (quando necessário)

**Ativado por demanda.** Quando o usuário quiser que escritório e celular
compartilhem contexto de projetos, ativar `memory_manager.py` com
`user_id` como chave de agrupamento.

---

## Resumo Executivo

| Questão | Resposta |
|---|---|
| A indústria tem padrão para multi-chamadas? | Sim — ThreadPool + run_in_executor (RLM já usa) |
| A indústria tem padrão para credenciais multi-device? | Sim — API Key por device + JWT |
| O RLM precisa reimplementar tudo do zero? | Não — 80% é padrão bem estabelecido |
| Existe algo genuinamente autoral necessário? | Sim — a camada de "behavior profiles" que conecta identidade do device ao comportamento do agente LLM |
| O que a bancada atual do RLM já resolveu bem? | timing-safe auth, rate limiting, AST-based security, EnvVarShield |
| Qual o gap mais urgente? | Token único global → token por device |
| O RLM vai precisar de biblioteca nova? | Não — JWT com stdlib, SQLite já existe, sliding window já existe |
