"""
RLM Backpressure — Controle de concorrência para proteger o agente.

Problema real:
    Se 100 webhooks chegam simultaneamente, o RLM cria 100 threads/tasks
    chamando o LLM ao mesmo tempo. Cada chamada consome ~5-30s de API
    e tokens. Sem controle, isso causa:
    - Rate limit do backend (429 do OpenAI/Anthropic)
    - OOM no VPS (cada RLM instance usa memória para contexto)
    - Degradação geral — todos os requests ficam lentos

Solução:
    ConcurrencyGate limita quantos requests podem processar simultaneamente.
    Requests excedentes esperam (com timeout) ou são rejeitados com 429.

Design RLM-nativo:
    - SyncGate: threading.Semaphore para Telegram e processamento em thread
    - AsyncGate: asyncio.Semaphore para FastAPI gateways
    - IDisposable — integra com DisposableStore
    - Emite métricas ao RLMEventBus (queue depth, wait time)

Uso:
    gate = AsyncGate(max_concurrent=5)
    async with gate.acquire(timeout=30):
        result = await process_request()
    # Se max_concurrent atingido, espera até 30s
    # Se timeout, levanta ConcurrencyExceeded
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from typing import Any

from rlm.logging import get_runtime_logger

log = get_runtime_logger("backpressure")


class ConcurrencyExceeded(Exception):
    """Levantada quando timeout expira esperando slot de concorrência."""
    pass


class SyncGate:
    """
    Gate de concorrência para contexto sync (Telegram, threads).

    Usa threading.Semaphore com contadores para métricas.
    """

    __slots__ = ("_sem", "_max", "_waiting", "_active", "_lock", "_disposed")

    def __init__(self, max_concurrent: int = 10) -> None:
        self._sem = threading.Semaphore(max_concurrent)
        self._max = max_concurrent
        self._waiting = 0
        self._active = 0
        self._lock = threading.Lock()
        self._disposed = False

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    @property
    def waiting(self) -> int:
        with self._lock:
            return self._waiting

    @contextlib.contextmanager
    def acquire(self, timeout: float = 30.0):
        """
        Context manager que adquire slot.
        Bloqueia até slot disponível ou timeout.

        Uso:
            with gate.acquire(timeout=30):
                process()
        """
        with self._lock:
            self._waiting += 1

        acquired = self._sem.acquire(timeout=timeout)

        with self._lock:
            self._waiting -= 1
            if not acquired:
                raise ConcurrencyExceeded(
                    f"Timeout {timeout}s esperando slot "
                    f"(max_concurrent={self._max}, active={self._active})"
                )
            self._active += 1

        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._sem.release()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "max_concurrent": self._max,
                "active": self._active,
                "waiting": self._waiting,
            }


class AsyncGate:
    """
    Gate de concorrência para contexto async (FastAPI).

    Usa asyncio.Semaphore com contadores para métricas.
    """

    __slots__ = ("_sem", "_max", "_waiting", "_active", "_lock", "_disposed")

    def __init__(self, max_concurrent: int = 10) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._max = max_concurrent
        self._waiting = 0
        self._active = 0
        self._lock = threading.Lock()  # contadores acessados cross-thread
        self._disposed = False

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    @property
    def waiting(self) -> int:
        with self._lock:
            return self._waiting

    @contextlib.asynccontextmanager
    async def acquire(self, timeout: float = 30.0):
        """
        Async context manager que adquire slot.

        Uso:
            async with gate.acquire(timeout=30):
                await process()
        """
        with self._lock:
            self._waiting += 1

        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            with self._lock:
                self._waiting -= 1
            raise ConcurrencyExceeded(
                f"Timeout {timeout}s esperando slot async "
                f"(max_concurrent={self._max}, active={self._active})"
            )

        with self._lock:
            self._waiting -= 1
            self._active += 1

        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._sem.release()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "max_concurrent": self._max,
                "active": self._active,
                "waiting": self._waiting,
            }
