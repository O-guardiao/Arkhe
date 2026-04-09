"""
RLM Automation Gateway — Fase 7.4 (Reescrita)

FastAPI server integrado com:
- SessionManager: sessões isoladas por client_id
- RLMSupervisor: timeout, abort, error-loop detection
- PluginLoader: injeção dinâmica de ferramentas no REPL
- EventRouter: roteamento de eventos por source pattern

Endpoints:
    POST   /webhook/{client_id}   — Receber e processar evento
    GET    /sessions              — Listar sessões ativas
    GET    /sessions/{id}         — Detalhes de uma sessão
    DELETE /sessions/{id}         — Abortar execução de uma sessão
    GET    /sessions/{id}/events  — Log de eventos da sessão
    GET    /plugins               — Listar plugins disponíveis
    GET    /routes                — Listar rotas configuradas
    GET    /health                — Health check
"""
import json
import asyncio
import os
from importlib import import_module
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
load_dotenv()  # carrega .env do diretório atual ou ~/.rlm/.env

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from rlm.core.session import SessionManager
from rlm.core.orchestration.supervisor import RLMSupervisor, SupervisorConfig, ExecutionResult
from rlm.core.engine.hooks import HookSystem
from rlm.core.security.execution_policy import build_tier_backends
from rlm.core.orchestration.handoff import make_handoff_fn, VALID_HANDOFF_ROLES
from rlm.core.orchestration.role_orchestrator import PENDING_HANDOFFS_KEY, orchestrate_roles
from rlm.core.orchestration.scheduler import RLMScheduler, CronJob
from rlm.core.skillkit.skill_loader import SkillLoader
from rlm.core.security.exec_approval import ExecApprovalGate
from rlm.gateway.webhook_dispatch import create_webhook_router
from rlm.server.openai_compat import create_openai_compat_router
from rlm.server.runtime_pipeline import RuntimeDispatchRejected, RuntimeDispatchServices, dispatch_runtime_prompt_sync
from rlm.gateway.auth_helpers import configured_tokens, require_token
from rlm.server.ws_server import RLMEventBus, start_ws_server
from rlm.server.drain import DrainGuard
from rlm.server.health_monitor import HealthMonitor
from rlm.plugins.channel_registry import ChannelRegistry, sanitize_text_payload
from rlm.plugins import PluginLoader
from rlm.core.comms.message_bus import get_message_bus
from rlm.core.comms.internal_api import resolve_internal_api_base_url
from rlm.core.comms.channel_status import ChannelStatusRegistry
from rlm.gateway.message_envelope import InboundMessage
from rlm.runtime import build_runtime_guard_from_env
from rlm.server.event_router import EventRouter
from rlm.core.structured_log import get_logger
from rlm.core.skillkit.skill_telemetry import get_skill_telemetry

# Channel gateways — agora gerenciados por rlm.gateway.transport_router.
# Os try/except abaixo foram removidos; use `from rlm.gateway.transport_router import ...`

gateway_log = get_logger("api")

_INTERNAL_AUTH_ENV_NAMES = ("RLM_INTERNAL_TOKEN", "RLM_WS_TOKEN", "RLM_API_TOKEN")
_ADMIN_AUTH_ENV_NAMES = ("RLM_ADMIN_TOKEN", "RLM_API_TOKEN", "RLM_WS_TOKEN")


def _require_internal_api_auth(request: Request) -> None:
    require_token(
        request,
        env_names=_INTERNAL_AUTH_ENV_NAMES,
        scope="internal API",
        allow_query=True,
    )


def _require_admin_api_auth(request: Request) -> None:
    require_token(
        request,
        env_names=_ADMIN_AUTH_ENV_NAMES,
        scope="admin API",
    )


