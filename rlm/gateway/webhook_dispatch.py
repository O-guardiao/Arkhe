"""
RLM Webhook Dispatch — Fase 9.2

Receptor HTTP externo para acionar o agente RLM de sistemas de terceiros.

Análogo ao receptor de hooks do OpenClaw Gateway (hooks.ts), mas adaptado para:
- FastAPI (não Node.js)
- RLM SessionManager + Supervisor como backend
- Autenticação via token em path ou header (sem configuração complexa)

Endpoints gerados por `create_webhook_router()`:
    POST /api/hooks/{token}           — disparo simples (texto livre)
    POST /api/hooks/{token}/{client_id}  — disparo para sessão específica

Formato do body (todos opcionais):
    {
        "text":       "faça um relatório de vendas",   ← texto para o agente
        "client_id":  "pipeline_vendas",               ← sessão alvo (override)
        "session_id": "sess_abc123",                   ← ID de sessão existente
        "channel":    "n8n",                           ← canal de origem (meta)
        "metadata":   {}                               ← payload bruto (passado ao evento)
    }

Autenticação:
    Token configurado em RLM_HOOK_TOKEN (env var).
    Enviado no path: POST /api/hooks/{meu_token}
    Ou no header:    X-Hook-Token: meu_token
    Ou bearer:       Authorization: Bearer meu_token

Rate limiting:
    In-memory, por IP. Padrão: 60 req/min.
    Bypass via RLM_HOOK_RATE_LIMIT=0.

Exemplo curl:
    curl -X POST http://localhost:5000/api/hooks/meu_token \\
         -H "Content-Type: application/json" \\
         -d '{"text": "gere relatório de hoje e envie por email"}'
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from rlm.core.structured_log import get_logger

hook_log = get_logger("webhook_dispatch")

DEFAULT_HOOK_CLIENT_ID = "hook_default"
DEFAULT_RATE_LIMIT_RPM = 60       # requisições por minuto por IP
DEFAULT_MAX_BODY_BYTES = 256_000  # 256 KB


# ---------------------------------------------------------------------------
# Rate Limiter simples (sliding window)
# ---------------------------------------------------------------------------

class _RateLimiter:
    """
    Sliding window rate limiter in-memory, por IP e/ou client_id.
    Thread-safe o suficiente para o uso em FastAPI (GIL protege deque).

    Phase 9.4 (CiberSeg): agora suporta rate limiting dual —
    por IP (anti-DoS) E por client_id (anti-abuse autenticado).
    """

    def __init__(self, rpm: int = DEFAULT_RATE_LIMIT_RPM):
        self._rpm = rpm
        self._windows: dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """
        Retorna (allowed, retry_after_seconds).
        ``key`` pode ser IP, client_id, ou combinação.
        retry_after_seconds > 0 apenas quando bloqueado.
        """
        if self._rpm <= 0:
            return True, 0

        now = time.monotonic()
        window_start = now - 60.0
        q = self._windows[key]

        # Remover entries fora da janela
        while q and q[0] < window_start:
            q.popleft()

        if len(q) >= self._rpm:
            oldest = q[0]
            retry_after = int(60.0 - (now - oldest)) + 1
            return False, retry_after

        q.append(now)
        return True, 0

    def check_dual(
        self,
        client_ip: str,
        client_id: str | None = None,
    ) -> tuple[bool, int]:
        """Check both IP and client_id limits. Returns the stricter result."""
        allowed_ip, retry_ip = self.is_allowed(f"ip:{client_ip}")
        if not allowed_ip:
            return False, retry_ip

        if client_id:
            allowed_cid, retry_cid = self.is_allowed(f"cid:{client_id}")
            if not allowed_cid:
                return False, retry_cid

        return True, 0


# ---------------------------------------------------------------------------
# Body Schema
# ---------------------------------------------------------------------------

class HookDispatchBody(BaseModel):
    """Body de uma requisição de webhook."""
    text: str | None = None         # mensagem livre para o agente
    client_id: str | None = None    # override da sessão alvo
    session_id: str | None = None   # ID de sessão existente (futuro uso)
    channel: str = "webhook"        # identificador do canal de origem
    metadata: dict[str, Any] = {}   # payload extra passado como contexto


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------

def create_webhook_router(
    expected_token: str,
    default_client_id: str = DEFAULT_HOOK_CLIENT_ID,
    rate_limit_rpm: int = DEFAULT_RATE_LIMIT_RPM,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> APIRouter:
    """
    Cria e retorna um APIRouter com os endpoints de webhook.

    Args:
        expected_token:   Token que autentica o caller. Deve ser gerado com
                          `secrets.token_hex(32)` e armazenado em RLM_HOOK_TOKEN.
        default_client_id: client_id padrão quando o body não especifica um.
        rate_limit_rpm:   Máximo de requisições por minuto por IP (0 = ilimitado).
        max_body_bytes:   Tamanho máximo do body em bytes.

    Usage em api.py:
        from rlm.gateway.webhook_dispatch import create_webhook_router
        token = os.environ.get("RLM_HOOK_TOKEN", "")
        if token:
            app.include_router(create_webhook_router(token), prefix="")
    """
    router = APIRouter(tags=["External Webhooks"])
    limiter = _RateLimiter(rpm=rate_limit_rpm)

    def _resolve_client_ip(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _extract_token(request: Request, path_token: str) -> str:
        """Extrai o token do path, header X-Hook-Token ou Authorization Bearer.

        Phase 9.3: path_token é suportado para retro-compatibilidade, mas
        deprecado: o token no path aparece em logs de nginx/acesso.
        Preferível: X-Hook-Token ou Authorization: Bearer.
        """
        hook_header = request.headers.get("X-Hook-Token", "").strip()
        if hook_header:
            return hook_header
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if path_token:
            # Deprecated: token in URL path leaks into access logs
            hook_log.warn(
                "[webhook] Token recebido no path URL (deprecado). "
                "Use X-Hook-Token ou Authorization: Bearer para evitar vazar token em logs."
            )
            return path_token.strip()
        return ""

    def _validate_token(received: str) -> bool:
        if not expected_token:
            return False
        # comparação em tempo constante (sem timing oracle)
        import hmac
        return hmac.compare_digest(
            expected_token.encode(),
            received.encode() if received else b"",
        )

    async def _dispatch(
        request: Request,
        path_token: str,
        path_client_id: str | None,
    ) -> JSONResponse:
        # --- Rate limit ---
        client_ip = _resolve_client_ip(request)
        # Phase 9.4: dual rate limiting (IP + client_id when available)
        _body_cid = None  # client_id not yet parsed; IP check first
        allowed, retry_after = limiter.is_allowed(f"ip:{client_ip}")
        if not allowed:
            hook_log.warn(f"[webhook] Rate limited: ip={client_ip}")
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={"error": "rate_limited", "retry_after": retry_after},
            )

        # --- Auth ---
        token = _extract_token(request, path_token)
        if not _validate_token(token):
            hook_log.warn(f"[webhook] Invalid token from ip={client_ip}")
            raise HTTPException(401, "Invalid or missing hook token")

        # ── Camada 3: try per-device identity ──
        _client_identity = None
        try:
            from rlm.core.auth import authenticate_client
            sm_ref = getattr(request.app.state, "session_manager", None)
            _db = sm_ref.db_path if sm_ref else "rlm_sessions.db"
            _client_identity = authenticate_client(_db, token)
        except Exception:
            pass  # graceful fallback — global token still validates

        # --- Body ---
        content_length = int(request.headers.get("content-length", "0"))
        if content_length > max_body_bytes:
            raise HTTPException(413, "Request body too large")

        try:
            raw = await request.body()
        except Exception as e:
            raise HTTPException(400, f"Failed to read body: {e}")

        body = HookDispatchBody()
        if raw:
            try:
                import json
                data = json.loads(raw)
                body = HookDispatchBody.model_validate(data)
            except Exception as e:
                raise HTTPException(400, f"Invalid JSON body: {e}")

        # --- Resolve client_id ---
        client_id = (
            path_client_id
            or body.client_id
            or (_client_identity.client_id if _client_identity and _client_identity.client_id != "legacy" else "")
            or default_client_id
        )

        # Phase 9.4: per-client_id rate limit (second layer)
        cid_allowed, cid_retry = limiter.is_allowed(f"cid:{client_id}")
        if not cid_allowed:
            hook_log.warn(f"[webhook] Rate limited (client_id): {client_id}")
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(cid_retry)},
                content={"error": "rate_limited", "retry_after": cid_retry},
            )

        # --- Build prompt ---
        text = body.text or ""
        if not text and body.metadata:
            import json
            text = json.dumps(body.metadata, ensure_ascii=False)
        if not text:
            raise HTTPException(422, "Body must contain 'text' or 'metadata'")

        # Enriquecer com metadados do canal
        prompt = text
        if body.channel and body.channel != "webhook":
            prompt = f"[via {body.channel}] {text}"

        hook_log.info(
            f"[webhook] Dispatching: client_id={client_id!r} "
            f"channel={body.channel!r} text={text[:80]!r}"
        )

        # --- Execute via app.state ---
        sm = request.app.state.session_manager
        supervisor = request.app.state.supervisor
        hooks = getattr(request.app.state, "hooks", None)

        session = sm.get_or_create(client_id)

        # ── Camada 3: populate session metadata from identity ──
        if _client_identity and _client_identity.client_id != "legacy":
            _pref = _client_identity.metadata.get("preferred_channel")
            if _pref:
                session.metadata["preferred_channel"] = _pref
            _bc = _client_identity.metadata.get("broadcast_channels", [])
            if _bc:
                session.metadata["broadcast_channels"] = _bc
            if _client_identity.context_hint:
                session.metadata["context_hint"] = _client_identity.context_hint
            session.metadata["client_profile"] = _client_identity.profile

        sm.log_event(session.session_id, "webhook_dispatch", {
            "client_id": client_id,
            "channel": body.channel,
            "ip": client_ip,
        })
        sm.log_operation(
            session.session_id,
            "message.receive",
            phase="dispatch",
            status="accepted",
            source="webhook_dispatch",
            payload={
                "client_id": client_id,
                "channel": body.channel,
                "ip": client_ip,
                "originating_channel": session.originating_channel,
                "delivery_context": session.delivery_context,
            },
        )

        if hooks:
            hooks.trigger(
                "webhook.received",
                client_id=client_id,
                channel=body.channel,
                text=text,
                session_id=session.session_id,
            )

        import asyncio
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: supervisor.execute(session, prompt),
            )
        except Exception as e:
            hook_log.warn(f"[webhook] Execution failed: {e}")
            raise HTTPException(500, f"Agent execution failed: {e}")

        sm.update_session(session)

        # ── MessageBus routing (Phase 3 multichannel) ─────────────────
        # Mesmo padrão de /webhook em api.py: normaliza inbound, registra
        # no bus e roteia resposta via Outbox → DeliveryWorker.
        use_bus = getattr(request.app.state, "use_message_bus", False)
        if use_bus:
            try:
                bus = request.app.state.message_bus
                from rlm.gateway.message_envelope import InboundMessage

                prefix = client_id.split(":", 1)[0] if ":" in client_id else "webhook"
                inbound_msg = InboundMessage(
                    channel=body.channel or prefix,
                    client_id=client_id,
                    text=text,
                    from_user="",
                    content_type="text",
                    channel_meta=body.metadata or {},
                )
                inbound_envelope = bus.ingest(inbound_msg)

                response_text = result.response if hasattr(result, "response") else ""
                was_replied = getattr(session, "__reply_delivered__", False)

                if response_text and not was_replied:
                    bus.route_response(
                        inbound_envelope,
                        response_text,
                        session,
                        session_id=session.session_id,
                    )
            except Exception as exc:
                # Bus failure NUNCA bloqueia o fluxo principal.
                hook_log.error(
                    f"[webhook] MessageBus routing failed (non-fatal): {exc}",
                )

        return JSONResponse({
            "status": result.status,
            "session_id": session.session_id,
            "client_id": client_id,
            "response": result.response,
            "execution_time": round(result.execution_time, 2),
        })

    # --- Routes ---

    @router.post("/api/hooks/{path_token}")
    async def dispatch_hook(path_token: str, request: Request):
        """
        Disparo de hook externo para o client_id padrão.

        O token pode ser enviado no path ou via header X-Hook-Token.
        """
        return await _dispatch(request, path_token=path_token, path_client_id=None)

    @router.post("/api/hooks/{path_token}/{path_client_id}")
    async def dispatch_hook_to_session(
        path_token: str,
        path_client_id: str,
        request: Request,
    ):
        """
        Disparo de hook externo para uma sessão específica.

        Útil quando você quer manter múltiplas sessões de agente
        com contextos diferentes (ex: "cliente_joao", "pipeline_vendas").
        """
        return await _dispatch(
            request, path_token=path_token, path_client_id=path_client_id
        )

    @router.get("/api/hooks/info")
    async def hook_info(request: Request):
        """
        Metadados públicos do receptor de webhooks.
        Confirma que o endpoint está ativo sem expor o token.
        """
        return {
            "status": "active",
            "endpoints": [
                "POST /api/hooks/{token}",
                "POST /api/hooks/{token}/{client_id}",
            ],
            "rate_limit_rpm": rate_limit_rpm,
            "max_body_bytes": max_body_bytes,
        }

    return router
