"""RLM Server package — FastAPI HTTP server, runtime pipeline e observabilidade.

Pacote público mínimo: expõe o event bus, health monitor e objetos de apoio.
Para a app ASGI use ``rlm.server.api:app`` diretamente.

Módulos internos (não re-exportados aqui para evitar imports circulares
e dependências opcionais):
    api               — FastAPI app + lifespan (depende de fastapi, uvicorn)
    backpressure      — SyncGate / AsyncGate / ConcurrencyExceeded
    brain_api / brain_router — extensão BrainOS
    dedup             — MessageDedup (deduplicação de webhooks)
    event_router      — EventRouter / EventRoute (roteamento de fontes)
    openai_compat     — router /v1/chat/completions
    runtime_pipeline  — RuntimeDispatchServices / RuntimeDispatchRejected
    scheduler         — RLMScheduler / CronJob (agendamento)
"""
from rlm.server.ws_server import RLMEvent, RLMEventBus, start_ws_server, SSEStream
from rlm.server.health_monitor import HealthMonitor
from rlm.server.drain import DrainGuard

__all__ = [
    # Observability — event bus
    "RLMEvent",
    "RLMEventBus",
    "start_ws_server",
    "SSEStream",
    # Health monitoring
    "HealthMonitor",
    # Graceful shutdown
    "DrainGuard",
]
