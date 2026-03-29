"""
RLM Graceful Drain — Middleware e estado para shutdown sem perda de requests.

Problema real:
    api.py faz shutdown abrupto: scheduler.stop() → skill_loader.deactivate_all()
    → supervisor.shutdown() → session_manager.close_all(). Requests em voo
    são cortados sem aviso. Não há fase de "drain" onde novas requests são
    rejeitadas enquanto as ativas terminam.

Solução:
    DrainGuard mantém um contador atômico de requests ativos.
    DrainMiddleware rejeita novas requests com 503 durante drain.
    A sequência de shutdown em api.py passa a ser:
        1. drain_guard.start_draining() → rejeita novas requests
        2. drain_guard.wait_active(timeout=30) → espera as ativas
        3. cleanup normal (scheduler, skills, supervisor, sessions)

Design RLM-nativo:
    - threading.Lock + threading.Event — funciona sem asyncio
    - Middleware Starlette pura (sem dependências externas)
    - Emite ao RLMEventBus para dashboard WS
"""
from __future__ import annotations

import threading
import time
from typing import Any

from rlm.logging import get_runtime_logger

log = get_runtime_logger("drain")


class DrainGuard:
    """
    Controla a fase de drain do servidor.

    Mantém contagem de requests ativos e permite bloquear novas durante drain.
    """

    __slots__ = ("_active", "_draining", "_drain_event", "_lock", "_event_bus")

    def __init__(self, event_bus: Any | None = None) -> None:
        self._active = 0
        self._draining = False
        self._drain_event = threading.Event()
        self._lock = threading.Lock()
        self._event_bus = event_bus

    @property
    def is_draining(self) -> bool:
        return self._draining

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    def enter_request(self) -> bool:
        """
        Tenta registrar um novo request.
        Retorna False se estamos drenando (→ rejeitar com 503).
        """
        with self._lock:
            if self._draining:
                return False
            self._active += 1
            return True

    def exit_request(self) -> None:
        """Marca um request como finalizado."""
        with self._lock:
            self._active = max(0, self._active - 1)
            if self._draining and self._active == 0:
                self._drain_event.set()

    def start_draining(self) -> None:
        """Inicia fase de drain — rejeita novos requests."""
        with self._lock:
            if self._draining:
                return
            self._draining = True
            active = self._active
            if active == 0:
                self._drain_event.set()

        log.info("Drain iniciado", active_requests=active)
        if self._event_bus is not None:
            try:
                self._event_bus.emit("drain", {
                    "status": "started",
                    "active_requests": active,
                })
            except Exception:
                pass

    def wait_active(self, timeout: float = 30.0) -> bool:
        """
        Bloqueia até todos os requests ativos terminarem ou timeout.
        Retorna True se todos finalizaram, False se timeout.
        """
        ok = self._drain_event.wait(timeout=timeout)
        with self._lock:
            remaining = self._active
        if ok:
            log.info("Drain completo — todos requests finalizados")
        else:
            log.warn("Drain timeout", remaining_requests=remaining, timeout_s=timeout)
        return ok
