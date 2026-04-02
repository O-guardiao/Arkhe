"""
RLM Heartbeat — Feedback visual generalizado para todos os canais.

Problema real:
    O Telegram gateway já tem um padrão de "keep typing" (thread daemon
    que envia sendChatAction a cada 4s). Mas esse padrão está hardcoded
    dentro de process_and_reply(). WhatsApp, Discord e Slack não têm
    feedback nenhum durante processamento.

Solução:
    Heartbeat generaliza o padrão typing thread do Telegram:
    - Aceita qualquer callable como "ação de heartbeat"
    - Funciona em sync (threading.Event) e async (asyncio.Event)
    - IDisposable — para automaticamente no dispose()
    - CancellationToken — para se o processamento for cancelado

Design RLM-nativo:
    - SyncHeartbeat: thread daemon (Telegram, chamadas sync)
    - AsyncHeartbeat: asyncio.Task (FastAPI gateways)
    - Ambos implementam IDisposable para integração com DisposableStore

Uso:
    # Sync (Telegram)
    hb = SyncHeartbeat(
        action=lambda: _send_typing(token, chat_id),
        interval_s=4.0,
    )
    hb.start()
    try:
        response = rlm.completion(text)
    finally:
        hb.dispose()

    # Async (WhatsApp/Discord/Slack)
    async with AsyncHeartbeat(
        action=lambda: send_typing_indicator(wa_id),
        interval_s=5.0,
    ) as hb:
        response = await run_rlm(text)
"""
from __future__ import annotations

import asyncio
import threading
from typing import Callable, Any

from rlm.core.lifecycle.cancellation import CancellationToken
from rlm.logging import get_runtime_logger

log = get_runtime_logger("heartbeat")


class SyncHeartbeat:
    """
    Heartbeat síncrono via thread daemon.

    Chama `action()` a cada `interval_s` segundos até stop()/dispose().
    Usado pelo Telegram gateway e qualquer processamento em thread.
    """

    __slots__ = (
        "_action", "_interval", "_cancel_token",
        "_stop_event", "_thread", "_disposed", "_unsub",
    )

    def __init__(
        self,
        action: Callable[[], Any],
        interval_s: float = 4.0,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        self._action = action
        self._interval = interval_s
        self._cancel_token = cancel_token
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._disposed = False
        self._unsub: Any = None

    def start(self) -> None:
        """Inicia a thread de heartbeat. Idempotente."""
        if self._thread is not None or self._disposed:
            return

        # Se já cancelado, nem começa
        if self._cancel_token is not None and self._cancel_token.is_cancelled:
            return

        # Registrar callback de cancelamento
        if self._cancel_token is not None:
            self._unsub = self._cancel_token.on_cancelled(self._stop_event.set)

        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="rlm-heartbeat",
        )
        self._thread.start()

    def stop(self) -> None:
        """Para o heartbeat. Thread-safe."""
        self._stop_event.set()

    def dispose(self) -> None:
        """IDisposable — para e limpa."""
        if self._disposed:
            return
        self._disposed = True
        self._stop_event.set()
        if self._unsub is not None:
            self._unsub.dispose()
            self._unsub = None
        # Não faz join() — é daemon thread, não bloqueia shutdown

    def _loop(self) -> None:
        """Loop interno da thread."""
        while not self._stop_event.is_set():
            try:
                self._action()
            except Exception as exc:
                log.debug("Heartbeat action falhou", error=str(exc))
            self._stop_event.wait(timeout=self._interval)


class AsyncHeartbeat:
    """
    Heartbeat assíncrono via asyncio.Task.

    Chama `action()` a cada `interval_s` segundos.
    Pode ser sync callable (chamado inline) ou async callable (awaited).

    Uso como context manager:
        async with AsyncHeartbeat(action=fn, interval_s=5.0) as hb:
            await do_work()
    """

    __slots__ = (
        "_action", "_interval", "_cancel_token",
        "_task", "_disposed", "_unsub", "_stopped",
    )

    def __init__(
        self,
        action: Callable[[], Any],
        interval_s: float = 5.0,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        self._action = action
        self._interval = interval_s
        self._cancel_token = cancel_token
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._disposed = False
        self._unsub: Any = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        """Inicia o heartbeat como asyncio.Task."""
        if self._task is not None or self._disposed:
            return
        if self._cancel_token is not None and self._cancel_token.is_cancelled:
            return

        # Cancelamento cross-thread via CancellationToken
        if self._cancel_token is not None:
            loop = asyncio.get_running_loop()

            def _on_cancel():
                loop.call_soon_threadsafe(self._stopped.set)

            self._unsub = self._cancel_token.on_cancelled(_on_cancel)

        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        """Para o heartbeat."""
        self._stopped.set()

    def dispose(self) -> None:
        """IDisposable — cancela task e limpa."""
        if self._disposed:
            return
        self._disposed = True
        self._stopped.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self._unsub is not None:
            self._unsub.dispose()
            self._unsub = None

    async def __aenter__(self) -> AsyncHeartbeat:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.dispose()

    async def _loop(self) -> None:
        """Loop interno da task."""
        while not self._stopped.is_set():
            try:
                result = self._action()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                log.debug("Async heartbeat action falhou", error=str(exc))
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self._interval)
                break  # stopped foi sinalizado
            except asyncio.TimeoutError:
                pass  # timeout = continuar loop
