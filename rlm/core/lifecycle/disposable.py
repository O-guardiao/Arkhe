"""
RLM Disposable Infrastructure — Fase 10.1

Inspirado em: VS Code src/vs/base/common/lifecycle.ts (MIT, Microsoft)

Unifica resource cleanup com um contrato único: .dispose().
Substitui os 13+ métodos (close/cleanup/shutdown) espalhados pelo codebase.

DisposableStore coleta múltiplos disposables e libera todos juntos em
ordem reversa — garante que dependências são liberadas antes dos donos.

Uso:
    store = DisposableStore()
    store.add(rlm_instance)
    store.add(mcp_client)
    store.add(structured_log)
    # ... no shutdown:
    store.dispose()   # libera tudo, em ordem reversa, sem exceções

    # Ou como context manager:
    with DisposableStore() as store:
        store.add(resource1)
        store.add(resource2)
    # dispose() chamado automaticamente
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Protocol, runtime_checkable

from rlm.logging import get_runtime_logger


log = get_runtime_logger("disposable")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class IDisposable(Protocol):
    """Contrato mínimo: qualquer objeto que aloca recursos deve implementar dispose()."""
    def dispose(self) -> None: ...


# ---------------------------------------------------------------------------
# DisposableStore
# ---------------------------------------------------------------------------

class DisposableStore:
    """
    Coleta múltiplos IDisposable e os libera juntos.

    - Ordem reversa: último adicionado é o primeiro liberado
    - Exceções são capturadas — nunca impede a liberação dos demais
    - Thread-safe: pode ser usado de qualquer thread
    - Idempotente: chamar dispose() múltiplas vezes é seguro
    """

    __slots__ = ("_items", "_disposed", "_lock")

    def __init__(self) -> None:
        self._items: list[IDisposable] = []
        self._disposed = False
        self._lock = threading.Lock()

    @property
    def is_disposed(self) -> bool:
        return self._disposed

    def add(self, item: IDisposable) -> IDisposable:
        """
        Registra um disposable. Se o store já foi disposed, chama item.dispose() imediatamente.
        Retorna o próprio item para conveniência de encadeamento.
        """
        with self._lock:
            if self._disposed:
                # Store já fechou — descarta imediatamente
                try:
                    item.dispose()
                except Exception:
                    pass
                return item
            self._items.append(item)
        return item

    def remove(self, item: IDisposable) -> bool:
        """Remove um item sem dispose(). Retorna True se encontrado."""
        with self._lock:
            try:
                self._items.remove(item)
                return True
            except ValueError:
                return False

    def dispose(self) -> None:
        """Libera todos os items em ordem reversa. Idempotente."""
        with self._lock:
            if self._disposed:
                return
            self._disposed = True
            items = list(reversed(self._items))
            self._items.clear()

        errors: list[Exception] = []
        for item in items:
            try:
                item.dispose()
            except Exception as e:
                errors.append(e)

        # Erros são logados mas nunca propagados — dispose deve sempre completar
        if errors:
            for err in errors:
                log.warn("Error during dispose", error=str(err))

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __enter__(self) -> DisposableStore:
        return self

    def __exit__(self, *_: Any) -> bool:
        self.dispose()
        return False

    def __del__(self) -> None:
        if not self._disposed and self._items:
            log.warn(
                "DisposableStore GC'd with undisposed items — potential leak",
                undisposed_items=len(self._items),
            )


# ---------------------------------------------------------------------------
# Adaptadores: envolvem objetos legados (close/cleanup/shutdown) em IDisposable
# ---------------------------------------------------------------------------

class CallbackDisposable:
    """Disposable que chama uma função arbitrária no dispose()."""

    __slots__ = ("_callback", "_disposed")

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._disposed = False

    def dispose(self) -> None:
        if not self._disposed:
            self._disposed = True
            self._callback()


def to_disposable(callback: Callable[[], None]) -> IDisposable:
    """Cria um IDisposable a partir de uma função callback."""
    return CallbackDisposable(callback)


def adapt_closeable(obj: Any) -> IDisposable:
    """
    Adapta objetos que têm close()/cleanup()/shutdown() para IDisposable.
    Procura métodos na ordem: dispose → close → cleanup → shutdown.
    """
    if isinstance(obj, IDisposable):
        return obj
    for method_name in ("dispose", "close", "cleanup", "shutdown"):
        method = getattr(obj, method_name, None)
        if callable(method):
            def _invoke() -> None:
                method()
                return None

            return CallbackDisposable(_invoke)
    raise TypeError(
        f"{type(obj).__name__} does not have dispose/close/cleanup/shutdown method"
    )
