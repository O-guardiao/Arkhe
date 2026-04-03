"""
Channel Status Registry — registro centralizado de estado runtime dos canais.

Inspirado em OpenClaw src/gateway/server-channels.ts:
    ChannelManager.getRuntimeSnapshot() → status de todos canais + accounts

Este módulo é o "cérebro" do service discovery do RLM:
    1. Cada canal registra-se no startup com seu prober
    2. O registry executa probe inicial → captura identidade do bot
    3. Health monitor periódico reproba canais
    4. API endpoint GET /api/channels/status expõe snapshot para TUI/CLI/dashboard

Singleton acessível via ``get_channel_status_registry()`` / ``init_channel_status_registry()``.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from rlm.core.comms.channel_probe import (
    BotIdentity,
    ChannelProber,
    NullProber,
    ProbeResult,
)
from rlm.core.structured_log import get_logger

_log = get_logger("channel_status")


# ── Channel Account Snapshot ──────────────────────────────────────────────

@dataclass
class ChannelAccountSnapshot:
    """
    Estado instantâneo de um canal:account — equivale a
    OpenClaw ChannelAccountSnapshot.
    """
    channel_id: str
    account_id: str = "default"

    # Configuração
    enabled: bool = True
    configured: bool = False

    # Runtime
    running: bool = False
    healthy: bool = False

    # Identidade (populada pelo probe)
    identity: BotIdentity | None = None

    # Probe
    last_probe_at: float = 0.0
    last_probe_ms: float = 0.0
    last_error: str | None = None

    # Lifecycle
    last_start_at: float = 0.0
    last_stop_at: float = 0.0
    reconnect_attempts: int = 0

    # Extra metadata
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializa para JSON (API response)."""
        d: dict[str, Any] = {
            "channel_id": self.channel_id,
            "account_id": self.account_id,
            "enabled": self.enabled,
            "configured": self.configured,
            "running": self.running,
            "healthy": self.healthy,
            "last_error": self.last_error,
            "reconnect_attempts": self.reconnect_attempts,
        }
        if self.identity:
            d["identity"] = {
                "bot_id": self.identity.bot_id,
                "username": self.identity.username,
                "display_name": self.identity.display_name,
            }
        if self.last_probe_at:
            d["last_probe_at"] = self.last_probe_at
            d["last_probe_ms"] = round(self.last_probe_ms, 1)
        if self.meta:
            d["meta"] = self.meta
        return d


# ── Channel Status Registry ──────────────────────────────────────────────