def _summarize_inbound_payload(client_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw_text = payload.get("text") or payload.get("message") or ""
    text_preview = sanitize_text_payload(raw_text)
    if len(text_preview) > 500:
        text_preview = text_preview[:460] + "...[truncado]"

    return {
        "client_id": client_id,
        "channel": client_id.partition(":")[0] or "webhook",
        "from_user": sanitize_text_payload(payload.get("from_user") or ""),
        "chat_id": payload.get("chat_id"),
        "payload_size": len(json.dumps(payload, ensure_ascii=False)),
        "text_preview": text_preview,
    }


# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated @app.on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # --- Startup ---
    gateway_log.info("Initializing infrastructure...")
    app.state.event_bus = RLMEventBus()
    app.state.ws_thread = None

    # ── Phase 0: Load structured config (rlm.toml + env overlay) ─────────
    from rlm.core.config import load_config as _load_config
    _cfg = _load_config()
    app.state.config = _cfg
    gateway_log.info(f"Config loaded: {len(_cfg.profiles)} profiles, bus_enabled={_cfg.message_bus.enabled}")

    # Phase 9.4 (CiberSeg): Validate critical env vars before anything else
    if not os.environ.get("RLM_HOOK_TOKEN"):
        gateway_log.warn(
            "RLM_HOOK_TOKEN is not set. External webhook receiver will remain disabled."
        )
    if not configured_tokens(*_INTERNAL_AUTH_ENV_NAMES):
        gateway_log.error(
            "No internal auth token configured. Core /webhook/{client_id} will reject requests. "
            "Set RLM_INTERNAL_TOKEN or reuse RLM_WS_TOKEN."
        )
    if not configured_tokens(*_ADMIN_AUTH_ENV_NAMES):
        gateway_log.error(
            "No admin auth token configured. Administrative endpoints will reject requests. "
            "Set RLM_ADMIN_TOKEN, RLM_API_TOKEN, or RLM_WS_TOKEN."
        )
    # RLM_JWT_SECRET is checked lazily (only when JWT is used), but warn early
    _jwt_secret = os.environ.get("RLM_JWT_SECRET", "")
    if _jwt_secret and len(_jwt_secret) < 32:
        gateway_log.error(
            "RLM_JWT_SECRET is set but too short (< 32 chars). "
            "JWT authentication will fail at runtime."
        )
    # Warn about bind address
    _api_bind_host = os.environ.get("RLM_API_HOST", "127.0.0.1")
    if _api_bind_host == "0.0.0.0":
        gateway_log.warn(
            "RLM_API_HOST=0.0.0.0 detected. REST API is exposed on ALL interfaces. "
            "Use 127.0.0.1 (loopback) or a VPN IP for production."
        )
    _ws_bind_host = os.environ.get("RLM_WS_HOST", "127.0.0.1")
    if _ws_bind_host == "0.0.0.0":
        gateway_log.warn(
            "RLM_WS_HOST=0.0.0.0 detected. WebSocket server is exposed on ALL interfaces. "
            "Use 127.0.0.1 (loopback) or a VPN IP for production."
        )

    # Session Manager
    db_path = os.environ.get("RLM_DB_PATH", "rlm_sessions.db")
    state_root = os.environ.get("RLM_STATE_ROOT", "./rlm_states")
    
    _rlm_backend = os.environ.get("RLM_BACKEND", "openai")
    _rlm_backend_kwargs = {"model_name": os.environ.get("RLM_MODEL_PLANNER", os.environ.get("RLM_MODEL", "gpt-4o-mini"))}
    _tier_backends, _tier_kwargs = build_tier_backends(_rlm_backend, _rlm_backend_kwargs)
    default_rlm_kwargs = {
        "backend": _rlm_backend,
        "backend_kwargs": _rlm_backend_kwargs,
        "environment": "local",
        "max_iterations": int(os.environ.get("RLM_MAX_ITERATIONS", "30")),
        "max_depth": int(os.environ.get("RLM_MAX_DEPTH", "3")),
        "persistent": True,
        "verbose": True,
        "event_bus": app.state.event_bus,
        "other_backends": _tier_backends or None,
        "other_backend_kwargs": _tier_kwargs or None,
    }

    # Hooks
    app.state.hooks = HookSystem()

    app.state.session_manager = SessionManager(
        db_path=db_path,
        state_root=state_root,
        default_rlm_kwargs=default_rlm_kwargs,
        hooks=app.state.hooks,
    )

    # Supervisor
    supervisor_config = SupervisorConfig(
        max_execution_time=int(os.environ.get("RLM_TIMEOUT", "120")),
        max_consecutive_errors=int(os.environ.get("RLM_MAX_ERRORS", "5")),
    )
    app.state.supervisor = RLMSupervisor(
        default_config=supervisor_config,
        session_manager=app.state.session_manager,
    )

    # Plugin Loader
    app.state.plugin_loader = PluginLoader()

    # Event Router
    app.state.event_router = EventRouter()

    # Runtime guard boundary — first compatibility layer for future native extraction
    app.state.runtime_guard = build_runtime_guard_from_env()
    app.state.exec_approval = app.state.runtime_guard.approvals
    app.state.exec_approval_required = app.state.runtime_guard.exec_approval_required

    # Phase 9.1: MCP Skills
    _skills_dir = os.environ.get(
        "RLM_SKILLS_DIR",
        os.path.join(os.path.dirname(__file__), "..", "skills"),
    )
    _skill_loader = SkillLoader()
    _all_skills = _skill_loader.load_from_dir(_skills_dir)
    _eligible_skills = _skill_loader.filter_eligible(_all_skills)
    app.state.skill_loader = _skill_loader
    app.state.skills_all = _all_skills
    app.state.skills_eligible = _eligible_skills
    app.state.session_manager.add_close_callback(
        lambda session: app.state.skill_loader.deactivate_scope(session.session_id)
    )
    # Index compacto — fallback estático para system prompt base (~570 tokens)
    # Por-request: contexto dinâmico com keyword routing é gerado no webhook handler
    app.state.skill_context = _skill_loader.build_system_prompt_context(
        _eligible_skills, mode="compact"
    )
    # SIF v3 — índice compacto + hints semânticos + recipes curtos.
    # Mantém o prompt enxuto sem expor codex executável inline.
    app.state.sif_table = _skill_loader.build_system_prompt_context(
        _eligible_skills, mode="sif"
    )

    # Phase 8: Scheduler
    def _run_scheduled_job(client_id: str, prompt: str):
        session = app.state.session_manager.get_or_create(client_id)
        # Use existing event loop without blocking scheduler thread
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            pass
        if loop and loop.is_running():
            loop.call_soon_threadsafe(
                lambda: loop.run_in_executor(None, lambda: app.state.supervisor.execute(session, prompt))
            )
        else:
            # Fallback if no loop
            app.state.supervisor.execute(session, prompt)

    app.state.scheduler = RLMScheduler(execute_fn=_run_scheduled_job)
    app.state.scheduler.start()

    ws_disabled = os.environ.get("RLM_WS_DISABLED", "false").lower() == "true"
    if not ws_disabled:
        app.state.ws_thread = start_ws_server(
            event_bus=app.state.event_bus,
            host=os.environ.get("RLM_WS_HOST", "127.0.0.1"),
            port=int(os.environ.get("RLM_WS_PORT", "8765")),
            ws_token=os.environ.get("RLM_WS_TOKEN"),
        )

    gateway_log.info("✓ Session Manager initialized")
    gateway_log.info("✓ Supervisor initialized")
    gateway_log.info(f"✓ Plugins available: {[p.name for p in app.state.plugin_loader.list_available()]}")
    gateway_log.info(f"✓ Routes configured: {len(app.state.event_router.routes)}")
    gateway_log.info("✓ Scheduler started")
    gateway_log.info("✓ HookSystem initialized")
    _skill_names = [s.name for s in _eligible_skills]
    gateway_log.info(f"✓ Skills eligible: {_skill_names} ({len(_all_skills)} total)")
    _approval_stats = app.state.exec_approval.stats()
    gateway_log.info(
        f"✓ ExecApprovalGate: required={app.state.exec_approval_required} timeout={_approval_stats.get('default_timeout_s', '?')}s"
    )
    if os.environ.get("RLM_HOOK_TOKEN"):
        gateway_log.info("✓ External webhook receiver: POST /api/hooks/{token}")
    if os.environ.get("RLM_API_TOKEN"):
        gateway_log.info("✓ OpenAI-compat API: POST /v1/chat/completions")
    if os.environ.get("DISCORD_APP_PUBLIC_KEY") or os.environ.get("RLM_DISCORD_SKIP_VERIFY", "") == "true":
        gateway_log.info("✓ Discord gateway: POST /discord/interactions")
    if os.environ.get("WHATSAPP_VERIFY_TOKEN"):
        gateway_log.info("✓ WhatsApp gateway: GET+POST /whatsapp/webhook")
    if os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_SIGNING_SECRET"):
        gateway_log.info("✓ Slack gateway: POST /slack/events")
    gateway_log.info("✓ WebChat: GET /webchat")
    if ws_disabled:
        gateway_log.info("• WebSocket: desabilitado via RLM_WS_DISABLED=true")
    elif app.state.ws_thread is not None:
        gateway_log.info("✓ WebSocket observability thread started")
    else:
        gateway_log.warn("WebSocket observability unavailable (missing dependency ou startup failure)")

    # DrainGuard + Health Monitor
    app.state.drain_guard = DrainGuard(event_bus=app.state.event_bus)
    app.state.health_monitor = HealthMonitor(
        event_bus=app.state.event_bus, interval_s=30.0,
    )
    app.state.health_monitor.register("api", lambda: True)
    app.state.health_monitor.start()
    gateway_log.info("✓ Health Monitor started")

    # ── Channel Infrastructure (bootstrap unificado — Camada 4) ─────
    # ANTES: ~140 linhas de init inline (CSR, MessageBus, adapters,
    #   probers, Telegram gateway, Discord/WhatsApp/Slack/WebChat/TUI).
    # AGORA: bootstrap_channel_infrastructure() centraliza tudo.
    # Qualquer canal futuro: 1) crie ChannelAdapter, 2) adicione
    # ChannelDescriptor em channel_bootstrap._CHANNEL_DESCRIPTORS. Fim.
    from rlm.core.comms.channel_bootstrap import bootstrap_channel_infrastructure

    _channel_infra = bootstrap_channel_infrastructure(
        session_manager=app.state.session_manager,
        event_bus=app.state.event_bus,
        db_path=db_path,
        start_gateways=False,       # gateways ativos iniciam abaixo
        config=_cfg,
    )
    app.state.channel_status_registry = _channel_infra.csr
    app.state.message_bus = _channel_infra.message_bus
    app.state.delivery_worker = _channel_infra.delivery_worker
    app.state.use_message_bus = _channel_infra.use_message_bus
    app.state.channel_infra = _channel_infra

    await _channel_infra.delivery_worker.start()
    app.state.health_monitor.register(
        "delivery_worker", _channel_infra.delivery_worker.is_alive,
    )
    gateway_log.info("✓ Channel bootstrap complete — DeliveryWorker started")

    # Telegram Gateway (server-specific — requer endpoints HTTP)
    app.state.telegram_gateway = None
    _tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if _tg_token:
        try:
            from rlm.gateway.telegram_gateway import TelegramGateway, GatewayConfig as TGConfig
            _tg_api_url = resolve_internal_api_base_url()
            _tg_config = TGConfig(
                bot_token=_tg_token,
                api_base_url=_tg_api_url,
                api_timeout_s=int(os.environ.get("RLM_TG_API_TIMEOUT", "120")),
                max_requests_per_min=int(os.environ.get("RLM_RATE_LIMIT", "10")),
            )
            _tg_allowed = os.environ.get("RLM_ALLOWED_CHATS", "").strip()
            if _tg_allowed:
                _tg_config.allowed_chat_ids = [int(x.strip()) for x in _tg_allowed.split(",") if x.strip()]
            _tg_gw = TelegramGateway(config=_tg_config)
            _tg_gw.run_in_thread()
            app.state.telegram_gateway = _tg_gw
            _channel_infra.csr.mark_running("telegram")
            gateway_log.info(f"✓ Telegram Gateway started (bridge → {_tg_api_url})")
        except Exception as e:
            gateway_log.error(f"Telegram Gateway failed to start: {e}")
            _channel_infra.csr.mark_stopped("telegram", error=str(e))

    gateway_log.info("Ready to receive events.")

    yield  # --- App is running ---

    # --- Shutdown ---
    gateway_log.info("Shutting down...")

    # Fase 1: Drain — rejeita novos requests, espera ativos
    app.state.drain_guard.start_draining()
    _drain_timeout = int(os.environ.get("RLM_DRAIN_TIMEOUT", "30"))
    app.state.drain_guard.wait_active(timeout=_drain_timeout)

    # Fase 2: Parar DeliveryWorker, Telegram Gateway, monitoramento e scheduler
    app.state.delivery_worker.stop()
    gateway_log.info("DeliveryWorker stopped")
    if app.state.telegram_gateway is not None:
        app.state.telegram_gateway.stop()
        gateway_log.info("Telegram Gateway stopped")
    app.state.health_monitor.dispose()
    app.state.scheduler.stop()

    # Fase 3: Cleanup de recursos
    app.state.skill_loader.deactivate_all()
    app.state.supervisor.shutdown()
    app.state.session_manager.close_all()
    gateway_log.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RLM Automation Gateway",
    version="2.0.0",
    description="Event-driven daemon powered by the RLM engine with OpenClaw-like infrastructure.",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — Phase 9.4 (CiberSeg)
# Lê origens permitidas de RLM_CORS_ORIGINS (vírgula-separado).
# Se não definida, bloqueia tudo (nenhuma origem externa).
# ---------------------------------------------------------------------------
_cors_origins_raw = os.environ.get("RLM_CORS_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()] if _cors_origins_raw else []

if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Hook-Token"],
    )

# ---------------------------------------------------------------------------
# Drain Middleware — rejeita novos requests com 503 durante drain
# ---------------------------------------------------------------------------

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse as StarletteJSONResponse


class DrainMiddleware(BaseHTTPMiddleware):
    """Rejeita requests com 503 durante graceful drain (exceto /health)."""

    async def dispatch(self, request: Request, call_next):
        # /health sempre passa — necessário para probes externos
        if request.url.path == "/health":
            return await call_next(request)

        drain_guard = getattr(request.app.state, "drain_guard", None)
        if drain_guard is not None and not drain_guard.enter_request():
            return StarletteJSONResponse(
                status_code=503,
                content={"detail": "Server is draining — try again later"},
            )
        try:
            return await call_next(request)
        finally:
            if drain_guard is not None:
                drain_guard.exit_request()


app.add_middleware(DrainMiddleware)

# ---------------------------------------------------------------------------
# Optional routers (condicionais via env vars)
# ---------------------------------------------------------------------------

_hook_token = os.environ.get("RLM_HOOK_TOKEN", "").strip()
if _hook_token:
    app.include_router(create_webhook_router(_hook_token))

_api_token = os.environ.get("RLM_API_TOKEN", "").strip()
app.include_router(create_openai_compat_router(_api_token))

# Channel gateways — ativos se as env vars obrigatórias estiverem presentes.
# O pacote canônico dos transports Python agora é rlm.gateway.
from rlm.gateway.transport_router import mount_channel_routers
mount_channel_routers(app)

# Brain router — endpoints /brain/* (ToolDispatcher, PermissionPolicy, SessionJournal)
from rlm.server.brain_router import router as _brain_router
app.include_router(_brain_router)

# WS Gateway — endpoint /ws/gateway (protocolo ws-protocol.v1.json, para consumers remotos)
from rlm.gateway.ws_gateway_endpoint import router as _ws_gateway_router
app.include_router(_ws_gateway_router)


# ---------------------------------------------------------------------------
# MessageBus helpers (Phase 3 multichannel)
# ---------------------------------------------------------------------------

def _normalize_webhook_payload(client_id: str, payload: dict) -> InboundMessage:
    """
    Converte payload bruto do /webhook em InboundMessage canônico.

    Extrai canal a partir do prefixo de client_id (ex: "telegram:123" → "telegram").
    Todos os gateways já enviam text + from_user no payload; metadata extra
    é preservada em channel_meta.
    """
    prefix = client_id.split(":", 1)[0] if ":" in client_id else "webhook"
    text = str(payload.get("text", ""))
    from_user = str(payload.get("from_user", ""))
    content_type = str(payload.get("type", "text"))

    meta: dict = {}
    if prefix == "whatsapp":
        meta = {
            "wa_id": payload.get("wa_id", ""),
            "message_id": payload.get("message_id", ""),
        }
    elif prefix == "slack":
        meta = {
            "thread_ts": payload.get("thread_ts", ""),
            "team_id": payload.get("team_id", ""),
            "channel": payload.get("channel", ""),
        }
    elif prefix == "telegram":
        meta = {"chat_id": payload.get("chat_id", "")}
    elif prefix == "discord":
        # client_id = "discord:{guild_id}:{user_id}"
        parts = client_id.split(":")
        meta = {
            "guild_id": parts[1] if len(parts) > 1 else "",
            "user_id": parts[2] if len(parts) > 2 else "",
        }
    elif prefix == "webchat":
        meta = {"session_id": payload.get("session_id", "")}

    return InboundMessage(
        channel=prefix,
        client_id=client_id,
        text=text,
        from_user=from_user,
        content_type=content_type,
        channel_meta=meta,
    )


# ---------------------------------------------------------------------------
# Webhook Endpoint (Core)
# ---------------------------------------------------------------------------

@app.post("/webhook/{client_id}")
async def receive_webhook(client_id: str, request: Request):
    """
    Core entrypoint for events.
    
    Creates or resumes a session for the client_id, routes the event
    through the EventRouter, loads appropriate plugins, and executes
    via the Supervisor with safety boundaries.
    """
    _require_internal_api_auth(request)

    # Parse payload
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    sm: SessionManager = request.app.state.session_manager
    services = RuntimeDispatchServices(
        session_manager=sm,
        supervisor=request.app.state.supervisor,
        plugin_loader=request.app.state.plugin_loader,
        event_router=request.app.state.event_router,
        hooks=request.app.state.hooks,
        skill_loader=request.app.state.skill_loader,
        runtime_guard=request.app.state.runtime_guard,
        eligible_skills=request.app.state.skills_eligible,
        skill_context=request.app.state.skill_context,
        exec_approval=request.app.state.exec_approval,
        exec_approval_required=request.app.state.exec_approval_required,
    )

    session = sm.get_or_create(client_id)
    ingress_payload = _summarize_inbound_payload(client_id, payload)
    ingress_payload["user_id"] = session.user_id
    sm.log_event(session.session_id, "webhook_received", ingress_payload)
    sm.log_operation(
        session.session_id,
        "message.receive",
        phase="ingress",
        status="accepted",
        source="webhook",
        payload={
            "client_id": client_id,
            "user_id": session.user_id,
            "originating_channel": session.originating_channel,
            "delivery_context": session.delivery_context,
            "from_user": ingress_payload.get("from_user", ""),
            "text_preview": ingress_payload.get("text_preview", ""),
        },
    )
    services.hooks.trigger(
        "message.received",
        session_id=session.session_id,
        context={"client_id": client_id, "payload": payload},
    )

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: dispatch_runtime_prompt_sync(
                services,
                client_id,
                payload,
                session=session,
                record_conversation=False,
                source_name="webhook",
            ),
        )
    except RuntimeDispatchRejected as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload)
    except Exception as e:
        raise HTTPException(500, f"Execution failed: {e}")

    # ── MessageBus routing (Phase 3 multichannel) ─────────────────────────
    # Quando ativo, normaliza mensagem inbound, registra no bus e roteia
    # a resposta via Outbox → DeliveryWorker → ChannelRegistry adapter.
    # O gateway recebe already_replied=True e NÃO reenvia.
    if getattr(request.app.state, "use_message_bus", False):
        try:
            bus = request.app.state.message_bus
            inbound_msg = _normalize_webhook_payload(client_id, payload)
            inbound_envelope = bus.ingest(inbound_msg)

            response_text = result.get("response", "") if isinstance(result, dict) else ""
            was_replied = result.get("already_replied", False) if isinstance(result, dict) else False

            if response_text and not was_replied:
                bus.route_response(
                    inbound_envelope,
                    response_text,
                    session,
                    session_id=session.session_id,
                )
                # Sinaliza ao gateway que a entrega será feita pelo DeliveryWorker
                if isinstance(result, dict):
                    result["already_replied"] = True
        except Exception as exc:
            # Bus failure NUNCA deve bloquear o fluxo principal.
            # Log e segue — gateway entrega normalmente como fallback.
            gateway_log.error(
                "MessageBus routing failed (non-fatal, gateway fallback active)",
                error=str(exc),
                client_id=client_id,
            )

    return result


