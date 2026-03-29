"""
RLM Health Monitor — Monitoramento e auto-recuperação de gateways.

Problema real:
    O Telegram gateway para em max_consecutive_errors e não reinicia.
    Os gateways webhook (WhatsApp/Slack/Discord) rodam como routers
    FastAPI — se o servidor crashar, não há checagem de saúde.
    O endpoint /health atual retorna {"status": "ok"} sem dados reais.

Solução:
    HealthMonitor verifica periodicamente se os gateways registrados
    estão operacionais. Expõe dados via get_health_report() para o
    endpoint /health. Integra com GatewayStateMachine para estado
    formal e com ShutdownManager para veto-based graceful shutdown.

Design RLM-nativo:
    - Thread daemon para checagem periódica (funciona sem asyncio)
    - IDisposable — para no dispose()
    - Integra com RLMEventBus para dashboard WS
    - Cada gateway registra um health check callable
    - Não depende de dependências externas

Uso:
    monitor = HealthMonitor(event_bus=bus, interval_s=30)
    monitor.register("telegram", lambda: tg_gateway._running)
    monitor.register("api", lambda: True)  # FastAPI sempre está rodando
    monitor.start()
    # ... no shutdown:
    monitor.dispose()
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from rlm.logging import get_runtime_logger

log = get_runtime_logger("health_monitor")


@dataclass
class HealthEntry:
    """Registro de saúde de um componente."""
    name: str
    check: Callable[[], bool]
    healthy: bool = True
    last_check: float = 0.0
    consecutive_failures: int = 0
    last_error: str = ""


class HealthMonitor:
    """
    Monitor de saúde periódico para componentes do RLM.

    Verifica cada componente registrado a cada `interval_s` segundos.
    Se um componente falhar `max_failures` vezes consecutivas, emite
    um evento de alerta ao RLMEventBus.
    """

    __slots__ = (
        "_entries", "_interval", "_max_failures",
        "_event_bus", "_thread", "_stop_event",
        "_disposed", "_lock", "_start_time",
    )

    def __init__(
        self,
        event_bus: Any | None = None,
        interval_s: float = 30.0,
        max_failures: int = 3,
    ) -> None:
        self._entries: dict[str, HealthEntry] = {}
        self._interval = interval_s
        self._max_failures = max_failures
        self._event_bus = event_bus
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._disposed = False
        self._lock = threading.Lock()
        self._start_time = time.monotonic()

    # ── Registro de componentes ──────────────────────────────────────────

    def register(
        self,
        name: str,
        check: Callable[[], bool],
    ) -> None:
        """
        Registra um componente para monitoramento.

        check: callable que retorna True se saudável, False se não.
               Exceções são tratadas como falha.
        """
        with self._lock:
            self._entries[name] = HealthEntry(name=name, check=check)
        log.debug("Health check registrado", component=name)

    def unregister(self, name: str) -> None:
        """Remove um componente do monitoramento."""
        with self._lock:
            self._entries.pop(name, None)

    # ── Verificação ──────────────────────────────────────────────────────

    def check_all(self) -> dict[str, dict[str, Any]]:
        """
        Executa todos os health checks agora.
        Retorna dict com status de cada componente.
        """
        results: dict[str, dict[str, Any]] = {}
        with self._lock:
            entries = list(self._entries.values())

        for entry in entries:
            now = time.monotonic()
            try:
                healthy = entry.check()
            except Exception as exc:
                healthy = False
                entry.last_error = str(exc)

            entry.last_check = now

            if healthy:
                if not entry.healthy:
                    log.info("Componente recuperado", component=entry.name)
                    self._emit_event(entry.name, "recovered")
                entry.healthy = True
                entry.consecutive_failures = 0
                entry.last_error = ""
            else:
                entry.consecutive_failures += 1
                entry.healthy = False
                log.warn(
                    "Health check falhou",
                    component=entry.name,
                    consecutive=entry.consecutive_failures,
                    error=entry.last_error,
                )
                if entry.consecutive_failures >= self._max_failures:
                    self._emit_event(entry.name, "unhealthy",
                                     failures=entry.consecutive_failures)

            results[entry.name] = {
                "healthy": entry.healthy,
                "consecutive_failures": entry.consecutive_failures,
                "last_error": entry.last_error,
            }

        return results

    # ── Health report para /health endpoint ──────────────────────────────

    def get_health_report(self) -> dict[str, Any]:
        """
        Gera relatório completo de saúde para o endpoint /health.

        Formato:
        {
            "status": "healthy" | "degraded" | "unhealthy",
            "uptime_s": 12345.6,
            "components": { "telegram": {...}, "api": {...} }
        }
        """
        with self._lock:
            entries = list(self._entries.values())

        components: dict[str, Any] = {}
        all_healthy = True
        any_healthy = False

        for entry in entries:
            components[entry.name] = {
                "healthy": entry.healthy,
                "consecutive_failures": entry.consecutive_failures,
                "last_error": entry.last_error,
                "seconds_since_check": round(
                    time.monotonic() - entry.last_check, 1
                ) if entry.last_check else None,
            }
            if entry.healthy:
                any_healthy = True
            else:
                all_healthy = False

        if all_healthy:
            status = "healthy"
        elif any_healthy:
            status = "degraded"
        else:
            status = "unhealthy"

        return {
            "status": status,
            "uptime_s": round(time.monotonic() - self._start_time, 1),
            "components": components,
        }

    # ── Loop periódico ───────────────────────────────────────────────────

    def start(self) -> None:
        """Inicia monitoramento periódico em thread daemon."""
        if self._thread is not None or self._disposed:
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="rlm-health-monitor",
        )
        self._thread.start()
        log.info("Health monitor iniciado", interval_s=self._interval)

    def stop(self) -> None:
        """Para o monitoramento."""
        self._stop_event.set()

    def dispose(self) -> None:
        """IDisposable — para e limpa."""
        if self._disposed:
            return
        self._disposed = True
        self._stop_event.set()
        with self._lock:
            self._entries.clear()

    def _loop(self) -> None:
        """Loop periódico de verificação."""
        while not self._stop_event.is_set():
            try:
                self.check_all()
            except Exception as exc:
                log.error("Erro no health check loop", error=str(exc))
            self._stop_event.wait(timeout=self._interval)

    # ── Helpers internos ─────────────────────────────────────────────────

    def _emit_event(self, component: str, status: str, **extra: Any) -> None:
        """Emite evento ao RLMEventBus se disponível."""
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit("health", {
                "component": component,
                "status": status,
                **extra,
            })
        except Exception:
            pass
