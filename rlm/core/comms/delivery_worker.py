"""
DeliveryWorker — Loop assíncrono que drena o Outbox e entrega via adapters.

Segue o pattern de ``AsyncHeartbeat`` (rlm/server/heartbeat.py):
  asyncio.create_task + _stop_event + start()/stop()

Integra com:
  - ``OutboxStore``: lê batches pendentes
  - ``ChannelRegistry``: despacha para adapters registrados
  - ``RLMEventBus`` (opcional): emite eventos de observabilidade (síncrono)
  - ``DrainGuard`` (futuro): respeita shutdown graceful

O worker roda no event loop do FastAPI (lifespan).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from rlm.core.comms.outbox import OutboxStore
from rlm.core.structured_log import get_logger

_log = get_logger("delivery_worker")


class DeliveryWorker:
    """
    Asyncio task de longa vida que drena o outbox em batches.

    Lifecycle:
        worker = DeliveryWorker(outbox, channel_registry, event_bus)
        await worker.start()   # cria asyncio.Task
        ...
        worker.stop()          # sinaliza parada
        # task termina na próxima iteração
    """

    POLL_INTERVAL: float = 0.5
    BATCH_SIZE: int = 20

    def __init__(
        self,
        outbox: OutboxStore,
        channel_registry: Any,
        event_bus: Any | None = None,
    ) -> None:
        self.outbox = outbox
        self.registry = channel_registry
        self.event_bus = event_bus
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        """Inicia o loop de entrega como asyncio.Task."""
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="delivery-worker")
        _log.info("DeliveryWorker started")

    def stop(self) -> None:
        """Sinaliza parada. O task termina na próxima iteração."""
        self._stopped.set()
        _log.info("DeliveryWorker stop requested")

    def is_alive(self) -> bool:
        """Health check — True se o task está rodando."""
        return self._task is not None and not self._task.done()

    async def _loop(self) -> None:
        """Loop principal: poll → drain → sleep."""
        while not self._stopped.is_set():
            try:
                delivered = await self._drain_batch()
                if not delivered:
                    # Nada pendente — espera antes de pollar de novo
                    try:
                        await asyncio.wait_for(
                            self._stopped.wait(),
                            timeout=self.POLL_INTERVAL,
                        )
                    except asyncio.TimeoutError:
                        pass
            except Exception as exc:
                _log.error("DeliveryWorker loop error", error=str(exc))
                try:
                    await asyncio.wait_for(
                        self._stopped.wait(),
                        timeout=self.POLL_INTERVAL * 2,
                    )
                except asyncio.TimeoutError:
                    pass
        _log.info("DeliveryWorker stopped")

    async def _drain_batch(self) -> int:
        """
        Busca batch do outbox, tenta entregar cada um via ChannelRegistry.
        Retorna quantidade entregue.
        """
        loop = asyncio.get_running_loop()
        # Outbox é síncrono (sqlite) — roda em executor
        rows = await loop.run_in_executor(
            None, self.outbox.fetch_pending, self.BATCH_SIZE,
        )
        if not rows:
            return 0

        delivered_count = 0
        for row in rows:
            envelope_id = row["id"]
            target_client_id = row["target_client_id"]
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = {"text": ""}

            text = payload.get("text", "")
            media_url = payload.get("media_url")
            t0 = time.monotonic()

            try:
                if media_url:
                    success = await loop.run_in_executor(
                        None,
                        self.registry.send_media,
                        target_client_id,
                        media_url,
                        payload.get("metadata", {}).get("caption", ""),
                    )
                else:
                    success = await loop.run_in_executor(
                        None,
                        self.registry.reply,
                        target_client_id,
                        text,
                    )

                latency_ms = (time.monotonic() - t0) * 1000

                if success:
                    await loop.run_in_executor(
                        None, self.outbox.mark_delivered, envelope_id,
                    )
                    delivered_count += 1
                    self._emit(
                        "delivery.sent",
                        {
                            "envelope_id": envelope_id,
                            "target": target_client_id,
                            "latency_ms": round(latency_ms, 1),
                        },
                    )
                else:
                    await loop.run_in_executor(
                        None,
                        self.outbox.mark_failed,
                        envelope_id,
                        "adapter returned False",
                    )
                    self._emit(
                        "delivery.failed",
                        {
                            "envelope_id": envelope_id,
                            "target": target_client_id,
                            "error": "adapter returned False",
                        },
                    )

            except Exception as exc:
                await loop.run_in_executor(
                    None,
                    self.outbox.mark_failed,
                    envelope_id,
                    str(exc),
                )
                self._emit(
                    "delivery.failed",
                    {
                        "envelope_id": envelope_id,
                        "target": target_client_id,
                        "error": str(exc),
                    },
                )

        return delivered_count

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emite evento no EventBus (sync, fire-and-forget)."""
        if self.event_bus is not None:
            try:
                self.event_bus.emit(event_type, data)
            except Exception:
                pass  # observabilidade não deve derrubar entrega