class ChannelStatusRegistry:
    """
    Registro centralizado de status de canais.

    Thread-safe. Guarda ChannelAccountSnapshot por (channel_id, account_id).
    Mantém referência aos probers para health checks sob demanda.
    """

    __slots__ = ("_snapshots", "_probers", "_lock")

    def __init__(self) -> None:
        self._snapshots: dict[str, ChannelAccountSnapshot] = {}
        self._probers: dict[str, ChannelProber] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(channel_id: str, account_id: str = "default") -> str:
        return f"{channel_id}:{account_id}"

    # ── Registration ──────────────────────────────────────────────────

    def register(
        self,
        channel_id: str,
        *,
        account_id: str = "default",
        prober: ChannelProber | None = None,
        enabled: bool = True,
        configured: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> ChannelAccountSnapshot:
        """
        Registra um canal no registry. Chamado no lifespan do api.py.

        Se ``prober`` fornecido, executa probe imediato para capturar
        identidade do bot e status de saúde.
        """
        key = self._key(channel_id, account_id)
        snap = ChannelAccountSnapshot(
            channel_id=channel_id,
            account_id=account_id,
            enabled=enabled,
            configured=configured,
            meta=meta or {},
        )

        if prober:
            self._probers[key] = prober
            result = prober.probe()
            snap.healthy = result.ok
            snap.identity = result.identity
            snap.last_probe_at = time.time()
            snap.last_probe_ms = result.elapsed_ms
            if not result.ok:
                snap.last_error = result.error
                _log.warn(
                    f"Probe failed for {channel_id}:{account_id}",
                    error=result.error,
                    elapsed_ms=round(result.elapsed_ms, 1),
                )
            else:
                _id = result.identity
                _log.info(
                    f"Probe OK: {channel_id}:{account_id}",
                    bot_id=_id.bot_id if _id else None,
                    username=_id.username if _id else None,
                    elapsed_ms=round(result.elapsed_ms, 1),
                )

        with self._lock:
            self._snapshots[key] = snap

        return snap

    # ── Status Updates ────────────────────────────────────────────────

    def update(
        self,
        channel_id: str,
        account_id: str = "default",
        **kwargs: Any,
    ) -> ChannelAccountSnapshot | None:
        """
        Atualiza campos de um snapshot existente (running, healthy, etc.).
        Retorna o snapshot atualizado ou None se não registrado.
        """
        key = self._key(channel_id, account_id)
        with self._lock:
            snap = self._snapshots.get(key)
            if snap is None:
                return None
            for attr, value in kwargs.items():
                if hasattr(snap, attr):
                    setattr(snap, attr, value)
            return snap

    def mark_running(
        self, channel_id: str, account_id: str = "default"
    ) -> None:
        """Marca canal como running (chamado quando gateway inicia com sucesso)."""
        self.update(
            channel_id, account_id,
            running=True, healthy=True, last_start_at=time.time(),
        )

    def mark_stopped(
        self, channel_id: str, account_id: str = "default", error: str | None = None,
    ) -> None:
        """Marca canal como parado."""
        self.update(
            channel_id, account_id,
            running=False, healthy=False, last_stop_at=time.time(),
            last_error=error,
        )

    def mark_error(
        self, channel_id: str, account_id: str = "default", error: str = "",
    ) -> None:
        """Marca erro no canal sem parar."""
        snap = self.update(channel_id, account_id, healthy=False, last_error=error)
        if snap:
            snap.reconnect_attempts += 1

    # ── Probe ─────────────────────────────────────────────────────────

    def probe(
        self, channel_id: str, account_id: str = "default", timeout_s: float = 10.0,
    ) -> ProbeResult:
        """
        Executa probe sob demanda para um canal registrado.
        Atualiza o snapshot com o resultado.
        """
        key = self._key(channel_id, account_id)
        prober = self._probers.get(key)
        if prober is None:
            return ProbeResult(ok=False, error=f"No prober for {channel_id}:{account_id}")

        result = prober.probe(timeout_s=timeout_s)

        with self._lock:
            snap = self._snapshots.get(key)
            if snap:
                snap.healthy = result.ok
                snap.last_probe_at = time.time()
                snap.last_probe_ms = result.elapsed_ms
                snap.last_error = result.error if not result.ok else snap.last_error
                if result.identity:
                    snap.identity = result.identity

        return result

    def probe_all(self, timeout_s: float = 10.0) -> dict[str, ProbeResult]:
        """Probe todos os canais registrados. Retorna {key: ProbeResult}."""
        results: dict[str, ProbeResult] = {}
        with self._lock:
            keys = list(self._probers.keys())
        for key in keys:
            parts = key.split(":", 1)
            ch_id, acc_id = parts[0], parts[1] if len(parts) > 1 else "default"
            results[key] = self.probe(ch_id, acc_id, timeout_s=timeout_s)
        return results

    # ── Query ─────────────────────────────────────────────────────────

    def get(
        self, channel_id: str, account_id: str = "default",
    ) -> ChannelAccountSnapshot | None:
        """Retorna snapshot de um canal específico."""
        key = self._key(channel_id, account_id)
        with self._lock:
            return self._snapshots.get(key)

    def get_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """
        Snapshot completo de todos os canais — serializado para API.

        Formato:
            {
                "telegram": [
                    {"channel_id": "telegram", "account_id": "default", "running": true, ...}
                ],
                "discord": [...],
            }
        """
        with self._lock:
            by_channel: dict[str, list[dict[str, Any]]] = {}
            for snap in self._snapshots.values():
                by_channel.setdefault(snap.channel_id, []).append(snap.to_dict())
        return by_channel

    def list_channels(self) -> list[str]:
        """Lista IDs de canais registrados (sem duplicatas)."""
        with self._lock:
            return sorted({s.channel_id for s in self._snapshots.values()})

    def list_running(self) -> list[ChannelAccountSnapshot]:
        """Lista apenas canais ativos (running=True)."""
        with self._lock:
            return [s for s in self._snapshots.values() if s.running]

    def summary(self) -> dict[str, Any]:
        """Resumo compacto para health endpoint."""
        with self._lock:
            total = len(self._snapshots)
            running = sum(1 for s in self._snapshots.values() if s.running)
            healthy = sum(1 for s in self._snapshots.values() if s.healthy)
        return {
            "total": total,
            "running": running,
            "healthy": healthy,
            "channels": self.get_snapshot(),
        }


# ── Singleton ─────────────────────────────────────────────────────────────

_registry_instance: ChannelStatusRegistry | None = None
_registry_lock = threading.Lock()


def init_channel_status_registry() -> ChannelStatusRegistry:
    """Inicializa o singleton. Idempotente."""
    global _registry_instance
    with _registry_lock:
        if _registry_instance is None:
            _registry_instance = ChannelStatusRegistry()
            _log.info("ChannelStatusRegistry initialized")
        return _registry_instance


def get_channel_status_registry() -> ChannelStatusRegistry:
    """Retorna o singleton. Levanta RuntimeError se não inicializado."""
    if _registry_instance is None:
        raise RuntimeError(
            "ChannelStatusRegistry não inicializado. "
            "Chame init_channel_status_registry() no lifespan."
        )
    return _registry_instance


def _reset_singleton() -> None:
    """Reset para testes. Não usar em produção."""
    global _registry_instance
    with _registry_lock:
        _registry_instance = None
