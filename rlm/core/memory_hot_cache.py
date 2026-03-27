"""
memory_hot_cache.py — Cache Quente de Memória por Sessão

Responsabilidade única: servir chunks de memória relevantes em <1ms (leitura síncrona
do cache em memória), enquanto atualiza o cache em background (sem bloquear o turno).

Arquitetura:
  - Uma instância de MemorySessionCache por session_id.
  - O cache armazena os últimos chunks recuperados pelo budget gate.
  - Leitura é SEMPRE síncrona e non-blocking (retorna o cache atual, mesmo que stale).
  - Atualização é SEMPRE assíncrona (thread daemon, fire-and-forget).
  - Thread safety: threading.RLock protege leitura e escrita no cache.

Fluxo por turno:
  T=0ms  → chat(user_msg) chamado
  T=0ms  → cache.read_sync() → retorna chunks do turno anterior (ou [] se primeiro turno)
  T=0ms  → prompt construído com chunks do cache (sem espera!)
  T=Xms  → RLM completion (o turno pesado)
  T=Xms  → cache.schedule_update(user_msg) → dispara atualização em background
  T=X+Nms → background thread atualiza o cache com novos chunks para o PRÓXIMO turno

Resultado: o usuário espera apenas o RLM, nunca a busca de memória.
Custo: o primeiro turno usa cache vazio; a partir do segundo, o cache está preaquecido.

Nota sobre consistência:
  O cache pode ter 1 turno de defasagem. Isso é aceitável porque:
  1. Memory injection é contextual, não crítico-de-precisão
  2. O ganho de latência (~200-500ms de search_hybrid) é muito maior que a perda
  3. search_hybrid ainda tem temporal decay — memórias muito antigas são penalizadas
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rlm.core.structured_log import get_logger

if TYPE_CHECKING:
    from rlm.core.memory_manager import MultiVectorMemory

_log = get_logger("memory_hot_cache")

# Tempo mínimo entre atualizações do cache (evita burst em turnos rápidos)
_MIN_UPDATE_INTERVAL_S: float = 2.0


# ---------------------------------------------------------------------------
# Registro por sessão
# ---------------------------------------------------------------------------

@dataclass
class MemorySessionCache:
    """
    Cache de chunks de memória para uma única sessão.

    Não use diretamente — use get_or_create_cache() para obter instâncias.
    """

    session_id: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    last_updated: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _update_thread: threading.Thread | None = field(default=None, repr=False)

    def read_sync(self) -> list[dict[str, Any]]:
        """
        Retorna os chunks em cache de forma síncrona (<1ms).

        Nunca bloqueia — se o lock estiver ocupado por uma atualização,
        retorna o último estado consistente. Seguro para chamar no hot path.

        Returns:
            Cópia rasa da lista de chunks atual. Pode estar 1 turno desatualizada.
        """
        with self._lock:
            return list(self.chunks)

    def schedule_update(
        self,
        query: str,
        memory_manager: "MultiVectorMemory",
        available_tokens: int = 4000,
    ) -> None:
        """
        Agenda uma atualização do cache em background (non-blocking).

        Dispara um daemon thread que busca novos chunks relevantes para a query
        e atualiza o cache. Se uma atualização já está em andamento ou foi feita
        recentemente, a nova requisição é ignorada (evita burst).

        Args:
            query: Query para busca de relevância (tipicamente a última mensagem do usuário).
            memory_manager: Instância de MultiVectorMemory para busca.
            available_tokens: Tokens disponíveis estimados para budget gate.
        """
        now = time.time()

        with self._lock:
            # Throttle: não atualiza se foi atualizado recentemente
            if now - self.last_updated < _MIN_UPDATE_INTERVAL_S:
                return
            # Não inicia nova thread se já há uma rodando
            if self._update_thread is not None and self._update_thread.is_alive():
                return

            thread = threading.Thread(
                target=self._run_update,
                args=(query, memory_manager, available_tokens),
                daemon=True,
                name=f"rlm-cache-update-{self.session_id[:8]}",
            )
            self._update_thread = thread

        thread.start()

    def _run_update(
        self,
        query: str,
        memory_manager: "MultiVectorMemory",
        available_tokens: int,
    ) -> None:
        """Executado em background thread. Falha silenciosa total."""
        try:
            from rlm.core.memory_budget import inject_memory_with_budget

            new_chunks, _ = inject_memory_with_budget(
                query=query,
                session_id=self.session_id,
                memory_manager=memory_manager,
                available_tokens=available_tokens,
            )

            with self._lock:
                self.chunks = new_chunks
                self.last_updated = time.time()

            _log.debug(
                f"Cache atualizado para sessão {self.session_id[:8]}…: "
                f"{len(new_chunks)} chunks"
            )
        except Exception as exc:
            _log.warn(f"MemorySessionCache._run_update falhou (suprimido): {exc}")

    def invalidate(self) -> None:
        """
        Invalida o cache (zera chunks e timestamp).
        Útil após reset de sessão ou mudança de contexto drástica.
        """
        with self._lock:
            self.chunks = []
            self.last_updated = 0.0


# ---------------------------------------------------------------------------
# Registry global de caches por sessão
# ---------------------------------------------------------------------------

_registry: dict[str, MemorySessionCache] = {}
_registry_lock = threading.Lock()


def get_or_create_cache(session_id: str) -> MemorySessionCache:
    """
    Retorna o cache da sessão, criando-o se ainda não existe.

    Thread-safe. Prefer usar esta função em vez de instanciar MemorySessionCache diretamente.

    Args:
        session_id: Identificador único da sessão.

    Returns:
        Instância de MemorySessionCache para a sessão.
    """
    with _registry_lock:
        if session_id not in _registry:
            _registry[session_id] = MemorySessionCache(session_id=session_id)
        return _registry[session_id]


def evict_cache(session_id: str) -> None:
    """
    Remove o cache de uma sessão do registry (libera memória).
    Chamado por RLMSession.dispose() ou close().

    Args:
        session_id: Sessão a remover.
    """
    with _registry_lock:
        _registry.pop(session_id, None)


def registry_size() -> int:
    """Retorna o número de sessões com cache ativo (para monitoramento)."""
    with _registry_lock:
        return len(_registry)
