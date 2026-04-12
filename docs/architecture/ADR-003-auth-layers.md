# ADR-003: Camadas de Autenticação

**Status**: Aceito  
**Data**: 2025-07-11  
**Contexto**: Existem três módulos de "auth" no RLM com responsabilidades distintas.

## Decisão

A autenticação é estruturada em três camadas, cada uma com escopo definido:

| Camada | Módulo | Responsabilidade |
|---|---|---|
| 1. Identidade do dispositivo/cliente | `rlm/core/auth.py` | Registro, autenticação e revogação de clientes. SQLite-backed. SHA-256 hashed tokens. Produz `ClientIdentity`. |
| 2. Tokens JWT | `rlm/core/security/auth.py` | Emissão e verificação de JWT (HMAC-SHA256). Zero dependências externas. Expiração obrigatória. Config via `RLM_JWT_SECRET`. |
| 3. Middleware HTTP | `rlm/gateway/auth_helpers.py` | Extração de token de request FastAPI, matching, `require_token()`. Depende de FastAPI. |

## Fluxo

```
Request HTTP chega
    ↓
auth_helpers.extract_request_token() → token string
    ↓
security/auth.verify_token() → claims dict (JWT) 
    OU
core/auth.authenticate_client() → ClientIdentity (token direto)
    ↓
RLM processa com identidade verificada
```

## Regras

1. **`core/auth.py` não pode importar FastAPI** — é Layer 3 (core), abaixo do gateway.
2. **`security/auth.py` é stdlib-only** — nenhuma dependência externa permitida.
3. **`gateway/auth_helpers.py` é a única camada que conhece HTTP** — Request, Header, HTTPException.
4. **Novos mecanismos de autenticação** (OAuth, API keys) devem escolher a camada correta.

## Consequências

- Três arquivos permanecem. Não fundir.
- `core/auth.py` pode migrar para `core/security/identity.py` num futuro refactor (agrupar security).
- Consumer de auth deve importar da camada mais alta que precisa (auth_helpers para endpoints, security/auth para JWT puro, core/auth para identity layer).
