"""
_sub_rlm_types — Exceptions, dataclasses e tipos públicos de sub_rlm.

Extraído de sub_rlm.py para separação de responsabilidades.
Todos os símbolos são re-exportados por sub_rlm.py — imports existentes
continuam funcionando sem alteração.
"""
from __future__ import annotations

import queue as _queue_mod
import threading
import time
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SubRLMError(RuntimeError):
    """Raised when a sub_rlm() call fails."""


class SubRLMDepthError(SubRLMError):
    """Raised when depth limit is reached and sub_rlm() cannot spawn."""


class SubRLMTimeoutError(SubRLMError):
    """Raised when sub_rlm() exceeds its timeout."""


# ---------------------------------------------------------------------------
# Result record (optional — exposto para testes e inspeção)
# ---------------------------------------------------------------------------

@dataclass
class SubRLMResult:
    task: str
    answer: str
    depth: int
    iterations_hint: int
    elapsed_s: float
    timed_out: bool = False
    error: str | None = None


@dataclass
class SubRLMArtifactResult:
    """Retornado por ``sub_rlm(..., return_artifacts=True)``.

    Contém a resposta textual normal **mais** os artefatos computacionais
    que o filho criou no seu REPL durante a execução.

    Artefatos são: funções Python criadas, dados processados, modelos
    parcialmente computados — qualquer variável local não-privada que não
    seja variável de entrada (``context_N``, ``history_N``).

    O insight central: cada filho pode **sintetizar primitivas** que o pai
    (ou chamadas subsequentes) reutilizam sem precisar recomputar.

    Campos
    ------
    answer    : resposta textual final (equivalente ao return normal).
    artifacts : dict ``{nome: valor}`` de todos os locals extrados do filho.
    depth     : profundidade do filho na hierarquia RLM.

    Métodos auxiliares
    ------------------
    ``callables()``       — só artefatos chamáveis (funções/lambdas/classes).
    ``values()``          — só artefatos não-chamáveis (dados/strings/dicts).
    ``as_custom_tools()`` — formato pronto para ``custom_tools=`` no RLM pai.

    Exemplo de uso no REPL do pai::

        # Filho cria uma função especializada
        resultado = sub_rlm(
            "Cria parse_log() que extrai campos de logs nginx no formato JSON",
            return_artifacts=True,
        )
        print(resultado.answer)  # "Função parse_log() criada e validada"

        # Injetar a função sintetizada nos próximos filhos
        logs_parsed = sub_rlm_parallel(
            [f"Parseia /data/log_{i}.txt" for i in range(10)],
            custom_tools=resultado.as_custom_tools(),  # <- reutiliza!
        )
    """
    answer: str
    artifacts: dict[str, Any]
    depth: int = 0

    def callables(self) -> dict[str, Any]:
        """Retorna apenas os artefatos que são chamáveis (funções, lambdas, classes)."""
        return {k: v for k, v in self.artifacts.items() if callable(v)}

    def values(self) -> dict[str, Any]:
        """Retorna apenas os artefatos não-chamáveis (str, dict, list, etc)."""
        return {k: v for k, v in self.artifacts.items() if not callable(v)}

    def as_custom_tools(self) -> dict[str, Any]:
        """Converte todos os artefatos para o formato ``custom_tools=`` do RLM.

        Inclui tanto chamáveis quanto valores não-chamáveis.
        Pronto para passar a ``RLM(custom_tools=...)`` ou atribuir a
        ``rlm_instance.custom_tools``.

        Uso::

            r = sub_rlm("...", return_artifacts=True)
            rlm_filho = RLM(custom_tools=r.as_custom_tools(), ...)
        """
        return dict(self.artifacts)


# ---------------------------------------------------------------------------
# AsyncHandle — handle de um filho rodando em background
# ---------------------------------------------------------------------------