# ---------------------------------------------------------------------------
# Session Endpoints
# ---------------------------------------------------------------------------

@app.get("/sessions")
async def list_sessions(request: Request, status: str | None = None):
    """List all sessions, optionally filtered by status."""
    _require_admin_api_auth(request)
    sm: SessionManager = request.app.state.session_manager
    sessions = sm.list_sessions(status=status)
    return {
        "count": len(sessions),
        "sessions": [sm.session_to_dict(s) for s in sessions],
    }


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get details of a specific session."""
    _require_admin_api_auth(request)
    sm: SessionManager = request.app.state.session_manager
    session = sm.get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id} not found")
    return sm.session_to_dict(session)


@app.delete("/sessions/{session_id}")
async def abort_session(session_id: str, request: Request):
    """Abort a running session or close an idle one."""
    _require_admin_api_auth(request)
    sm: SessionManager = request.app.state.session_manager
    supervisor: RLMSupervisor = request.app.state.supervisor

    # Try to abort if running
    if supervisor.is_running(session_id):
        supervisor.abort(session_id, reason="Aborted via API")
        sm.log_event(session_id, "aborted", {"reason": "API request"})
        return {"status": "abort_signal_sent", "session_id": session_id}

    # Otherwise close the session
    closed = sm.close_session(session_id)
    if closed:
        return {"status": "session_closed", "session_id": session_id}
    raise HTTPException(404, f"Session {session_id} not found or not active")


@app.get("/sessions/{session_id}/events")
async def get_session_events(session_id: str, request: Request, limit: int = 50):
    """Get event log for a session."""
    _require_admin_api_auth(request)
    sm: SessionManager = request.app.state.session_manager
    events = sm.get_events(session_id, limit=limit)
    return {"session_id": session_id, "events": events}


@app.get("/sessions/{session_id}/operations")
async def get_session_operations(session_id: str, request: Request, limit: int = 50):
    """Get structured operation log for a session."""
    _require_admin_api_auth(request)
    sm: SessionManager = request.app.state.session_manager
    operations = sm.get_operation_log(session_id, limit=limit)
    return {"session_id": session_id, "operations": operations}


@app.get("/sessions/{session_id}/runtime")
async def get_session_runtime(
    session_id: str,
    request: Request,
    coordination_limit: int = 0,
    coordination_operation: str | None = None,
    coordination_topic: str | None = None,
    coordination_branch_id: int | None = None,
):
    """Retorna snapshot do runtime workbench da sessão ativa."""
    _require_admin_api_auth(request)
    sm: SessionManager = request.app.state.session_manager
    session = sm.get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id} not found")
    if session.rlm_instance is None:
        raise HTTPException(409, f"Session {session_id} has no active RLM instance")

    env = getattr(session.rlm_instance, "_persistent_env", None)
    snapshot = getattr(env, "get_runtime_state_snapshot", None)
    if not callable(snapshot):
        raise HTTPException(409, f"Session {session_id} has no runtime workbench snapshot")

    return {
        "session_id": session_id,
        "runtime": snapshot(
            coordination_limit=coordination_limit,
            coordination_operation=coordination_operation,
            coordination_topic=coordination_topic,
            coordination_branch_id=coordination_branch_id,
        ),
    }


# ---------------------------------------------------------------------------
# Plugin & Route Endpoints
# ---------------------------------------------------------------------------

@app.get("/plugins")
async def list_plugins(request: Request):
    """List all available plugins."""
    _require_admin_api_auth(request)
    loader: PluginLoader = request.app.state.plugin_loader
    manifests = loader.list_available()
    return {
        "count": len(manifests),
        "plugins": [loader.manifest_to_dict(m) for m in manifests],
    }


@app.get("/routes")
async def list_routes(request: Request):
    """List all configured event routes."""
    _require_admin_api_auth(request)
    router: EventRouter = request.app.state.event_router
    return {"routes": router.list_routes()}


# ---------------------------------------------------------------------------
# Skills Endpoints
# ---------------------------------------------------------------------------

@app.get("/skills")
async def list_skills(request: Request):
    """Lista todas as skills descobertas e quais estão elegíveis no ambiente atual."""
    _require_admin_api_auth(request)
    skill_loader: SkillLoader = request.app.state.skill_loader
    all_skills = request.app.state.skills_all
    eligible_names = set(request.app.state.skill_loader.get_active_names())
    eligible_skill_names = {s.name for s in request.app.state.skills_eligible}
    return {
        "count": len(all_skills),
        "active_mcp": skill_loader.get_active_names(),
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "has_mcp": s.has_mcp,
                "namespace": s.namespace_name if s.has_mcp else None,
                "eligible": s.name in eligible_skill_names,
                "active": s.name in eligible_names,
                "requires_bins": s.requires_bins,
            }
            for s in all_skills
        ],
    }


@app.get("/skills/telemetry")
async def list_skills_telemetry(request: Request, include_recent: bool = False, limit: int = 20):
    """Retorna resumo operacional da telemetria de skills."""
    _require_admin_api_auth(request)
    telemetry = get_skill_telemetry()
    safe_limit = max(1, min(limit, 100))
    summary = telemetry.get_summary(include_recent=include_recent, limit=safe_limit)
    summary["known_skills"] = len(request.app.state.skills_all)
    summary["eligible_skills"] = len(request.app.state.skills_eligible)
    return summary


@app.get("/skills/telemetry/{skill_name}")
async def get_skill_telemetry_report(skill_name: str, request: Request, limit: int = 20):
    """Retorna estatísticas e eventos recentes de uma skill específica."""
    _require_admin_api_auth(request)
    all_skill_names = {skill.name for skill in request.app.state.skills_all}
    if skill_name not in all_skill_names:
        raise HTTPException(404, f"Skill '{skill_name}' não encontrada")
    telemetry = get_skill_telemetry()
    safe_limit = max(1, min(limit, 100))
    return telemetry.get_skill_report(skill_name, limit=safe_limit)


@app.get("/skills/telemetry/compose")
async def get_compose_telemetry(request: Request, limit: int = 10):
    """Retorna as composições mais frequentes observadas entre skills."""
    _require_admin_api_auth(request)
    telemetry = get_skill_telemetry()
    safe_limit = max(1, min(limit, 100))
    return telemetry.get_transition_report(limit=safe_limit)


@app.get("/skills/telemetry/session/{session_id}/compose")
async def get_session_compose_telemetry(session_id: str, request: Request, limit: int = 10):
    """Retorna as transições observadas em uma sessão específica."""
    _require_admin_api_auth(request)
    telemetry = get_skill_telemetry()
    safe_limit = max(1, min(limit, 100))
    return telemetry.get_transition_report(session_id=session_id, limit=safe_limit)


@app.get("/skills/telemetry/search")
async def search_skill_traces(request: Request, query: str, skill_name: str = "", limit: int = 5):
    """Recupera traces relevantes por overlap lexical com a query atual."""
    _require_admin_api_auth(request)
    telemetry = get_skill_telemetry()
    safe_limit = max(1, min(limit, 20))
    return {
        "query": query,
        "skill_name": skill_name,
        "matches": telemetry.get_relevant_traces(query, skill_name=skill_name, limit=safe_limit),
    }


# ---------------------------------------------------------------------------
# Cron / Scheduler Endpoints
# ---------------------------------------------------------------------------

class CronJobRequest(BaseModel):
    name: str
    client_id: str
    prompt: str
    schedule: str  # ex.: "every:30m", "at:2026-01-01T08:00:00", "0 8 * * *"
    enabled: bool = True


@app.get("/cron/jobs")
async def list_cron_jobs(request: Request):
    """Lista todos os jobs agendados."""
    _require_admin_api_auth(request)
    scheduler: RLMScheduler = request.app.state.scheduler
    jobs = scheduler.list_jobs()
    return {
        "count": len(jobs),
        "jobs": [
            {
                "name": j.name,
                "schedule": j.schedule,
                "client_id": j.client_id,
                "prompt": j.prompt,
                "enabled": j.enabled,
                "run_count": j.run_count,
                "last_run": j.last_run,
                "last_error": j.last_error,
            }
            for j in jobs
        ],
    }


@app.post("/cron/jobs", status_code=201)
async def create_cron_job(body: CronJobRequest, request: Request):
    """Cria ou substitui um job agendado."""
    _require_admin_api_auth(request)
    scheduler: RLMScheduler = request.app.state.scheduler
    job = CronJob(
        name=body.name,
        client_id=body.client_id,
        prompt=body.prompt,
        schedule=body.schedule,
        enabled=body.enabled,
    )
    scheduler.add_job(job)
    return {"status": "created", "name": body.name}


@app.delete("/cron/jobs/{job_name}")
async def delete_cron_job(job_name: str, request: Request):
    """Remove um job agendado pelo nome."""
    _require_admin_api_auth(request)
    scheduler: RLMScheduler = request.app.state.scheduler
    removed = scheduler.remove_job(job_name)
    if not removed:
        raise HTTPException(404, f"Job '{job_name}' não encontrado")
    return {"status": "removed", "name": job_name}


# ---------------------------------------------------------------------------
# Hooks Endpoints
# ---------------------------------------------------------------------------

@app.get("/hooks/stats")
async def hooks_stats(request: Request):
    """Retorna estatísticas dos hooks registrados."""
    _require_admin_api_auth(request)
    hooks: HookSystem = request.app.state.hooks
    return hooks.get_stats()


# ---------------------------------------------------------------------------
# Exec Approval Endpoints
# ---------------------------------------------------------------------------

@app.get("/exec/pending")
async def list_pending_approvals(request: Request):
    """Lista execuções aguardando aprovação humana."""
    _require_admin_api_auth(request)
    gate: ExecApprovalGate = request.app.state.exec_approval
    return {"pending": gate.list_pending(), "stats": gate.stats()}


@app.post("/exec/approve/{request_id}")
async def approve_exec(request_id: str, request: Request):
    """Aprova uma execução pendente. O REPL é desbloqueado imediatamente."""
    _require_admin_api_auth(request)
    gate: ExecApprovalGate = request.app.state.exec_approval
    resolved_by = request.headers.get("X-Operator", "human")
    ok = gate.approve(request_id, resolved_by=resolved_by)
    if not ok:
        raise HTTPException(404, f"Pending approval '{request_id}' not found")
    return {"status": "approved", "id": request_id}


@app.post("/exec/deny/{request_id}")
async def deny_exec(request_id: str, request: Request):
    """Nega uma execução pendente. O REPL recebe PermissionError."""
    _require_admin_api_auth(request)
    gate: ExecApprovalGate = request.app.state.exec_approval
    resolved_by = request.headers.get("X-Operator", "human")
    ok = gate.deny(request_id, resolved_by=resolved_by)
    if not ok:
        raise HTTPException(404, f"Pending approval '{request_id}' not found")
    return {"status": "denied", "id": request_id}


@app.get("/exec/{request_id}")
async def get_exec_record(request_id: str, request: Request):
    """Retorna o estado de uma solicitação de aprovação (pending ou resolved)."""
    _require_admin_api_auth(request)
    gate: ExecApprovalGate = request.app.state.exec_approval
    record = gate.get_record(request_id)
    if not record:
        raise HTTPException(404, f"Approval record '{request_id}' not found")
    return record


# ---------------------------------------------------------------------------
# Channel Discovery (Camada 4 — Service Discovery)
# ---------------------------------------------------------------------------

@app.get("/api/channels/status")
async def channels_status(request: Request):
    """
    Retorna snapshot completo de todos os canais registrados.

    Inclui: identidade do bot, status running/healthy, resultado do último probe,
    metadata de configuração. Equivale a OpenClaw ChannelManager.getRuntimeSnapshot().
    """
    _require_admin_api_auth(request)
    csr: ChannelStatusRegistry = request.app.state.channel_status_registry
    return csr.summary()


@app.post("/api/channels/{channel_id}/probe")
async def channels_probe(channel_id: str, request: Request):
    """
    Executa probe sob demanda para um canal específico.

    Útil para diagnosticar conectividade após atualização de VPS ou mudança de rede.
    """
    _require_admin_api_auth(request)
    csr: ChannelStatusRegistry = request.app.state.channel_status_registry
    snap = csr.get(channel_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not registered")
    result = csr.probe(channel_id)
    return {
        "channel_id": channel_id,
        "probe": {
            "ok": result.ok,
            "elapsed_ms": round(result.elapsed_ms, 1),
            "error": result.error,
            "identity": {
                "bot_id": result.identity.bot_id,
                "username": result.identity.username,
                "display_name": result.identity.display_name,
            } if result.identity else None,
        },
        "snapshot": csr.get(channel_id).to_dict() if csr.get(channel_id) else None,
    }


@app.post("/api/channels/send")
async def channels_send(request: Request):
    """
    Envia mensagem cross-channel via MessageBus ou ChannelRegistry.

    Body JSON: {"target_client_id": "telegram:12345", "message": "texto"}
    """
    _require_admin_api_auth(request)
    body = await request.json()
    target = body.get("target_client_id", "").strip()
    message = body.get("message", "").strip()
    if not target or not message:
        raise HTTPException(400, "target_client_id e message são obrigatórios")

    # Tenta via MessageBus primeiro; fallback para ChannelRegistry
    try:
        from rlm.core.comms.message_bus import get_message_bus
        from rlm.core.comms.envelope import Envelope, Direction
        bus = get_message_bus()
        env = Envelope(
            source_channel="tui",
            source_client_id="operator",
            target_client_id=target,
            direction=Direction.OUTBOUND,
            text=message,
        )
        eid = bus.enqueue_outbound(env)
        return {"status": "queued", "envelope_id": eid, "via": "message_bus"}
    except RuntimeError:
        pass

    try:
        from rlm.plugins.channel_registry import ChannelRegistry
        ChannelRegistry.reply(target, message)
        return {"status": "sent", "via": "channel_registry"}
    except Exception as exc:
        raise HTTPException(502, f"Falha ao enviar: {exc}") from exc


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check(request: Request):
    """Health check with system status and component health.

    Endpoint publico (sem autenticação) — necessário para probes do TUI e
    de orquestradores externos. Não expõe dados sensíveis; o servidor já
    escuta apenas em loopback por padrão.
    """
    sm: SessionManager = request.app.state.session_manager
    supervisor: RLMSupervisor = request.app.state.supervisor
    loader: PluginLoader = request.app.state.plugin_loader
    monitor: HealthMonitor = request.app.state.health_monitor

    active_sessions = sm.list_sessions(status="idle") + sm.list_sessions(status="running")
    running = supervisor.get_active_sessions()

    health_report = monitor.get_health_report()

    # Channel discovery summary
    csr: ChannelStatusRegistry = request.app.state.channel_status_registry
    _ch_summary = csr.summary()

    return {
        "status": health_report["status"],
        "engine": "RLM Automation Gateway v2.0",
        "uptime_s": health_report["uptime_s"],
        "active_sessions": len(active_sessions),
        "running_executions": len(running),
        "plugins_available": len(loader.list_available()),
        "model": os.environ.get("RLM_MODEL", "gpt-4o-mini"),
        "draining": request.app.state.drain_guard.is_draining,
        "components": health_report["components"],
        "channels": _ch_summary,
    }


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def start_server(host: str = "127.0.0.1", port: int = 5000):
    """Start the RLM Gateway server.

    Phase 9.3 (CiberSeg): bind padrão alterado para 127.0.0.1 (loopback).
    Para expor via VPN WireGuard: start_server(host="10.0.0.1").
    NUNCA use host="0.0.0.0" em produção sem nginx/Caddy + TLS na frente.
    """
    uvicorn = import_module("uvicorn")
    uvicorn.run(
        "rlm.server.api:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    start_server()
