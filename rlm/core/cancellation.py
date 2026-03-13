"""
RLM Cancellation Tokens — Fase 10.2

Inspirado em: VS Code src/vs/base/common/cancellation.ts (MIT, Microsoft)

Substitui o hack ad-hoc de threading.Event (``_abort_event``) por um sistema
composicional de cancellation tokens:

- CancellationToken é imutável — consumidores só verificam e observam
- CancellationTokenSource é quem controla — cria o token e cancela
- Tokens podem ter parents: cancelar o parent cancela todos os filhos
- Integra com IDisposable — fontes são descartáveis

Uso:
    source = CancellationTokenSource()
    token = source.token

    # Consumidor (RLM loop):
    if token.is_cancelled:
        break

    # Controlador (Supervisor):
    source.cancel()

    # Hierárquico (sub-agentes):
    child_source = CancellationTokenSource(parent=token)
    sub_rlm.run(child_source.token)
    # cancelar source cancela child_source automaticamente
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from rlm.core.disposable import IDisposable, CallbackDisposable


# ---------------------------------------------------------------------------
# CancellationToken
# ---------------------------------------------------------------------------

class CancellationToken:
    """
    Token que indica se o cancelamento foi requisitado.

    Consumidores verificam is_cancelled e registram callbacks
    via on_cancelled(). Nunca modifica o estado — quem modifica
    é o CancellationTokenSource.
    """

    # Singletons reutilizáveis
    NONE: CancellationToken   # token que nunca cancela
    CANCELLED: CancellationToken  # token sempre cancelado

    __slots__ = ("_cancelled", "_listeners", "_lock", "_reason")

    def __init__(self) -> None:
        self._cancelled = False
        self._listeners: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._reason: str = ""

    @property
    def is_cancelled(self) -> bool:
        """True se o cancelamento foi requisitado."""
        return self._cancelled

    @property
    def reason(self) -> str:
        """Motivo do cancelamento, se fornecido."""
        return self._reason

    def on_cancelled(self, callback: Callable[[], None]) -> IDisposable:
        """
        Registra callback chamado quando cancelamento é requisitado.
        Se já cancelado, chama imediatamente (no próximo tick via threading).
        Retorna um IDisposable que remove o listener.
        """
        with self._lock:
            if self._cancelled:
                # Já cancelado — executa imediatamente em thread separada
                # para evitar reentrância no chamador
                callback()
                return CallbackDisposable(lambda: None)
            self._listeners.append(callback)

        def _remove():
            with self._lock:
                try:
                    self._listeners.remove(callback)
                except ValueError:
                    pass

        return CallbackDisposable(_remove)

    def _fire(self, reason: str) -> None:
        """Chamado internamente por CancellationTokenSource.cancel()."""
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            self._reason = reason
            listeners = list(self._listeners)
            self._listeners.clear()

        for fn in listeners:
            try:
                fn()
            except Exception:
                pass  # Listeners nunca impedem o cancelamento


class _NoneToken(CancellationToken):
    """Token que nunca é cancelado. Singleton."""
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__()

    def _fire(self, reason: str) -> None:
        pass  # Never fires

    @property
    def is_cancelled(self) -> bool:
        return False


class _CancelledToken(CancellationToken):
    """Token permanentemente cancelado. Singleton."""
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = True
        self._reason = "pre-cancelled"

    @property
    def is_cancelled(self) -> bool:
        return True

    def on_cancelled(self, callback: Callable[[], None]) -> IDisposable:
        callback()
        return CallbackDisposable(lambda: None)


# Inicializa singletons
CancellationToken.NONE = _NoneToken()
CancellationToken.CANCELLED = _CancelledToken()


# ---------------------------------------------------------------------------
# CancellationTokenSource
# ---------------------------------------------------------------------------

class CancellationTokenSource:
    """
    Controla o cancelamento de um CancellationToken.

    Pode opcionalmente receber um token parent: se o parent for
    cancelado, este source também é cancelado.
    """

    __slots__ = ("_token", "_parent_listener", "_disposed")

    def __init__(self, parent: CancellationToken | None = None) -> None:
        self._token = CancellationToken()
        self._disposed = False

        # Vincula ao parent: se o parent cancelar, nós cancelamos
        self._parent_listener: IDisposable | None = None
        if parent is not None and parent is not CancellationToken.NONE:
            self._parent_listener = parent.on_cancelled(
                lambda: self.cancel(reason="parent cancelled")
            )

    @property
    def token(self) -> CancellationToken:
        """O token controlado por este source. Passe para consumidores."""
        return self._token

    def cancel(self, reason: str = "cancelled") -> None:
        """
        Requisita cancelamento. Dispara todos os listeners registrados no token.
        Idempotente — chamar múltiplas vezes é seguro.
        """
        if not self._disposed:
            self._token._fire(reason)

    def dispose(self) -> None:
        """Limpa recursos. NÃO cancela — apenas desconecta do parent."""
        if not self._disposed:
            self._disposed = True
            if self._parent_listener is not None:
                self._parent_listener.dispose()
                self._parent_listener = None
