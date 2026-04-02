"""
RLM Graceful Shutdown — Fase 10.4

Inspirado em: VS Code src/vs/platform/lifecycle/common/lifecycle.ts (MIT, Microsoft)

Gerencia shutdown graceful com sistema de veto: antes de fechar,
pergunta a cada participante se pode fechar. Se alguém veta (ex:
compactação em andamento, memória salvando), o shutdown espera.

Uso:
    manager = ShutdownManager()

    # Compactor registra participação:
    manager.register(lambda: compactor.is_running)  # veta se rodando

    # No shutdown:
    await manager.shutdown(timeout=10.0)
    # Fase 1: pergunta a todos — espera quem vetar
    # Fase 2: força dispose de tudo
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable

from rlm.core.lifecycle.disposable import DisposableStore, IDisposable, to_disposable
from rlm.logging import get_runtime_logger

log = get_runtime_logger("shutdown")


# ---------------------------------------------------------------------------
# ShutdownParticipant
# ---------------------------------------------------------------------------

# Um participante retorna True para vetar (adiar) o shutdown.
# False = pode fechar, True = preciso de mais tempo
ShutdownVetoFn = Callable[[], bool]


# ---------------------------------------------------------------------------
# ShutdownManager
# ---------------------------------------------------------------------------

class ShutdownManager:
    """
    Gerencia shutdown com fases:
      1. Pergunta a cada participante se pode fechar (veto check)
      2. Espera até timeout que vetos se resolvam
      3. Força dispose de todos os recursos registrados

    Participantes podem:
      - Registrar veto functions que retornam True enquanto trabalho crítico roda
      - Registrar disposables que são limpos na fase final

    Thread-safe — pode ser chamado de qualquer thread.
    """

    def __init__(self) -> None:
        self._veto_fns: list[tuple[str, ShutdownVetoFn]] = []
        self._disposables = DisposableStore()
        self._lock = threading.Lock()
        self._shutting_down = False

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def register_veto(self, name: str, veto_fn: ShutdownVetoFn) -> IDisposable:
        """
        Registra uma função de veto. Chamada durante shutdown para saber
        se este subsistema precisa de mais tempo.

        Args:
            name: Nome do subsistema (para logging)
            veto_fn: Retorna True se o shutdown deve ser adiado

        Returns:
            IDisposable que remove o registro
        """
        entry = (name, veto_fn)
        with self._lock:
            self._veto_fns.append(entry)

        def _remove():
            with self._lock:
                try:
                    self._veto_fns.remove(entry)
                except ValueError:
                    pass

        return to_disposable(_remove)

    def register_disposable(self, item: IDisposable) -> IDisposable:
        """Registra um disposable para ser limpo na fase de force-close."""
        return self._disposables.add(item)

    def shutdown_sync(self, timeout: float = 10.0) -> None:
        """
        Executa shutdown síncrono com fases de veto.

        Fase 1: Pergunta a cada participante — espera até que todos liberem
                 ou timeout expire.
        Fase 2: Força dispose de todos os recursos.
        """
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            vetos = list(self._veto_fns)

        log.info("Shutdown iniciado", participants=len(vetos))

        # Fase 1: espera vetos resolverem
        deadline = time.monotonic() + timeout
        poll_interval = 0.2

        while time.monotonic() < deadline:
            active_vetos = []
            for name, veto_fn in vetos:
                try:
                    if veto_fn():
                        active_vetos.append(name)
                except Exception as e:
                    log.warn("Veto check falhou", participant=name, error=str(e))

            if not active_vetos:
                log.info("Todos participantes liberaram — procedendo com dispose")
                break

            log.info("Aguardando participantes", participants=", ".join(active_vetos))
            time.sleep(poll_interval)
        else:
            log.warn("Timeout de shutdown — forçando dispose", timeout_s=timeout)

        # Fase 2: força dispose de tudo
        self._disposables.dispose()
        log.info("Shutdown completo")

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Versão async do shutdown para uso em contextos FastAPI."""
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            vetos = list(self._veto_fns)

        log.info("Async shutdown iniciado", participants=len(vetos))

        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            active_vetos = []
            for name, veto_fn in vetos:
                try:
                    if veto_fn():
                        active_vetos.append(name)
                except Exception as e:
                    log.warn("Async veto check falhou", participant=name, error=str(e))

            if not active_vetos:
                break

            log.info("Aguardando participantes", participants=", ".join(active_vetos))
            await asyncio.sleep(0.2)
        else:
            log.warn("Timeout de async shutdown — forçando dispose", timeout_s=timeout)

        self._disposables.dispose()
        log.info("Async shutdown completo")
