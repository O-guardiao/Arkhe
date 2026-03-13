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
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
load_dotenv()  # carrega .env do diretório atual ou ~/.rlm/.env

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from rlm.core.session import SessionManager
from rlm.core.supervisor import RLMSupervisor, SupervisorConfig, ExecutionResult
from rlm.core.hooks import HookSystem
from rlm.core.handoff import make_handoff_fn, VALID_HANDOFF_ROLES
from rlm.core.role_orchestrator import PENDING_HANDOFFS_KEY, orchestrate_roles
from rlm.core.scheduler import RLMScheduler, CronJob
from rlm.core.skill_loader import SkillLoader
from rlm.core.exec_approval import ExecApprovalGate
from rlm.server.webhook_dispatch import create_webhook_router
from rlm.server.openai_compat import create_openai_compat_router
from rlm.plugins import PluginLoader
from rlm.server.event_router import EventRouter
from rlm.core.structured_log import get_logger
from rlm.core.skill_telemetry import get_skill_telemetry

# Channel gateways — importados aqui; registrados condicionalmente abaixo
try:
    from rlm.server.discord_gateway import router as _discord_router
    _HAS_DISCORD_GW = True
except ImportError:
    _HAS_DISCORD_GW = False

try:
    from rlm.server.whatsapp_gateway import router as _whatsapp_router
    _HAS_WHATSAPP_GW = True
except ImportError:
    _HAS_WHATSAPP_GW = False

try:
    from rlm.server.slack_gateway import router as _slack_router
    _HAS_SLACK_GW = True
except ImportError:
    _HAS_SLACK_GW = False

try:
    from rlm.server.webchat import router as _webchat_router
    _HAS_WEBCHAT = True
except ImportError:
    _HAS_WEBCHAT = False

gateway_log = get_logger("api")


# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated @app.on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # --- Startup ---
    gateway_log.info("Initializing infrastructure...")

    # Phase 9.4 (CiberSeg): Validate critical env vars before anything else
    _missing_critical: list[str] = []
    if not os.environ.get("RLM_HOOK_TOKEN") and not os.environ.get("RLM_API_TOKEN"):
        gateway_log.warn(
            "Neither RLM_HOOK_TOKEN nor RLM_API_TOKEN is set. "
            "All API endpoints will be unauthenticated."
        )
    # RLM_JWT_SECRET is checked lazily (only when JWT is used), but warn early
    _jwt_secret = os.environ.get("RLM_JWT_SECRET", "")
    if _jwt_secret and len(_jwt_secret) < 32:
        gateway_log.error(
            "RLM_JWT_SECRET is set but too short (< 32 chars). "
            "JWT authentication will fail at runtime."
        )
    # Warn about bind address
    _bind_host = os.environ.get("RLM_HOST", "127.0.0.1")
    if _bind_host == "0.0.0.0":
        gateway_log.warn(
            "RLM_HOST=0.0.0.0 detected. Server is exposed on ALL interfaces. "
            "Use 127.0.0.1 (loopback) or a VPN IP for production."
        )

    # Session Manager
    db_path = os.environ.get("RLM_DB_PATH", "rlm_sessions.db")
    state_root = os.environ.get("RLM_STATE_ROOT", "./rlm_states")
    
    default_rlm_kwargs = {
        "backend": "openai",
        "backend_kwargs": {"model_name": os.environ.get("RLM_MODEL", "gpt-4o-mini")},
        "environment": "local",
        "max_iterations": int(os.environ.get("RLM_MAX_ITERATIONS", "30")),
        "persistent": True,
        "verbose": True,
    }

    app.state.session_manager = SessionManager(
        db_path=db_path,
        state_root=state_root,
        default_rlm_kwargs=default_rlm_kwargs,
    )

    # Supervisor
    supervisor_config = SupervisorConfig(
        max_execution_time=int(os.environ.get("RLM_TIMEOUT", "120")),
        max_consecutive_errors=int(os.environ.get("RLM_MAX_ERRORS", "5")),
    )
    app.state.supervisor = RLMSupervisor(default_config=supervisor_config)

    # Plugin Loader
    app.state.plugin_loader = PluginLoader()

    # Event Router
    app.state.event_router = EventRouter()

    # Hooks
    app.state.hooks = HookSystem()

    # Phase 9.2: Exec Approval Gate
    _approval_timeout = int(os.environ.get("RLM_EXEC_APPROVAL_TIMEOUT", "60"))
    _approval_required = os.environ.get("RLM_EXEC_APPROVAL_REQUIRED", "false").lower() == "true"
    app.state.exec_approval = ExecApprovalGate(default_timeout_s=_approval_timeout)
    app.state.exec_approval_required = _approval_required

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

    gateway_log.info("✓ Session Manager initialized")
    gateway_log.info("✓ Supervisor initialized")
    gateway_log.info(f"✓ Plugins available: {[p.name for p in app.state.plugin_loader.list_available()]}")
    gateway_log.info(f"✓ Routes configured: {len(app.state.event_router.routes)}")
    gateway_log.info("✓ Scheduler started")
    gateway_log.info("✓ HookSystem initialized")
    _skill_names = [s.name for s in _eligible_skills]
    gateway_log.info(f"✓ Skills eligible: {_skill_names} ({len(_all_skills)} total)")
    gateway_log.info(f"✓ ExecApprovalGate: required={_approval_required} timeout={_approval_timeout}s")
    if os.environ.get("RLM_HOOK_TOKEN"):
        gateway_log.info("✓ External webhook receiver: POST /api/hooks/{token}")
    if os.environ.get("RLM_API_TOKEN"):
        gateway_log.info("✓ OpenAI-compat API: POST /v1/chat/completions")
    # Channel gateways
    if os.environ.get("DISCORD_APP_PUBLIC_KEY") or os.environ.get("RLM_DISCORD_SKIP_VERIFY", "") == "true":
        gateway_log.info("✓ Discord gateway: POST /discord/interactions")
    if os.environ.get("WHATSAPP_VERIFY_TOKEN"):
        gateway_log.info("✓ WhatsApp gateway: GET+POST /whatsapp/webhook")
    if os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_SIGNING_SECRET"):
        gateway_log.info("✓ Slack gateway: POST /slack/events")
    gateway_log.info("✓ WebChat: GET /webchat")
    gateway_log.info("Ready to receive events.")

    yield  # --- App is running ---

    # --- Shutdown ---
    gateway_log.info("Shutting down...")
    app.state.scheduler.stop()
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
# Optional routers (condicionais via env vars)
# ---------------------------------------------------------------------------

_hook_token = os.environ.get("RLM_HOOK_TOKEN", "").strip()
if _hook_token:
    app.include_router(create_webhook_router(_hook_token))

_api_token = os.environ.get("RLM_API_TOKEN", "").strip()
app.include_router(create_openai_compat_router(_api_token))

# Channel gateways — ativos se as env vars obrigatórias estiverem presentes
if _HAS_DISCORD_GW and (
    os.environ.get("DISCORD_APP_PUBLIC_KEY") or
    os.environ.get("RLM_DISCORD_SKIP_VERIFY", "").lower() == "true"
):
    app.include_router(_discord_router)

if _HAS_WHATSAPP_GW and os.environ.get("WHATSAPP_VERIFY_TOKEN"):
    app.include_router(_whatsapp_router)

if _HAS_SLACK_GW and (
    os.environ.get("SLACK_BOT_TOKEN") or
    os.environ.get("SLACK_SIGNING_SECRET")
):
    app.include_router(_slack_router)

