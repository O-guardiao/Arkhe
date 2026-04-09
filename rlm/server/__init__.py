"""RLM Server package — FastAPI HTTP server, runtime pipeline e observabilidade.

Pacote público mínimo: expõe apenas o event bus e o health monitor.
Para a app ASGI use ``rlm.server.api:app`` diretamente.
"""
from rlm.server.ws_server import RLMEventBus, start_ws_server, SSEStream
from rlm.server.health_monitor import HealthMonitor
from rlm.server.drain import DrainGuard

__all__ = [
    "RLMEventBus",
    "start_ws_server",
    "SSEStream",
    "HealthMonitor",
    "DrainGuard",
]
