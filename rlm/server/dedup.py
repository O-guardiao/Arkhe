"""
RLM Dedup — Deduplicação de mensagens por TTL para gateways webhook.

Problema real:
    Meta (WhatsApp) e Slack re-entregam webhooks quando não recebem 200
    rápido o suficiente. O RLM pode receber a mesma mensagem 2-3x,
    gerando respostas duplicadas ao usuário.

Solução:
    MessageDedup mantém um cache de msg_ids vistos com TTL automático.
    Cada gateway chama dedup.is_duplicate(msg_id) antes de processar.
    Se True, descarta silenciosamente.

Design RLM-nativo:
    - Thread-safe via threading.Lock (gateways async usam BackgroundTask
      que pode rodar em thread diferente)
    - OrderedDict como LRU — evicção O(1) sem percorrer toda a estrutura
    - IDisposable — integra com DisposableStore para cleanup no shutdown
    - Sem dependências externas

Capacidade padrão: 10.000 entradas × ~100 bytes = ~1 MB máx.
TTL padrão: 300s (5 min) — cobre a janela de re-entrega da Meta (~2-3 min).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Protocol, runtime_checkable

from rlm.logging import get_runtime_logger

log = get_runtime_logger("dedup")


@runtime_checkable
class IDisposable(Protocol):
    def dispose(self) -> None: ...


class MessageDedup:
    """
    Cache de deduplicação baseado em TTL + LRU.

    Uso:
        dedup = MessageDedup(ttl_s=300, max_entries=10_000)
        if dedup.is_duplicate("wamid.abc123"):
            return  # descarta silenciosamente
        # ... processar mensagem
    """

    __slots__ = ("_ttl_s", "_max_entries", "_cache", "_lock", "_disposed")

    def __init__(self, ttl_s: float = 300.0, max_entries: int = 10_000) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        # OrderedDict: chave = msg_id, valor = timestamp de inserção
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()
        self._disposed = False

    # ── API pública ──────────────────────────────────────────────────────

    def is_duplicate(self, msg_id: str) -> bool:
        """
        Retorna True se msg_id já foi visto dentro do TTL.
        Se não é duplicata, registra o msg_id automaticamente.

        Thread-safe — pode ser chamado de BackgroundTask e polling threads.
        """
        if not msg_id:
            return False  # sem ID = não dá pra deduplicar, deixa passar

        now = time.monotonic()

        with self._lock:
            if self._disposed:
                return False

            # Evicção lazy dos expirados mais antigos (O(1) amortizado)
            self._evict_expired(now)

            if msg_id in self._cache:
                # Move to end para LRU
                self._cache.move_to_end(msg_id)
                log.debug("Mensagem duplicada detectada", msg_id=msg_id)
                return True

            # Registrar como visto
            self._cache[msg_id] = now

            # Evicção por capacidade (remove o mais antigo)
            while len(self._cache) > self._max_entries:
                evicted_id, _ = self._cache.popitem(last=False)
                log.debug("Dedup eviction (capacity)", evicted_id=evicted_id)

            return False

    def seen_count(self) -> int:
        """Número de entradas atualmente no cache."""
        with self._lock:
            return len(self._cache)

    def clear(self) -> None:
        """Limpa todo o cache. Útil em testes."""
        with self._lock:
            self._cache.clear()

    # ── IDisposable ──────────────────────────────────────────────────────

    def dispose(self) -> None:
        """Libera o cache e marca como disposed."""
        with self._lock:
            if self._disposed:
                return
            count = len(self._cache)
            self._cache.clear()
            self._disposed = True
        if count:
            log.debug("MessageDedup disposed", entries_cleared=count)

    # ── Internos ─────────────────────────────────────────────────────────

    def _evict_expired(self, now: float) -> None:
        """Remove entradas expiradas do início do OrderedDict (mais antigas primeiro)."""
        cutoff = now - self._ttl_s
        while self._cache:
            # Peek no primeiro item (mais antigo)
            oldest_id, oldest_ts = next(iter(self._cache.items()))
            if oldest_ts > cutoff:
                break  # tudo daí pra frente está válido
            self._cache.popitem(last=False)