if _HAS_WEBCHAT:
    app.include_router(_webchat_router)


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
    # Parse payload
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    sm: SessionManager = request.app.state.session_manager
    supervisor: RLMSupervisor = request.app.state.supervisor
    plugin_loader: PluginLoader = request.app.state.plugin_loader
    router: EventRouter = request.app.state.event_router
    hooks: HookSystem = request.app.state.hooks
    skill_loader: SkillLoader = request.app.state.skill_loader
    eligible_skills = request.app.state.skills_eligible
    skill_context: str = request.app.state.skill_context

    # Get or create session
    session = sm.get_or_create(client_id)
    sm.log_event(session.session_id, "webhook_received", {
        "client_id": client_id,
        "payload_size": len(json.dumps(payload)),
    })

    # Disparar hook de mensagem recebida
    hooks.trigger(
        "message.received",
        session_id=session.session_id,
        context={"client_id": client_id, "payload": payload},
    )

    # Phase 11.3: STT pré-processamento — transcreve áudio ANTES de rotear
    # Se client_id for 'audio:*' e tiver payload de áudio, enriquece com 'transcription'
    payload = EventRouter.preprocess_audio(client_id, payload)

    # Route the event to get prompt + plugins
    # prompt can now be a str OR a list of dictionaries (multimodal)
    prompt, plugins_to_load = router.route(client_id, payload)

    # Phase 9.3: Prompt injection scan before the input reaches the LLM
    from rlm.core.security import auditor as _sec_auditor
    _raw_text: str = ""
    if isinstance(prompt, str):
        _raw_text = prompt
    elif isinstance(prompt, list):
        _raw_text = " ".join(p.get("text", "") for p in prompt if isinstance(p, dict))
    if _raw_text:
        _threat = _sec_auditor.audit_input(_raw_text, session_id=session.session_id)
        if _threat.threat_level == "high":
            sm.log_event(session.session_id, "security_threat_high", {
                "patterns": _threat.patterns_found,
                "text_preview": _raw_text[:200],
            })
            # High threat: reject with explicit message (does NOT execute the RLM)
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Input rejected by security policy.",
                    "threat_level": _threat.threat_level,
                    "patterns": _threat.patterns_found,
                },
            )
        elif _threat.is_suspicious:
            sm.log_event(session.session_id, "security_threat_low", {
                "patterns": _threat.patterns_found,
                "level": _threat.threat_level,
            })

    # --- Smart Skill Delivery: contexto dinâmico por query ---
    # Extrai texto da query para keyword routing (multimodal-safe)
    _query_text: str = ""
    if isinstance(prompt, str):
        _query_text = prompt
    elif isinstance(prompt, list):
        # multimodal: extrai partes text
        _query_text = " ".join(
            part.get("text", "") for part in prompt if isinstance(part, dict)
        )
    _prompt_plan = skill_loader.plan_prompt_context(eligible_skills, query=_query_text, mode="auto")
    # Gera contexto otimizado: bodies só para skills relevantes à query
    _dynamic_skill_context: str = skill_loader.build_system_prompt_context(
        eligible_skills, query=_query_text, mode="auto"
    )
    # Log de economia de tokens (debug)
    if _query_text:
        _estimate = skill_loader.estimate_tokens(eligible_skills, query=_query_text)
        gateway_log.debug(
            f"Smart Delivery: {_estimate['smart_tokens']}t vs {_estimate['full_tokens']}t full "
            f"({_estimate['saving_pct']}% saving, {_estimate['matched_skills']} skills matched)"
        )
        ranked_payload = [
            {
                "name": rank.skill.name,
                "score": rank.score,
                "telemetry": rank.telemetry_score,
                "trace": rank.trace_score,
                "cost_penalty": rank.cost_penalty,
                "risk_penalty": rank.risk_penalty,
            }
            for rank in _prompt_plan.ranked_skills[:8]
        ]
        blocked_payload = [
            {
                "name": availability.skill.name,
                "reasons": availability.reasons,
            }
            for availability in _prompt_plan.blocked_skills[:8]
        ]
        sm.log_event(session.session_id, "skill_routing", {
            "query": _query_text[:300],
            "effective_mode": _prompt_plan.effective_mode,
            "selected_skills": [skill.name for skill in _prompt_plan.expanded_skills],
            "ranked_skills": ranked_payload,
            "blocked_skills": blocked_payload,
        })
        get_skill_telemetry().record_routing(
            mode=_prompt_plan.effective_mode,
            query=_query_text,
            ranked_skills=ranked_payload,
            selected_skills=[skill.name for skill in _prompt_plan.expanded_skills],
            blocked_skills=blocked_payload,
            session_id=session.session_id,
            client_id=client_id,
        )

    # Injeta SIF table no system prompt do RLM para esta requisição
    # (skills_context vai para build_rlm_system_prompt → aparecem no system prompt, não só no REPL)
    if session.rlm_instance and (_dynamic_skill_context or skill_context):
        session.rlm_instance.skills_context = _dynamic_skill_context or skill_context

    # Load plugins and universal reply into the REPL namespace
    if session.rlm_instance:
        env = getattr(session.rlm_instance, '_persistent_env', None)
        if env and hasattr(env, 'locals'):
            if plugins_to_load:
                plugin_loader.inject_multiple(plugins_to_load, env.locals)
                
            # Phase 9.2 / 11.3: Inject reply helpers bound to this specific client_id
            from rlm.plugins.channel_registry import ChannelRegistry

            def reply(message: str) -> bool:
                """Envia resposta de texto ao usuário pelo canal original."""
                return ChannelRegistry.reply(session.client_id, message)

            def reply_audio(
                text: str,
                voice: str = "alloy",
                output_format: str = "mp3",
            ) -> bool:
                """
                Sintetiza TTS e envia como áudio pelo canal original.

                Args:
                    text: Texto para falar.
                    voice: Voz TTS — alloy, echo, fable, onyx, nova, shimmer.
                    output_format: Formato do arquivo (mp3, opus, aac, flac).
                """
                return ChannelRegistry.reply_audio(
                    session.client_id, text, voice=voice, output_format=output_format
                )

            def send_media(media_url_or_path: str, caption: str = "") -> bool:
                """Envia mídia (imagem, áudio, documento) pelo canal original."""
                return ChannelRegistry.send_media(session.client_id, media_url_or_path, caption)

            env.locals['reply'] = reply
            env.locals['reply_audio'] = reply_audio
            env.locals['send_media'] = send_media

            # Phase 9.1: Activate eligible MCP skills
            skill_loader.activate_all(
                eligible_skills,
                env.locals,
                activation_scope=session.session_id,
            )
            # Inject dynamic skill context (keyword-routed)
            if _dynamic_skill_context:
                env.locals['__rlm_skills__'] = _dynamic_skill_context
            elif skill_context:
                env.locals['__rlm_skills__'] = skill_context

            # SIF Factory — Camada 2: callables compiladas (shell, weather, web_search...).
            # LLM chama diretamente sem boilerplate: shell("cmd"), weather("SP")
            skill_loader.inject_sif_callables(eligible_skills, env.locals)

            # Inject skill_doc + skill_list REPL globals (Camada 3: on-demand lazy)
            _skill_doc_fn, _skill_list_fn = skill_loader.build_skill_doc_fn(eligible_skills)
            env.locals['skill_doc'] = _skill_doc_fn
            env.locals['skill_list'] = _skill_list_fn

            # Phase 9.2: Exec Approval Gate
            approval_gate: ExecApprovalGate = request.app.state.exec_approval
            approval_required: bool = request.app.state.exec_approval_required
            if approval_required:
                env.locals['confirm_exec'] = approval_gate.make_repl_fn(session.session_id)
            else:
                # Gate disponível mas não obrigatório — LLM pode chamar voluntariamente
                _gate_fn = approval_gate.make_repl_fn(session.session_id)
                env.locals['confirm_exec'] = _gate_fn

            env.locals.setdefault(PENDING_HANDOFFS_KEY, [])

            env.locals['request_handoff'] = make_handoff_fn(
                session_id=session.session_id,
                log_event=sm.log_event,
                hooks=hooks,
                telemetry=get_skill_telemetry(),
                client_id=client_id,
                state_sink=lambda payload: env.locals[PENDING_HANDOFFS_KEY].append(dict(payload)),
            )
            env.locals['handoff_roles'] = list(VALID_HANDOFF_ROLES)

    # Execute via Supervisor (in a thread to not block FastAPI)
    loop = asyncio.get_event_loop()
    hooks.trigger(
        "completion.started",
        session_id=session.session_id,
        context={"client_id": client_id},
    )
    try:
        # Pass the prompt (str or list) to the supervisor
        def _execute_with_skill_context() -> ExecutionResult:
            _skill_ctx_tokens = skill_loader.set_request_context(
                session_id=session.session_id,
                client_id=client_id,
                query=_query_text,
            )
            try:
                return supervisor.execute(session, prompt)
            finally:
                skill_loader.clear_request_context(_skill_ctx_tokens)

        result: ExecutionResult = await loop.run_in_executor(
            None,
            _execute_with_skill_context,
        )
    except Exception as e:
        hooks.trigger(
            "completion.aborted",
            session_id=session.session_id,
            context={"error": str(e)},
        )
        sm.log_event(session.session_id, "execution_error", {"error": str(e)})
        raise HTTPException(500, f"Execution failed: {e}")

    hooks.trigger(
        "completion.finished",
        session_id=session.session_id,
        context={
            "status": result.status,
            "execution_time": result.execution_time,
        },
    )

    if result.status == "completed" and session.rlm_instance is not None:
        env = getattr(session.rlm_instance, '_persistent_env', None)
        if env and hasattr(env, 'locals'):
            role_outcome = orchestrate_roles(
                rlm=session.rlm_instance,
                prompt=_query_text or str(prompt),
                response=result.response or "",
                prompt_plan=_prompt_plan,
                repl_locals=env.locals,
                log_event=sm.log_event,
                session_id=session.session_id,
                hooks=hooks,
            )
            result.response = role_outcome.response
            if role_outcome.steps:
                sm.log_event(session.session_id, "agent_role_summary", {
                    "steps": role_outcome.steps,
                    "escalated": role_outcome.escalated,
                    "retried": role_outcome.retried,
                })

    # Update session in DB
    sm.update_session(session)
    sm.log_event(session.session_id, "execution_complete", {
        "status": result.status,
        "execution_time": result.execution_time,
    })

    return {
        "status": result.status,
        "session_id": session.session_id,
        "response": result.response,
        "execution_time": round(result.execution_time, 2),
        "abort_reason": result.abort_reason,
        "error_detail": result.error_detail,
    }


