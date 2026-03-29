"""
RLM Gateway State Machine — Ciclo de vida formal para gateways.

Problema real:
    O Telegram gateway usa um booleano `self._running` e um contador
    `self._error_count` para controlar estado. Não há como saber se
    o gateway está reconectando, drenando, ou parado por erro. O dashboard
    WS não recebe info de estado.

Solução:
    GatewayStateMachine formaliza as transições de estado com validação
    e emissão de eventos ao RLMEventBus para observabilidade em tempo real.

Estados:
    IDLE → CONNECTING → RUNNING → DRAINING → STOPPED
                ↓                    ↑
            RECONNECTING ───────────┘
                ↓
            ERROR → STOPPED

Design RLM-nativo:
    - Thread-safe (threading.Lock)
    - Emite ao RLMEventBus se disponível
    - IDisposable — transition para STOPPED no dispose
    - Validação de transições (não permite saltar estados)
"""
from __future__ import annotations

import enum
import threading
import time
from typing import Any, Callable

from rlm.logging import get_runtime_logger

log = get_runtime_logger("gateway_state")


class GatewayState(enum.Enum):
    """Estados possíveis de um gateway."""
    IDLE = "idle"
    CONNECTING = "connecting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    DRAINING = "draining"
    ERROR = "error"
    STOPPED = "stopped"


# Transições válidas: estado_atual → {estados_destino_permitidos}
_VALID_TRANSITIONS: dict[GatewayState, frozenset[GatewayState]] = {
    GatewayState.IDLE:          frozenset({GatewayState.CONNECTING, GatewayState.STOPPED}),
    GatewayState.CONNECTING:    frozenset({GatewayState.RUNNING, GatewayState.ERROR, GatewayState.STOPPED}),
    GatewayState.RUNNING:       frozenset({GatewayState.DRAINING, GatewayState.RECONNECTING, GatewayState.ERROR, GatewayState.STOPPED}),
    GatewayState.RECONNECTING:  frozenset({GatewayState.CONNECTING, GatewayState.ERROR, GatewayState.STOPPED}),
    GatewayState.DRAINING:      frozenset({GatewayState.STOPPED}),
    GatewayState.ERROR:         frozenset({GatewayState.RECONNECTING, GatewayState.STOPPED}),
    GatewayState.STOPPED:       frozenset(),  # terminal
}


class GatewayStateMachine:
    """
    Máquina de estados formal para gateways RLM.

    Uso:
        sm = GatewayStateMachine("telegram", event_bus=bus)
        sm.transition(GatewayState.CONNECTING)
        sm.transition(GatewayState.RUNNING)
        # ... no erro:
        sm.transition(GatewayState.ERROR, reason="Connection timeout")
        sm.transition(GatewayState.RECONNECTING)
    """

    __slots__ = (
        "_name", "_state", "_lock", "_event_bus",
        "_listeners", "_last_change", "_disposed",
    )

    def __init__(
        self,
        name: str,
        event_bus: Any | None = None,
        initial: GatewayState = GatewayState.IDLE,
    ) -> None:
        self._name = name
        self._state = initial
        self._lock = threading.Lock()
        self._event_bus = event_bus  # RLMEventBus — duck-typed
        self._listeners: list[Callable[[GatewayState, GatewayState, str], None]] = []
        self._last_change = time.monotonic()
        self._disposed = False

    @property
    def state(self) -> GatewayState:
        return self._state

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_running(self) -> bool:
        """Atalho para checar se o gateway está operacional."""
        return self._state in (GatewayState.RUNNING, GatewayState.CONNECTING)

    @property
    def is_terminal(self) -> bool:
        return self._state == GatewayState.STOPPED

    def time_in_state(self) -> float:
        """Segundos desde a última transição."""
        return time.monotonic() - self._last_change

    # ── Transição ────────────────────────────────────────────────────────

    def transition(
        self,
        target: GatewayState,
        reason: str = "",
    ) -> bool:
        """
        Transiciona para o estado alvo.

        Retorna True se a transição foi feita, False se inválida.
        Emite evento ao RLMEventBus se disponível.
        """
        with self._lock:
            if self._disposed and target != GatewayState.STOPPED:
                return False

            old = self._state
            if old == target:
                return True  # já no estado alvo — idempotente

            valid_targets = _VALID_TRANSITIONS.get(old, frozenset())
            if target not in valid_targets:
                log.warn(
                    "Transição de estado inválida",
                    gateway=self._name,
                    from_state=old.value,
                    to_state=target.value,
                    valid_targets=[s.value for s in valid_targets],
                )
                return False

            self._state = target
            self._last_change = time.monotonic()

        # Fora do lock — emitir eventos e notificar listeners
        log.info(
            "Gateway state change",
            gateway=self._name,
            from_state=old.value,
            to_state=target.value,
            reason=reason,
        )

        # Emitir ao EventBus para dashboard WS
        if self._event_bus is not None:
            try:
                self._event_bus.emit("gateway_state", {
                    "gateway": self._name,
                    "from": old.value,
                    "to": target.value,
                    "reason": reason,
                })
            except Exception:
                pass  # observer failure nunca deve crashar gateway

        # Notificar listeners locais
        for fn in self._listeners:
            try:
                fn(old, target, reason)
            except Exception:
                pass

        return True

    # ── Listeners ────────────────────────────────────────────────────────

    def on_change(
        self,
        callback: Callable[[GatewayState, GatewayState, str], None],
    ) -> None:
        """Registra callback chamado em cada transição: fn(old, new, reason)."""
        self._listeners.append(callback)

    # ── IDisposable ──────────────────────────────────────────────────────

    def dispose(self) -> None:
        """Move para STOPPED e limpa listeners."""
        if self._disposed:
            return
        self._disposed = True
        self.transition(GatewayState.STOPPED, reason="dispose")
        self._listeners.clear()

    # ── Debug ────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Snapshot serializável para /health e dashboard."""
        return {
            "gateway": self._name,
            "state": self._state.value,
            "time_in_state_s": round(self.time_in_state(), 1),
        }