class AsyncHandle:
    """
    Handle retornado por ``sub_rlm_async()``. Representa um filho RLM em execução
    em daemon thread — o chamador não bloqueia.

    API básica:
        handle.is_done          → True se o filho terminou
        handle.elapsed_s        → segundos desde o início
        handle.result(timeout)  → bloqueia e retorna resposta final
        handle.log_poll()       → lê mensagens de progresso do filho (não bloqueia)
        handle.cancel()         → sinaliza que o resultado não é mais necessário

    P2P dinâmico (bus compartilhado entre todos os filhos async do mesmo pai):
        handle.bus              → SiblingBus — mesmo objeto que o filho recebeu
        handle.branch_id        → ID único deste filho na rede

    O bus persiste no objeto pai (RLM._async_bus), então filhos lançados em
    momentos diferentes — inclusive em turns distintos — compartilham o mesmo
    barramento. O pai também fala no bus via ``async_bus`` no REPL.

    Exemplo de coordenação:
        # No REPL do pai:
        h1 = sub_rlm_async("analisa jan.csv")
        h2 = sub_rlm_async("analisa fev.csv")
        async_bus.publish("control/formato", "parquet")  # h1 e h2 lêem

        # No REPL de h1 (e h2):
        fmt = sibling_subscribe("control/formato", timeout_s=5.0)
        sibling_publish("resultado/jan", {"linhas": 1024})

        # Após no REPL do pai, ou via Python:
        dados = h1.bus.peek("resultado/jan")
    """

    def __init__(
        self,
        task: str,
        depth: int,
        thread: threading.Thread,
        result_holder: list,
        error_holder: list,
        log_queue: "_queue_mod.Queue[str]",
        bus: "Any | None" = None,
        branch_id: "int | None" = None,
        cancel_event: "threading.Event | None" = None,
        cancel_token_source: "Any | None" = None,
    ) -> None:
        self.task = task
        self.depth = depth
        self._thread = thread
        self._result_holder = result_holder
        self._error_holder = error_holder
        self._log_queue = log_queue
        self._started_at = time.perf_counter()
        self._cancelled = False
        #: Event partilhado com o filho — filho lê via check_cancel() no REPL
        self._cancel_event: threading.Event = (
            cancel_event if cancel_event is not None else threading.Event()
        )
        #: CancellationTokenSource do filho — bridge bidirecional
        self._cancel_token_source = cancel_token_source
        #: SiblingBus compartilhado — acesso Python-native ao barramento P2P
        self.bus = bus
        #: ID único deste filho na rede de filhos async do pai
        self.branch_id = branch_id

    @property
    def is_done(self) -> bool:
        """True se o filho terminou (com sucesso, erro ou timeout natural)."""
        return not self._thread.is_alive()

    @property
    def elapsed_s(self) -> float:
        """Segundos decorridos desde que o filho foi iniciado."""
        return time.perf_counter() - self._started_at

    def result(self, timeout_s: float = 300.0) -> str:
        """
        Bloqueia até o filho terminar e retorna a resposta final.

        Args:
            timeout_s: Máximo de segundos a esperar. Default 300s.

        Returns:
            String com a resposta do filho.

        Raises:
            SubRLMTimeoutError: Se o filho não terminar no prazo.
            SubRLMError: Se o filho falhou com exceção.
        """
        if not self.is_done:
            self._thread.join(timeout=timeout_s)
        if self._thread.is_alive():
            raise SubRLMTimeoutError(
                f"sub_rlm_async: filho não terminou em {timeout_s:.0f}s "
                f"(depth={self.depth}). "
                "Use handle.log_poll() para ver o progresso parcial."
            )
        if self._error_holder:
            exc = self._error_holder[0]
            raise SubRLMError(
                f"sub_rlm_async: filho falhou (depth={self.depth}): {exc}"
            ) from exc
        if not self._result_holder:
            raise SubRLMError(
                f"sub_rlm_async: filho não retornou resposta (depth={self.depth})."
            )
        return self._result_holder[0]

    def log_poll(self) -> list[str]:
        """
        Lê mensagens de progresso publicadas pelo filho sem bloquear.

        O filho publica via ``parent_log("msg")`` no seu REPL.
        Cada chamada drena as mensagens novas desde a última leitura.

        Returns:
            Lista de strings (pode ser vazia).
        """
        msgs: list[str] = []
        while True:
            try:
                msgs.append(self._log_queue.get_nowait())
            except _queue_mod.Empty:
                break
        return msgs

    def cancel(self) -> None:
        """
        Sinaliza cancelamento ao filho via ``threading.Event`` e
        ``CancellationToken`` (bridge bidirecional).

        O filho verifica chamando ``check_cancel()`` no seu REPL — retorna
        ``True`` quando cancelado. O LLM filho deve verificar periodicamente
        entre etapas longas e encerrar limpo::

            if check_cancel():
                parent_log("cancelado pelo pai, encerrando")
                FINAL_VAR("cancelado")

        Após cancel(), chamar result() ainda pode retornar o valor se o
        filho já terminou antes de verificar o evento.
        """
        self._cancelled = True
        self._cancel_event.set()
        # Bridge reverso: event → token — garante que netos via hierarquia
        # de CancellationToken também recebam o sinal
        if self._cancel_token_source is not None:
            self._cancel_token_source.cancel(reason="AsyncHandle.cancel()")

    def __repr__(self) -> str:
        bid = f" branch={self.branch_id}" if self.branch_id is not None else ""
        status = "done" if self.is_done else f"running {self.elapsed_s:.1f}s"
        return f"<AsyncHandle depth={self.depth}{bid} {status} task={self.task[:40]!r}>"


# ---------------------------------------------------------------------------
# Parallel result types
# ---------------------------------------------------------------------------

@dataclass
class SubRLMParallelTaskResult:
    """
    Resultado de uma única tarefa dentro de sub_rlm_parallel().

    Campos:
        task        — texto original da tarefa
        branch_id   — índice (0-based) da tarefa no array de entrada
        ok          — True se executou sem exceção
        answer      — resposta do filho (str) ou None se falhou
        error       — mensagem de erro se ok=False, None se ok=True
        elapsed_s   — tempo de execução desta tarefa
    """
    task: str
    branch_id: int
    ok: bool
    answer: str | None
    error: str | None
    elapsed_s: float
    task_id: int | None = None
    parent_task_id: int | None = None
    status: str = "not-started"


class SubRLMParallelDetailedResults(list[SubRLMParallelTaskResult]):
    def __init__(
        self,
        items: list[SubRLMParallelTaskResult] | None = None,
        *,
        summary: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(items or [])
        self.summary = dict(summary or {})


# ---------------------------------------------------------------------------
# Type aliases para anotações externas
# ---------------------------------------------------------------------------

SubRLMCallable = Any
SubRLMParallelCallable = Any