# ---------------------------------------------------------------------------
# Session Endpoints
# ---------------------------------------------------------------------------

@app.get("/sessions")
async def list_sessions(request: Request, status: str | None = None):
    """List all sessions, optionally filtered by status."""
    sm: SessionManager = request.app.state.session_manager
    sessions = sm.list_sessions(status=status)
    return {
        "count": len(sessions),
        "sessions": [sm.session_to_dict(s) for s in sessions],
    }


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get details of a specific session."""
    sm: SessionManager = request.app.state.session_manager
    session = sm.get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id} not found")
    return sm.session_to_dict(session)


@app.delete("/sessions/{session_id}")
async def abort_session(session_id: str, request: Request):
    """Abort a running session or close an idle one."""
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
    sm: SessionManager = request.app.state.session_manager
    events = sm.get_events(session_id, limit=limit)
    return {"session_id": session_id, "events": events}


# ---------------------------------------------------------------------------
# Plugin & Route Endpoints
# ---------------------------------------------------------------------------

@app.get("/plugins")
async def list_plugins(request: Request):
    """List all available plugins."""
    loader: PluginLoader = request.app.state.plugin_loader
    manifests = loader.list_available()
    return {
        "count": len(manifests),
        "plugins": [loader.manifest_to_dict(m) for m in manifests],
    }


@app.get("/routes")
async def list_routes(request: Request):
    """List all configured event routes."""
    router: EventRouter = request.app.state.event_router
    return {"routes": router.list_routes()}


# ---------------------------------------------------------------------------
# Skills Endpoints
# ---------------------------------------------------------------------------

@app.get("/skills")
async def list_skills(request: Request):
    """Lista todas as skills descobertas e quais estão elegíveis no ambiente atual."""
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
    telemetry = get_skill_telemetry()
    safe_limit = max(1, min(limit, 100))
    summary = telemetry.get_summary(include_recent=include_recent, limit=safe_limit)
    summary["known_skills"] = len(request.app.state.skills_all)
    summary["eligible_skills"] = len(request.app.state.skills_eligible)
    return summary


@app.get("/skills/telemetry/{skill_name}")
async def get_skill_telemetry_report(skill_name: str, request: Request, limit: int = 20):
    """Retorna estatísticas e eventos recentes de uma skill específica."""
    all_skill_names = {skill.name for skill in request.app.state.skills_all}
    if skill_name not in all_skill_names:
        raise HTTPException(404, f"Skill '{skill_name}' não encontrada")
    telemetry = get_skill_telemetry()
    safe_limit = max(1, min(limit, 100))
    return telemetry.get_skill_report(skill_name, limit=safe_limit)


@app.get("/skills/telemetry/compose")
async def get_compose_telemetry(limit: int = 10):
    """Retorna as composições mais frequentes observadas entre skills."""
    telemetry = get_skill_telemetry()
    safe_limit = max(1, min(limit, 100))
    return telemetry.get_transition_report(limit=safe_limit)


@app.get("/skills/telemetry/session/{session_id}/compose")
async def get_session_compose_telemetry(session_id: str, limit: int = 10):
    """Retorna as transições observadas em uma sessão específica."""
    telemetry = get_skill_telemetry()
    safe_limit = max(1, min(limit, 100))
    return telemetry.get_transition_report(session_id=session_id, limit=safe_limit)


@app.get("/skills/telemetry/search")
async def search_skill_traces(query: str, skill_name: str = "", limit: int = 5):
    """Recupera traces relevantes por overlap lexical com a query atual."""
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
    hooks: HookSystem = request.app.state.hooks
    return hooks.get_stats()


# ---------------------------------------------------------------------------
# Exec Approval Endpoints
# ---------------------------------------------------------------------------

@app.get("/exec/pending")
async def list_pending_approvals(request: Request):
    """Lista execuções aguardando aprovação humana."""
    gate: ExecApprovalGate = request.app.state.exec_approval
    return {"pending": gate.list_pending(), "stats": gate.stats()}


@app.post("/exec/approve/{request_id}")
async def approve_exec(request_id: str, request: Request):
    """Aprova uma execução pendente. O REPL é desbloqueado imediatamente."""
    gate: ExecApprovalGate = request.app.state.exec_approval
    resolved_by = request.headers.get("X-Operator", "human")
    ok = gate.approve(request_id, resolved_by=resolved_by)
    if not ok:
        raise HTTPException(404, f"Pending approval '{request_id}' not found")
    return {"status": "approved", "id": request_id}


@app.post("/exec/deny/{request_id}")
async def deny_exec(request_id: str, request: Request):
    """Nega uma execução pendente. O REPL recebe PermissionError."""
    gate: ExecApprovalGate = request.app.state.exec_approval
    resolved_by = request.headers.get("X-Operator", "human")
    ok = gate.deny(request_id, resolved_by=resolved_by)
    if not ok:
        raise HTTPException(404, f"Pending approval '{request_id}' not found")
    return {"status": "denied", "id": request_id}


@app.get("/exec/{request_id}")
async def get_exec_record(request_id: str, request: Request):
    """Retorna o estado de uma solicitação de aprovação (pending ou resolved)."""
    gate: ExecApprovalGate = request.app.state.exec_approval
    record = gate.get_record(request_id)
    if not record:
        raise HTTPException(404, f"Approval record '{request_id}' not found")
    return record


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check(request: Request):
    """Health check with system status."""
    sm: SessionManager = request.app.state.session_manager
    supervisor: RLMSupervisor = request.app.state.supervisor
    loader: PluginLoader = request.app.state.plugin_loader

    active_sessions = sm.list_sessions(status="idle") + sm.list_sessions(status="running")
    running = supervisor.get_active_sessions()

    return {
        "status": "online",
        "engine": "RLM Automation Gateway v2.0",
        "active_sessions": len(active_sessions),
        "running_executions": len(running),
        "plugins_available": len(loader.list_available()),
        "model": os.environ.get("RLM_MODEL", "gpt-4o-mini"),
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
    uvicorn.run(
        "rlm.server.api:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    start_server()
