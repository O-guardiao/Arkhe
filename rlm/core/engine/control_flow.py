"""
RLM Control Flow Primitives — Fase 10.3

Inspirado em: VS Code src/vs/base/common/controlFlow.ts + async.ts (MIT, Microsoft)

Primitivas de controle de fluxo para o RLM:
- ReentrancyBarrier: impede reentrância em seções críticas
- AsyncLimiter: limita chamadas assíncronas concorrentes (rate limiting LLM)
- Throttler: coalesce múltiplas chamadas em uma só
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# ReentrancyBarrier
# ---------------------------------------------------------------------------

class ReentrancyBarrier:
    """
    Impede código de reentrar seções críticas.

    Uso:
        barrier = ReentrancyBarrier()

        def on_state_changed():
            def _work():
                fire_hooks()  # hooks podem chamar on_state_changed novamente
            barrier.run_or_skip(_work)  # chamada reentrante é ignorada

        def on_critical():
            def _work():
                save_state()
            barrier.run_or_throw(_work)  # reentrância dispara erro (bug no código)
    """

    __slots__ = ("_occupied",)

    def __init__(self) -> None:
        self._occupied = False

    @property
    def is_occupied(self) -> bool:
        """True se algum runner está executando dentro da barreira."""
        return self._occupied

    def run_or_skip(self, runner: Callable[[], T]) -> T | None:
        """
        Executa runner se a barreira não está ocupada.
        Se ocupada (reentrância), retorna None silenciosamente.
        """
        if self._occupied:
            return None
        self._occupied = True
        try:
            return runner()
        finally:
            self._occupied = False

    def run_or_throw(self, runner: Callable[[], T]) -> T:
        """
        Executa runner. Se a barreira está ocupada, levanta RuntimeError.
        Use para detectar bugs — reentrância neste contexto é sempre um erro.
        """
        if self._occupied:
            raise RuntimeError("ReentrancyBarrier: chamada reentrante detectada — isto é um bug")
        self._occupied = True
        try:
            return runner()
        finally:
            self._occupied = False


# ---------------------------------------------------------------------------
# AsyncLimiter
# ---------------------------------------------------------------------------

class AsyncLimiter:
    """
    Limita a concorrência de operações assíncronas.

    Inspirado no VS Code Limiter<T>. Garante que no máximo N coroutines
    executam em paralelo. Excedentes ficam em fila.

    Uso:
        limiter = AsyncLimiter(max_concurrent=5)

        async def call_llm(prompt):
            return await limiter.queue(client.acompletion(prompt))

    Para uso síncrono (ThreadPoolExecutor), use SyncLimiter.
    """

    __slots__ = ("_semaphore", "_size", "_max", "_disposed")

    def __init__(self, max_concurrent: int = 5) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._max = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._size = 0
        self._disposed = False

    @property
    def size(self) -> int:
        """Número de operações em fila + executando."""
        return self._size

    @property
    def available(self) -> int:
        """Número de slots disponíveis."""
        return max(0, self._max - self._size)

    async def queue(self, coro: Awaitable[T]) -> T:
        """
        Enfileira uma coroutine. Bloqueia (await) se o limite está atingido.
        Executa assim que um slot abrir.
        """
        if self._disposed:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise RuntimeError("AsyncLimiter has been disposed")
        self._size += 1
        try:
            async with self._semaphore:
                return await coro
        finally:
            self._size -= 1

    def dispose(self) -> None:
        self._disposed = True


# ---------------------------------------------------------------------------
# SyncLimiter
# ---------------------------------------------------------------------------

class SyncLimiter:
    """
    Limita a concorrência de operações síncronas via threading.Semaphore.

    Uso no LMHandler para rate-limiting chamadas LLM síncronas:
        limiter = SyncLimiter(max_concurrent=5)

        def call_llm(prompt):
            with limiter:
                return client.completion(prompt)
    """

    __slots__ = ("_semaphore", "_max", "_active", "_lock")

    def __init__(self, max_concurrent: int = 5) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._max = max_concurrent
        self._semaphore = threading.Semaphore(max_concurrent)
        self._active = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> int:
        """Número de operações atualmente em execução."""
        with self._lock:
            return self._active

    def __enter__(self) -> SyncLimiter:
        self._semaphore.acquire()
        with self._lock:
            self._active += 1
        return self

    def __exit__(self, *_: Any) -> bool:
        with self._lock:
            self._active -= 1
        self._semaphore.release()
        return False

    def dispose(self) -> None:
        pass  # Semaphore não precisa limpeza explícita


# ---------------------------------------------------------------------------
# Throttler
# ---------------------------------------------------------------------------

class Throttler:
    """
    Coalesce múltiplas chamadas síncronas durante uma execução.

    Se N chamadas chegam enquanto uma execução está ativa, apenas
    1 execução adicional é feita quando a primeira terminar.
    Útil para rate-limitar saves, compactações, e syncs.

    Uso:
        throttler = Throttler()

        def save():
            data = collect_data()
            write_to_disk(data)

        # Mesmo chamado 100x em 1 segundo, save() roda no máx 2 vezes
        throttler.queue(save)
    """

    __slots__ = ("_lock", "_active", "_pending", "_disposed")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._pending: Callable[[], Any] | None = None
        self._disposed = False

    def queue(self, factory: Callable[[], T]) -> T | None:
        """
        Enfileira uma execução. Se já há uma ativa, armazena como pending.
        Retorna o resultado da execução ou None se foi coalescido.
        """
        if self._disposed:
            return None

        with self._lock:
            if self._active:
                self._pending = factory
                return None
            self._active = True

        try:
            result = factory()
        finally:
            with self._lock:
                self._active = False
                pending = self._pending
                self._pending = None

            if pending is not None:
                return self.queue(pending)

        return result

    def dispose(self) -> None:
        self._disposed = True
        with self._lock:
            self._pending = None
