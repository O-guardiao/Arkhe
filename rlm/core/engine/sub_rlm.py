"""
sub_rlm — Fase 9.3: Decomposição Explícita via REPL

Expõe `sub_rlm(task, ...)` como função injetável no namespace REPL.

Problema que resolve
--------------------
O mecanismo atual de recursão é implícito e cego: o REPL faz socket_request
e o LMHandler roteia para um sub-backend baseado em `depth`. O LLM não
controla *quando* delegar, *o quê* delegar, nem *recebe de volta* o resultado
para usar na próxima linha.

`sub_rlm()` transforma isso em decomposição explícita:

    # O próprio LLM escreve isso no REPL:
    dados = sub_rlm("Normaliza /tmp/vendas.csv, retorna CSV string")
    metricas = sub_rlm(f"Calcula KPIs deste CSV:\\n{dados}", max_iterations=8)
    FINAL_VAR("metricas")

Garantias
---------
- Namespace isolation: namespace filho é completamente separado do pai.
- Depth guard: se depth >= max_depth, levanta SubRLMDepthError (não recursão infinita).
- Timeout: `timeout_s` garante que o filho não trava o pai indefinidamente.
- Herança de backend: filho usa mesmo backend/modelo do pai por padrão.
- Thread-safe: cada filho cria seu próprio LMHandler (socket separado).

Quando NÃO usar
---------------
- Tarefas < 5 iterações → use `llm_query()` (sem overhead de nova instância).
- Paralelismo (3 filhos simultâneos) → race condition nos sockets. Use sequencial.
- Quando o contexto REPL do pai precisa ser compartilhado → não compartilha (proposital).

Estrutura do módulo (pós-refatoração)
-------------------------------------
- ``_sub_rlm_types.py``   — Exceptions, dataclasses, AsyncHandle, type aliases.
- ``_sub_rlm_helpers.py`` — Helpers privados (parent interaction, heuristics, guidance).
- ``sub_rlm.py``          — Factories (serial, async, parallel) + re-exports públicos.
"""
from __future__ import annotations

import concurrent.futures as _cf
import queue as _queue_mod
import threading
import time
from typing import Any, TYPE_CHECKING, cast

from rlm.core.security.execution_policy import build_backend_kwargs, resolve_subagent_model
from rlm.core.types import ClientBackend, EnvironmentType

# ── Re-exports públicos (mantém backward compat) ─────────────────────────
from rlm.core.engine._sub_rlm_types import (  # noqa: F401
    SubRLMError,
    SubRLMDepthError,
    SubRLMTimeoutError,
    SubRLMResult,
    SubRLMArtifactResult,
    AsyncHandle,
    SubRLMParallelTaskResult,
    SubRLMParallelDetailedResults,
    SubRLMCallable,
    SubRLMParallelCallable,
)

# ── Helpers privados ──────────────────────────────────────────────────────
from rlm.core.engine._sub_rlm_helpers import (
    _record_parent_runtime_event,
    _attach_parent_bus,
    _get_parent_env,
    _register_parent_subagent_task,
    _update_parent_subagent_task,
    _ensure_parallel_batch_root,
    _set_parent_parallel_summary,
    _get_parent_active_recursive_strategy,
    _resolve_parallel_strategy,
    _merge_context_fragments,
    _build_recursive_guidance_context,
    _compute_parallel_heuristics,
    _evaluate_stop_condition,
)

if TYPE_CHECKING:
    from rlm.core.engine.rlm import RLM


# ---------------------------------------------------------------------------
# Shared: child spawn helper (elimina duplicação serial ↔ async)
# ---------------------------------------------------------------------------

def _prepare_child_env_kwargs(
    parent: "RLM",
    *,
    sibling_bus: Any | None = None,
    sibling_branch_id: int | None = None,
    cancel_event: "threading.Event | None" = None,
    log_queue: "_queue_mod.Queue[str] | None" = None,
) -> dict[str, Any]:
    """Constrói env_kwargs para um filho, incluindo memória/canal/bus/cancel."""
    _env_kwargs = parent.environment_kwargs.copy() if parent.environment_kwargs else {}
    if sibling_bus is not None:
        _env_kwargs["_sibling_bus"] = sibling_bus
        _env_kwargs["_sibling_branch_id"] = sibling_branch_id
    if cancel_event is not None:
        _env_kwargs["_cancel_event"] = cancel_event
    if log_queue is not None:
        _env_kwargs["_parent_log_queue"] = log_queue

    # Lacuna 1: Compartilhar memória do pai com filhos
    _parent_memory = getattr(parent, "_shared_memory", None)
    if _parent_memory is None:
        _penv = getattr(parent, "_persistent_env", None)
        if _penv is not None:
            _parent_memory = getattr(_penv, "_memory", None)
    if _parent_memory is not None:
        _env_kwargs["_parent_memory"] = _parent_memory
        # Multichannel: propagar canal de origem do pai para filhos
        _parent_ctx = getattr(_parent_memory, "_agent_context", None)
        if _parent_ctx is not None and getattr(_parent_ctx, "channel", None) is not None:
            _env_kwargs["_originating_channel"] = _parent_ctx.channel

    return _env_kwargs


def _spawn_child_rlm(
    parent: "RLM",
    *,
    _rlm_cls: "type[RLM] | None",
    child_depth: int,
    max_iterations: int,
    env_kwargs: dict[str, Any],
    child_model: str | None,
    system_prompt: str | None = None,
    interaction_mode: str = "repl",
    strategy_context: dict[str, Any] | None = None,
) -> "RLM":
    """Cria uma instância RLM filha com todas as propagações corretas."""
    if _rlm_cls is not None:
        _cls = _rlm_cls
    else:
        from rlm.core.engine.rlm import RLM as _cls

    child = _cls(
        backend=cast(ClientBackend, parent.backend),
        backend_kwargs=build_backend_kwargs(parent.backend_kwargs, child_model),
        environment=cast(EnvironmentType, parent.environment_type),
        environment_kwargs=env_kwargs if env_kwargs else None,
        depth=child_depth,
        max_depth=parent.max_depth,
        max_iterations=max(1, min(max_iterations, 50)),
        custom_system_prompt=system_prompt,
        interaction_mode=interaction_mode,
        verbose=False,
        event_bus=parent.event_bus,
    )

    if strategy_context:
        child._active_recursive_strategy = dict(strategy_context)
        child._active_mcts_archive_key = strategy_context.get("archive_key")
    parent_archive_store = getattr(parent, "_mcts_archives", None)
    if isinstance(parent_archive_store, dict):
        child._mcts_archives = parent_archive_store

    return child


def _propagate_cancel_token(
    parent: "RLM",
    child: "RLM",
    cancel_event: "threading.Event | None" = None,
) -> Any:
    """Propaga CancellationToken do pai para o filho. Retorna CancellationTokenSource ou None."""
    _parent_token = getattr(parent, "_cancel_token", None)
    if _parent_token is not None and hasattr(_parent_token, "is_cancelled"):
        from rlm.core.lifecycle.cancellation import CancellationTokenSource
        _child_cts = CancellationTokenSource(parent=_parent_token)
        child._cancel_token = _child_cts.token
        if cancel_event is not None:
            _child_cts.token.on_cancelled(lambda evt=cancel_event: evt.set())
        return _child_cts
    return None


# ---------------------------------------------------------------------------
# Core function factory — sub_rlm (serial)
# ---------------------------------------------------------------------------

def make_sub_rlm_fn(parent: "RLM", _rlm_cls: "type[RLM] | None" = None) -> "SubRLMCallable":
    """
    Retorna a função `sub_rlm(task, ...)` vinculada ao RLM pai.

    Deve ser chamada antes do loop de completion e o resultado injetado em
    `environment.globals["sub_rlm"]`.

    Args:
        parent: instância RLM pai que será usada como base de configuração.
                Aceita também RLMSession — o wrapper é removido automaticamente.
        _rlm_cls: classe RLM a instanciar (injectável para testes). Se None,
                  importa `rlm.core.engine.rlm.RLM` em runtime (evita circular import).

    Returns:
        Função sub_rlm pronta para injeção no namespace REPL.
    """
    # Unwrap RLMSession → RLM core.
    if not isinstance(getattr(parent, "depth", None), int):
        _inner = getattr(parent, "_rlm", None)
        if _inner is not None and isinstance(getattr(_inner, "depth", None), int):
            parent = _inner

    def sub_rlm(
        task: str,
        context: str = "",
        max_iterations: int = 8,
        timeout_s: float = 300.0,
        return_artifacts: bool = False,
        system_prompt: "str | None" = None,
        model: str | None = None,
        model_role: str = "worker",
        interaction_mode: str = "repl",
        _task_id: int | None = None,
        _cancel_event: "threading.Event | None" = None,
        _sibling_bus: "Any | None" = None,
        _sibling_branch_id: "int | None" = None,
    ) -> "str | SubRLMArtifactResult":
        """
        Executa uma sub-tarefa em uma instância RLM filha isolada.

        O LLM filho opera em namespace REPL separado — variáveis do pai não
        vazam para o filho, e vice-versa. O filho retorna um único valor de
        texto (a resposta final) que pode ser atribuído a uma variável REPL.

        Args:
            task: Descrição da sub-tarefa. Seja específico: inclua dados
                  inline (strings curtas) ou caminhos de arquivo.
            context: Contexto adicional para o filho (opcional). Inserido
                     como prefixo do prompt.
            max_iterations: Número máximo de iterações do filho. Default 8.
                            Reduza para tarefas simples (5-8), aumente para
                            análises complexas (20-30).
            timeout_s: Timeout hard em segundos. Se expirar, levanta
                       SubRLMTimeoutError. Default 300s.

        Returns:
            String com a resposta final do filho.

        Raises:
            SubRLMDepthError: Se depth + 1 >= max_depth (profundidade máxima).
            SubRLMTimeoutError: Se o filho não terminar em `timeout_s` segundos.
            SubRLMError: Para outros erros de execução do filho.

        Exemplos:
            # ETL pipeline
            dados = sub_rlm("Lê /tmp/vendas.csv, remove duplicatas, retorna CSV string")
            kpis = sub_rlm(f"Calcula total e top-5 deste CSV:\\n{dados[:2000]}")

            # Code review
            bugs = sub_rlm(f"Lista bugs em JSON:\\n```python\\n{codigo}\\n```")

            # Limitando custo
            resumo = sub_rlm("Resume em 3 frases: " + texto_longo, max_iterations=5)
        """
        # ── Depth guard ──────────────────────────────────────────────────────
        child_depth = parent.depth + 1
        if child_depth >= parent.max_depth:
            raise SubRLMDepthError(
                f"sub_rlm: profundidade máxima atingida "
                f"(depth={parent.depth}, max_depth={parent.max_depth}). "
                f"Aumente max_depth na instância RLM pai para permitir recursão mais profunda."
            )

        # ── Build prompt ─────────────────────────────────────────────────────
        strategy_context = _get_parent_active_recursive_strategy(parent) or {}
        recursive_guidance = _build_recursive_guidance_context(
            parent,
            strategy_context=strategy_context,
            branch_id=_sibling_branch_id,
            phase_label="serial_recursive_call",
        )
        full_prompt = _merge_context_fragments(context, recursive_guidance, task)

        subagent_mode = "parallel" if _sibling_branch_id is not None else "serial"
        task_preview = task[:160]
        runtime_task_id = _register_parent_subagent_task(
            parent,
            mode=subagent_mode,
            title=f"[{subagent_mode}] {task_preview}",
            branch_id=_sibling_branch_id,
            child_depth=child_depth,
            task_preview=task_preview,
            task_id=_task_id,
        )

        _record_parent_runtime_event(
            parent,
            "subagent.spawned",
            {
                "mode": subagent_mode,
                "task_preview": task_preview,
                "child_depth": child_depth,
                "branch_id": _sibling_branch_id,
                "task_id": runtime_task_id,
            },
        )
        if _sibling_bus is not None:
            _attach_parent_bus(parent, _sibling_bus)

        # ── Spawn child RLM ───────────────────────────────────────────────────
        env_kwargs = _prepare_child_env_kwargs(
            parent,
            sibling_bus=_sibling_bus,
            sibling_branch_id=_sibling_branch_id,
            cancel_event=_cancel_event,
        )

        child_model = resolve_subagent_model(
            parent,
            requested_model=model,
            model_role=model_role,
            child_depth=child_depth,
        )

        child = _spawn_child_rlm(
            parent,
            _rlm_cls=_rlm_cls,
            child_depth=child_depth,
            max_iterations=max_iterations,
            env_kwargs=env_kwargs,
            child_model=child_model,
            system_prompt=system_prompt,
            interaction_mode=interaction_mode,
            strategy_context=strategy_context,
        )

        # Lacuna 2: Propagar CancelToken do pai para filho serial
        _propagate_cancel_token(parent, child, _cancel_event)

        # ── Execute com timeout ───────────────────────────────────────────────
        result_holder: list[Any] = []
        error_holder: list[BaseException] = []
        t_start = time.perf_counter()

        def _run():
            try:
                if return_artifacts:
                    completion = child.completion(
                        full_prompt, root_prompt=task, capture_artifacts=True,
                    )
                    result_holder.append((completion.response, completion.artifacts or {}))
                else:
                    completion = child.completion(full_prompt, root_prompt=task)
                    result_holder.append(completion.response)
            except Exception as exc:  # noqa: BLE001
                error_holder.append(exc)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout_s)
        elapsed = time.perf_counter() - t_start

        if thread.is_alive():
            _record_parent_runtime_event(
                parent,
                "subagent.finished",
                {
                    "mode": "serial",
                    "task_preview": task_preview,
                    "child_depth": child_depth,
                    "branch_id": _sibling_branch_id,
                    "task_id": runtime_task_id,
                    "ok": False,
                    "timed_out": True,
                    "elapsed_s": elapsed,
                },
            )
            _update_parent_subagent_task(
                parent,
                task_id=runtime_task_id,
                branch_id=_sibling_branch_id,
                status="blocked",
                note=f"timeout after {elapsed:.2f}s",
                metadata={"timed_out": True, "elapsed_s": elapsed},
            )
            raise SubRLMTimeoutError(
                f"sub_rlm: filho não terminou em {timeout_s:.0f}s "
                f"(depth={child_depth}). "
                f"Aumente timeout_s ou reduza max_iterations."
            )

        if error_holder:
            exc = error_holder[0]
            _record_parent_runtime_event(
                parent,
                "subagent.finished",
                {
                    "mode": "serial",
                    "task_preview": task_preview,
                    "child_depth": child_depth,
                    "branch_id": _sibling_branch_id,
                    "task_id": runtime_task_id,
                    "ok": False,
                    "timed_out": False,
                    "elapsed_s": elapsed,
                    "error": str(exc),
                },
            )
            _update_parent_subagent_task(
                parent,
                task_id=runtime_task_id,
                branch_id=_sibling_branch_id,
                status="blocked",
                note=str(exc),
                metadata={"timed_out": False, "elapsed_s": elapsed, "error": str(exc)},
            )
            raise SubRLMError(
                f"sub_rlm: filho falhou (depth={child_depth}): {exc}"
            ) from exc

        if not result_holder:
            _record_parent_runtime_event(
                parent,
                "subagent.finished",
                {
                    "mode": "serial",
                    "task_preview": task_preview,
                    "child_depth": child_depth,
                    "branch_id": _sibling_branch_id,
                    "task_id": runtime_task_id,
                    "ok": False,
                    "timed_out": False,
                    "elapsed_s": elapsed,
                    "error": "empty result",
                },
            )
            _update_parent_subagent_task(
                parent,
                task_id=runtime_task_id,
                branch_id=_sibling_branch_id,
                status="blocked",
                note="empty result",
                metadata={"timed_out": False, "elapsed_s": elapsed, "error": "empty result"},
            )
            raise SubRLMError(
                f"sub_rlm: filho não retornou resposta (depth={child_depth})."
            )

        _record_parent_runtime_event(
            parent,
            "subagent.finished",
            {
                "mode": "serial",
                "task_preview": task_preview,
                "child_depth": child_depth,
                "branch_id": _sibling_branch_id,
                "task_id": runtime_task_id,
                "ok": True,
                "timed_out": False,
                "elapsed_s": elapsed,
            },
        )

        result_preview = result_holder[0][0] if return_artifacts else result_holder[0]
        result_text = str(result_preview)
        final_status = "cancelled" if result_text.startswith("[CANCELLED]") else "completed"
        _update_parent_subagent_task(
            parent,
            task_id=runtime_task_id,
            branch_id=_sibling_branch_id,
            status=final_status,
            note=result_text[:200],
            metadata={"timed_out": False, "elapsed_s": elapsed},
        )

        if return_artifacts:
            answer, arts = result_holder[0]
            return SubRLMArtifactResult(answer=answer, artifacts=arts, depth=child_depth)
        return result_holder[0]

    # Preservar metadados úteis para inspeção no REPL
    sub_rlm.__name__ = "sub_rlm"
    sub_rlm.__qualname__ = "sub_rlm"
    setattr(sub_rlm, "_parent_depth", parent.depth)
    setattr(sub_rlm, "_parent_max_depth", parent.max_depth)

    return sub_rlm  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# sub_rlm_async factory
# ---------------------------------------------------------------------------

def make_sub_rlm_async_fn(
    parent: "RLM",
    _rlm_cls: "type[RLM] | None" = None,
) -> "Any":
    """
    Retorna a função ``sub_rlm_async(task, ...)`` vinculada ao RLM pai.

    Fire-and-forget: inicia o filho em daemon thread e retorna um AsyncHandle
    imediatamente, sem bloquear o chamador.

    Bus P2P dinâmico
    ----------------
    Na primeira chamada, cria ``parent._async_bus`` (SiblingBus) e
    ``parent._async_branch_counter``. Nas chamadas seguintes — inclusive em
    turns distintos — reutiliza o mesmo bus. Isso garante que filhos lançados
    em momentos diferentes se vejam no mesmo barramento.

    Cada filho recebe ``sibling_publish/subscribe/peek/topics`` no seu REPL
    (mesmo caminho de sub_rlm_parallel). O pai recebe ``async_bus`` como
    objeto direto para publicar/ler sem intermediários.

    Deve ser injetada em ``environment.globals["sub_rlm_async"]``.
    """
    # ── Bus P2P persistente no pai ─────────────────────────────────────────
    if not hasattr(parent, "_async_bus") or parent._async_bus is None:
        from rlm.core.comms.sibling_bus import SiblingBus as _SiblingBus
        parent._async_bus = _SiblingBus()
        parent._async_branch_counter = 0
    _bus = parent._async_bus
    _attach_parent_bus(parent, _bus)

    def sub_rlm_async(
        task: str,
        context: str = "",
        max_iterations: int = 8,
        timeout_s: float = 300.0,
        model: str | None = None,
        model_role: str = "worker",
    ) -> AsyncHandle:
        """
        Inicia um filho RLM em background e retorna imediatamente um handle.

        Diferente de sub_rlm() que bloqueia até o resultado, sub_rlm_async()
        retorna um AsyncHandle enquanto o filho trabalha em paralelo.

        O filho pode publicar progresso chamando ``parent_log("msg")`` no seu
        REPL — ficam disponíveis via ``handle.log_poll()``.

        Args:
            task:           Descrição da sub-tarefa.
            context:        Contexto opcional prefixado no prompt.
            max_iterations: Iterações do filho. Default 8.
            timeout_s:      Timeout máximo do filho em segundos. Default 300s.

        Returns:
            AsyncHandle — não bloqueia. Use handle.result() quando precisar
            da resposta final.

        Exemplo no REPL::

            h1 = sub_rlm_async("Analisa /dados/jan.csv, retorna KPIs")
            h2 = sub_rlm_async("Analisa /dados/fev.csv, retorna KPIs")

            # h1 e h2 já estão rodando. O pai pode continuar:
            parent_log("Iniciados 2 processos de análise em paralelo")

            # Coletar quando pronto:
            jan = h1.result(timeout_s=300)
            fev = h2.result(timeout_s=300)
            FINAL_VAR("resultados")
        """
        child_depth = parent.depth + 1
        if child_depth >= parent.max_depth:
            raise SubRLMDepthError(
                f"sub_rlm_async: profundidade máxima atingida "
                f"(depth={parent.depth}, max_depth={parent.max_depth}). "
                f"Aumente max_depth na instância RLM pai."
            )

        full_prompt = (
            f"{context.rstrip()}\n\n{task.strip()}" if context else task.strip()
        )

        log_queue: "_queue_mod.Queue[str]" = _queue_mod.Queue()
        cancel_event = threading.Event()

        branch_id: int = parent._async_branch_counter
        parent._async_branch_counter += 1
        task_preview = task[:160]
        runtime_task_id = _register_parent_subagent_task(
            parent,
            mode="async",
            title=f"[async b{branch_id}] {task_preview}",
            branch_id=branch_id,
            child_depth=child_depth,
            task_preview=task_preview,
        )

        _record_parent_runtime_event(
            parent,
            "subagent.spawned",
            {
                "mode": "async",
                "task_preview": task_preview,
                "child_depth": child_depth,
                "branch_id": branch_id,
                "task_id": runtime_task_id,
            },
        )

        env_kwargs = _prepare_child_env_kwargs(
            parent,
            sibling_bus=_bus,
            sibling_branch_id=branch_id,
            cancel_event=cancel_event,
            log_queue=log_queue,
        )

        child_model = resolve_subagent_model(
            parent,
            requested_model=model,
            model_role=model_role,
            child_depth=child_depth,
        )

        child = _spawn_child_rlm(
            parent,
            _rlm_cls=_rlm_cls,
            child_depth=child_depth,
            max_iterations=max_iterations,
            env_kwargs=env_kwargs,
            child_model=child_model,
        )

        # Lacuna 2: Propagar CancelToken do pai para filho async
        _child_cts = _propagate_cancel_token(parent, child, cancel_event)

        result_holder: list[Any] = []
        error_holder: list[BaseException] = []

        def _run() -> None:
            try:
                completion = child.completion(full_prompt, root_prompt=task)
                result_holder.append(completion.response)
                _record_parent_runtime_event(
                    parent,
                    "subagent.finished",
                    {
                        "mode": "async",
                        "task_preview": task_preview,
                        "child_depth": child_depth,
                        "branch_id": branch_id,
                        "task_id": runtime_task_id,
                        "ok": True,
                        "timed_out": False,
                    },
                )
                result_text = str(completion.response)
                _update_parent_subagent_task(
                    parent,
                    task_id=runtime_task_id,
                    branch_id=branch_id,
                    status="cancelled" if result_text.startswith("[CANCELLED]") else "completed",
                    note=result_text[:200],
                )
            except Exception as exc:  # noqa: BLE001
                error_holder.append(exc)
                _record_parent_runtime_event(
                    parent,
                    "subagent.finished",
                    {
                        "mode": "async",
                        "task_preview": task_preview,
                        "child_depth": child_depth,
                        "branch_id": branch_id,
                        "task_id": runtime_task_id,
                        "ok": False,
                        "timed_out": False,
                        "error": str(exc),
                    },
                )
                _update_parent_subagent_task(
                    parent,
                    task_id=runtime_task_id,
                    branch_id=branch_id,
                    status="blocked",
                    note=str(exc),
                    metadata={"error": str(exc)},
                )

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"sub-rlm-async-b{branch_id}-d{child_depth}",
        )
        thread.start()

        return AsyncHandle(
            task=task,
            depth=child_depth,
            thread=thread,
            result_holder=result_holder,
            error_holder=error_holder,
            log_queue=log_queue,
            bus=_bus,
            branch_id=branch_id,
            cancel_event=cancel_event,
            cancel_token_source=_child_cts,
        )

    sub_rlm_async.__name__ = "sub_rlm_async"
    sub_rlm_async.__qualname__ = "sub_rlm_async"
    setattr(sub_rlm_async, "_parent_depth", parent.depth)
    setattr(sub_rlm_async, "_parent_max_depth", parent.max_depth)

    return sub_rlm_async  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Parallel execution — sub_rlm_parallel()
# ---------------------------------------------------------------------------

def make_sub_rlm_parallel_fn(
    parent: "RLM",
    _rlm_cls: "type[RLM] | None" = None,
) -> "Any":
    """
    Retorna a função `sub_rlm_parallel(tasks, ...)` vinculada ao RLM pai.

    Deve ser injetada em `environment.globals["sub_rlm_parallel"]`.

    Funcionamento
    -------------
    Recebe uma lista de N tarefas independentes e as executa TODAS em paralelo
    usando ThreadPoolExecutor — exatamente como o MCTSOrchestrator faz com
    ramos de exploração. Cada tarefa ganha sua própria instância RLM filha
    com namespace REPL isolado.

    Exemplo de uso no REPL:

        resultados = sub_rlm_parallel([
            "normaliza /tmp/jan.csv, retorna CSV string",
            "normaliza /tmp/fev.csv, retorna CSV string",
            "normaliza /tmp/mar.csv, retorna CSV string",
        ])
        # resultados[0], resultados[1], resultados[2] chegam juntos (~1× o tempo)

        # Verificar falhas individuais:
        for r in sub_rlm_parallel_detailed([...]):
            if not r.ok:
                print(f"ERRO branch {r.branch_id}: {r.error}")

    Args:
        parent:   instância RLM pai.
        _rlm_cls: injetável para testes (evita circular import).
    """
    _serial_fn = make_sub_rlm_fn(parent, _rlm_cls=_rlm_cls)

    # ── Lacuna 3: SiblingBus unificado parallel+async ─────────────────────
    from rlm.core.comms.sibling_bus import SiblingBus as _SiblingBus
    if not hasattr(parent, "_async_bus") or parent._async_bus is None:
        parent._async_bus = _SiblingBus()
        parent._async_branch_counter = 0
    _sibling_bus_instance = parent._async_bus
    _attach_parent_bus(parent, _sibling_bus_instance)

    # ------------------------------------------------------------------
    # Core shared: _run_parallel_core — implementação unificada
    # ------------------------------------------------------------------
    def _run_parallel_core(
        tasks: list[str],
        context: str,
        max_iterations: int,
        timeout_s: float,
        max_workers: int,
        return_artifacts: bool,
        coordination_policy: str | None,
        system_prompts: "list[str | None] | None",
        models: "list[str | None] | None",
        interaction_modes: "list[str | None] | None",
    ) -> SubRLMParallelDetailedResults:
        """Core unificado para sub_rlm_parallel e sub_rlm_parallel_detailed.

        Retorna sempre SubRLMParallelDetailedResults; o wrapper público
        extrai strings quando ``return_artifacts=False``.
        """
        if not tasks:
            return SubRLMParallelDetailedResults([])

        strategy_context = _resolve_parallel_strategy(
            parent,
            coordination_policy=coordination_policy,
        )
        resolved_policy = str(strategy_context["coordination_policy"])
        consensus_target = min(len(tasks), 2)

        batch_task_id = _ensure_parallel_batch_root(
            parent,
            task_count=len(tasks),
            coordination_policy=resolved_policy,
        )

        _record_parent_runtime_event(
            parent,
            "subagent.parallel_started",
            {
                "task_count": len(tasks),
                "max_workers": max_workers,
                "child_depth": parent.depth + 1,
                "batch_task_id": batch_task_id,
                "coordination_policy": resolved_policy,
                "strategy": strategy_context,
            },
        )

        child_depth = parent.depth + 1
        if child_depth >= parent.max_depth:
            raise SubRLMDepthError(
                f"sub_rlm_parallel: profundidade máxima atingida "
                f"(depth={parent.depth}, max_depth={parent.max_depth}). "
                f"Nenhuma das {len(tasks)} tarefas foi executada."
            )

        _n = min(len(tasks), max(1, max_workers))
        _task_count = len(tasks)  # FIX BUG #1: usar len(tasks) para arrays

        # Resultados rastreados per-branch
        detail_results: list[SubRLMParallelTaskResult | None] = [None] * _task_count
        cancel_events = {i: threading.Event() for i in range(_task_count)}
        branch_task_ids: dict[int, int | None] = {}
        winner_branch_id: int | None = None
        winner_lock = threading.Lock()
        successful_branch_ids: set[int] = set()
        consensus_signal_published = False

        for branch_id, task in enumerate(tasks):
            task_preview = task[:160]
            branch_task_ids[branch_id] = _register_parent_subagent_task(
                parent,
                mode="parallel",
                title=f"[parallel b{branch_id}] {task_preview}",
                branch_id=branch_id,
                child_depth=child_depth,
                task_preview=task_preview,
                parent_task_id=batch_task_id,
                metadata={
                    "coordination_policy": resolved_policy,
                    "strategy_name": strategy_context.get("strategy_name"),
                    "stop_condition": strategy_context.get("stop_condition"),
                    "repl_search_mode": strategy_context.get("repl_search_mode"),
                },
            )

        # ── Coordination infrastructure (shared, not duplicated) ──────────
        def _apply_stop_signal(source_branch_id: int | None, reason: str) -> None:
            for branch_id, cancel_event in cancel_events.items():
                if source_branch_id is not None and branch_id == source_branch_id:
                    continue
                cancel_event.set()
                _update_parent_subagent_task(
                    parent,
                    task_id=branch_task_ids.get(branch_id),
                    branch_id=branch_id,
                    status="cancelled",
                    note=reason,
                    metadata={"cancel_reason": reason},
                )

        def _coordination_observer(event: dict[str, Any]) -> None:
            nonlocal winner_branch_id
            if event.get("operation") != "control_publish":
                return
            signal_type = str(event.get("metadata", {}).get("semantic_type", "")).strip()
            if signal_type == "switch_strategy":
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    return
                action = str(payload.get("action") or "").strip()
                prioritized_branch_id = payload.get("prioritized_branch_id")
                if not isinstance(prioritized_branch_id, int):
                    return
                with winner_lock:
                    winner_branch_id = prioritized_branch_id
                if action in {"focus_branch", "fix_winner_branch"}:
                    _apply_stop_signal(prioritized_branch_id, str(payload.get("reason") or action))
                return
            if signal_type not in {"stop", "solution_found", "consensus_reached"}:
                return
            if signal_type == "solution_found" and resolved_policy != "stop_on_solution":
                return
            if signal_type == "consensus_reached" and resolved_policy != "consensus_reached":
                return
            sender_id = event.get("sender_id")
            payload = event.get("payload")
            _apply_stop_signal(sender_id if isinstance(sender_id, int) else None, str(payload))

        def _publish_control_signal(signal_type: str, payload: dict[str, Any], *, sender_id: int) -> None:
            publish_control = getattr(_sibling_bus_instance, "publish_control", None)
            if callable(publish_control):
                publish_control(
                    f"control/{signal_type}",
                    payload,
                    sender_id=sender_id,
                    signal_type=signal_type,
                )

        def _current_answers_errors() -> tuple[list[Any], list[str | None]]:
            """Extrai answers/errors atuais de detail_results."""
            answers = [item.answer if item is not None and item.ok else None for item in detail_results]
            errors = [item.error if item is not None else None for item in detail_results]
            return answers, errors

        def _register_success(branch_id: int) -> None:
            nonlocal winner_branch_id, consensus_signal_published
            with winner_lock:
                successful_branch_ids.add(branch_id)
                if winner_branch_id is None:
                    winner_branch_id = branch_id

                current_answers, current_errors = _current_answers_errors()
                heuristics = _compute_parallel_heuristics(
                    total_tasks=_task_count,
                    answers=current_answers,
                    errors=current_errors,
                )
                stop_state = _evaluate_stop_condition(
                    stop_condition=str(strategy_context.get("stop_condition", "")),
                    coordination_policy=resolved_policy,
                    heuristics=heuristics,
                )

                if resolved_policy == "stop_on_solution" and stop_state["reached"]:
                    if winner_branch_id == branch_id:
                        _publish_control_signal(
                            "solution_found",
                            {
                                "winning_branch_id": branch_id,
                                "winning_task_id": branch_task_ids.get(branch_id),
                                "policy": resolved_policy,
                                "strategy_name": strategy_context.get("strategy_name"),
                                "stop_evaluation": stop_state,
                            },
                            sender_id=branch_id,
                        )
                elif (
                    resolved_policy == "consensus_reached"
                    and not consensus_signal_published
                    and stop_state["reached"]
                ):
                    consensus_signal_published = True
                    _publish_control_signal(
                        "consensus_reached",
                        {
                            "winning_branch_id": winner_branch_id,
                            "consensus_branch_ids": sorted(successful_branch_ids),
                            "consensus_target": consensus_target,
                            "policy": resolved_policy,
                            "strategy_name": strategy_context.get("strategy_name"),
                            "stop_evaluation": stop_state,
                        },
                        sender_id=branch_id,
                    )

        add_observer = getattr(_sibling_bus_instance, "add_observer", None)
        remove_observer = getattr(_sibling_bus_instance, "remove_observer", None)
        if callable(add_observer):
            add_observer(_coordination_observer)

        # ── Normalize per-branch parameters ───────────────────────────────
        # FIX BUG #1 (CRÍTICO): arrays dimensionados por _task_count, não _n
        _sys_prompts: list[str | None] = [None] * _task_count
        if system_prompts is not None:
            for _sp_idx in range(min(len(system_prompts), _task_count)):
                _sys_prompts[_sp_idx] = system_prompts[_sp_idx]
        _interaction_modes: list[str] = ["repl"] * _task_count
        if interaction_modes is not None:
            for _mode_idx in range(min(len(interaction_modes), _task_count)):
                _mode = interaction_modes[_mode_idx]
                if _mode is not None:
                    _interaction_modes[_mode_idx] = _mode
        _models: list[str | None] = [None] * _task_count
        if models is not None:
            for _model_idx in range(min(len(models), _task_count)):
                _models[_model_idx] = models[_model_idx]

        # ── Run one task ──────────────────────────────────────────────────
        def _run_one(
            branch_id: int,
            task: str,
            extra_context: str = "",
        ) -> SubRLMParallelTaskResult:
            t0 = time.perf_counter()
            try:
                answer = _serial_fn(
                    task,
                    context=_merge_context_fragments(context, extra_context),
                    max_iterations=max_iterations,
                    timeout_s=timeout_s,
                    return_artifacts=return_artifacts,
                    system_prompt=_sys_prompts[branch_id],
                    model=_models[branch_id],
                    interaction_mode=_interaction_modes[branch_id],
                    _task_id=branch_task_ids.get(branch_id),
                    _cancel_event=cancel_events[branch_id],
                    _sibling_bus=_sibling_bus_instance,
                    _sibling_branch_id=branch_id,
                )
                if isinstance(answer, SubRLMArtifactResult):
                    answer_text = answer.answer
                else:
                    answer_text = str(answer)
                # Match original behavior: cancelled answers → error slot
                if answer_text.startswith("[CANCELLED"):
                    result = SubRLMParallelTaskResult(
                        task=task, branch_id=branch_id, ok=False,
                        answer=None,
                        error=f"[CANCELLED branch {branch_id}] {answer_text}",
                        elapsed_s=time.perf_counter() - t0,
                        task_id=branch_task_ids.get(branch_id),
                        parent_task_id=batch_task_id,
                        status="cancelled",
                    )
                    if return_artifacts and isinstance(answer, SubRLMArtifactResult):
                        result._artifact_obj = answer  # type: ignore[attr-defined]
                    detail_results[branch_id] = result
                    return result
                status = "completed"
                result = SubRLMParallelTaskResult(
                    task=task, branch_id=branch_id, ok=True,
                    answer=answer_text if not return_artifacts else answer_text,
                    error=None,
                    elapsed_s=time.perf_counter() - t0,
                    task_id=branch_task_ids.get(branch_id),
                    parent_task_id=batch_task_id,
                    status=status,
                )
                # Guardar artifact inteiro no _artifacts_store para extração posterior
                if return_artifacts and isinstance(answer, SubRLMArtifactResult):
                    result._artifact_obj = answer  # type: ignore[attr-defined]
                detail_results[branch_id] = result
                if status == "completed":
                    _register_success(branch_id)
                return result
            except SubRLMDepthError:
                raise
            except Exception as exc:
                error_text = str(exc)
                if cancel_events[branch_id].is_set():
                    error_text = f"[CANCELLED branch {branch_id}] {error_text}"
                    status = "cancelled"
                else:
                    error_text = f"[ERRO branch {branch_id}] {error_text}"
                    status = "blocked"
                result = SubRLMParallelTaskResult(
                    task=task, branch_id=branch_id, ok=False,
                    answer=None, error=error_text,
                    elapsed_s=time.perf_counter() - t0,
                    task_id=branch_task_ids.get(branch_id),
                    parent_task_id=batch_task_id,
                    status=status,
                )
                detail_results[branch_id] = result
                return result

        # ── Execute phase 1 ──────────────────────────────────────────────
        _total_timeout = timeout_s * _n + 60.0
        executor = _cf.ThreadPoolExecutor(
            max_workers=_n,
            thread_name_prefix="sub-rlm-parallel",
        )
        try:
            futures = {
                executor.submit(_run_one, i, task): i
                for i, task in enumerate(tasks)
            }
            try:
                completed_iter = _cf.as_completed(futures, timeout=_total_timeout)
                for future in completed_iter:
                    bid = futures[future]
                    try:
                        detail_results[bid] = future.result()
                    except SubRLMDepthError:
                        for f in futures:
                            f.cancel()
                        raise
                    except Exception as exc:
                        detail_results[bid] = SubRLMParallelTaskResult(
                            task=tasks[bid], branch_id=bid, ok=False,
                            answer=None, error=f"[ERRO branch {bid}] execução interna: {exc}",
                            elapsed_s=0.0,
                            task_id=branch_task_ids.get(bid),
                            parent_task_id=batch_task_id,
                            status="blocked",
                        )
            except _cf.TimeoutError:
                for f, bid in futures.items():
                    if not f.done():
                        detail_results[bid] = SubRLMParallelTaskResult(
                            task=tasks[bid], branch_id=bid, ok=False,
                            answer=None,
                            error=f"[ERRO branch {bid}] timeout total ({_total_timeout:.0f}s)",
                            elapsed_s=_total_timeout,
                            task_id=branch_task_ids.get(bid),
                            parent_task_id=batch_task_id,
                            status="blocked",
                        )
                        _update_parent_subagent_task(
                            parent,
                            task_id=branch_task_ids.get(bid),
                            branch_id=bid,
                            status="blocked",
                            note=f"timeout total ({_total_timeout:.0f}s)",
                        )
                        f.cancel()
        finally:
            if callable(remove_observer):
                remove_observer(_coordination_observer)
            executor.shutdown(wait=False, cancel_futures=True)

        # ── Phase 2: switch_strategy replan ──────────────────────────────
        phase_one_answers, phase_one_errors = _current_answers_errors()
        phase_one_stop_evaluation = _evaluate_stop_condition(
            stop_condition=str(strategy_context.get("stop_condition", "")),
            coordination_policy=resolved_policy,
            heuristics=_compute_parallel_heuristics(
                total_tasks=_task_count,
                answers=phase_one_answers,
                errors=phase_one_errors,
            ),
        )

        if resolved_policy == "switch_strategy" and phase_one_stop_evaluation["reached"]:
            replan_targets = [
                branch_id
                for branch_id, item in enumerate(detail_results)
                if branch_id != winner_branch_id and (item is None or not item.ok)
            ]
            if replan_targets:
                winner_text = None
                if winner_branch_id is not None and detail_results[winner_branch_id] is not None:
                    winner_text = detail_results[winner_branch_id].answer
                _publish_control_signal(
                    "switch_strategy",
                    {
                        "winning_branch_id": winner_branch_id,
                        "target_branch_ids": replan_targets,
                        "policy": resolved_policy,
                        "strategy_name": strategy_context.get("strategy_name"),
                        "stop_condition": strategy_context.get("stop_condition"),
                        "repl_search_mode": strategy_context.get("repl_search_mode"),
                        "stop_evaluation": phase_one_stop_evaluation,
                    },
                    sender_id=winner_branch_id or 0,
                )
                _record_parent_runtime_event(
                    parent,
                    "subagent.parallel_replanned",
                    {
                        "phase": 2,
                        "winner_branch_id": winner_branch_id,
                        "target_branch_ids": replan_targets,
                        "coordination_policy": resolved_policy,
                        "strategy": strategy_context,
                        "stop_evaluation": phase_one_stop_evaluation,
                    },
                )
                for branch_id in replan_targets:
                    cancel_events[branch_id] = threading.Event()
                    _update_parent_subagent_task(
                        parent,
                        task_id=branch_task_ids.get(branch_id),
                        branch_id=branch_id,
                        status="in-progress",
                        note="phase 2 replan",
                        metadata={
                            "phase": 2,
                            "coordination_policy": resolved_policy,
                            "strategy_name": strategy_context.get("strategy_name"),
                        },
                    )

                replan_contexts = {
                    branch_id: _build_recursive_guidance_context(
                        parent,
                        strategy_context=strategy_context,
                        branch_id=branch_id,
                        phase_label="parallel_phase_2_replan",
                        winner_text=winner_text,
                        stop_evaluation=phase_one_stop_evaluation,
                    )
                    for branch_id in replan_targets
                }

                replan_executor = _cf.ThreadPoolExecutor(
                    max_workers=max(1, min(_n, len(replan_targets))),
                    thread_name_prefix="sub-rlm-parallel-phase2",
                )
                try:
                    replan_futures = {
                        replan_executor.submit(
                            _run_one,
                            branch_id,
                            tasks[branch_id],
                            replan_contexts[branch_id],
                        ): branch_id
                        for branch_id in replan_targets
                    }
                    for future in _cf.as_completed(replan_futures, timeout=_total_timeout):
                        bid = replan_futures[future]
                        try:
                            replanned_result = future.result()
                            detail_results[bid] = replanned_result
                            if replanned_result.ok:
                                _register_success(bid)
                        except Exception as exc:
                            detail_results[bid] = SubRLMParallelTaskResult(
                                task=tasks[bid], branch_id=bid, ok=False,
                                answer=None, error=f"[ERRO branch {bid}] replan interno: {exc}",
                                elapsed_s=0.0,
                                task_id=branch_task_ids.get(bid),
                                parent_task_id=batch_task_id,
                                status="blocked",
                            )
                finally:
                    replan_executor.shutdown(wait=False, cancel_futures=True)

        # ── Finalize ─────────────────────────────────────────────────────
        final_results = [r for r in detail_results if r is not None]
        cancelled_count = sum(1 for r in final_results if r.status == "cancelled")
        failed_count = sum(1 for r in final_results if r.status == "blocked")

        final_answers, final_errors = _current_answers_errors()
        stop_evaluation = _evaluate_stop_condition(
            stop_condition=str(strategy_context.get("stop_condition", "")),
            coordination_policy=resolved_policy,
            heuristics=_compute_parallel_heuristics(
                total_tasks=_task_count,
                answers=final_answers,
                errors=final_errors,
            ),
        )

        _record_parent_runtime_event(
            parent,
            "subagent.parallel_finished",
            {
                "task_count": _task_count,
                "failed_count": failed_count,
                "cancelled_count": cancelled_count,
                "child_depth": parent.depth + 1,
                "winner_branch_id": winner_branch_id,
                "batch_task_id": batch_task_id,
                "coordination_policy": resolved_policy,
                "strategy": strategy_context,
                "stop_evaluation": stop_evaluation,
            },
        )

        _set_parent_parallel_summary(
            parent,
            winner_branch_id=winner_branch_id,
            cancelled_count=cancelled_count,
            failed_count=failed_count,
            total_tasks=_task_count,
            strategy=strategy_context,
            stop_evaluation=stop_evaluation,
        )
        _update_parent_subagent_task(
            parent,
            task_id=batch_task_id,
            branch_id=None,
            status="completed" if failed_count < _task_count else "blocked",
            note=f"winner={winner_branch_id}, cancelled={cancelled_count}, failed={failed_count}",
            metadata={
                "winner_branch_id": winner_branch_id,
                "cancelled_count": cancelled_count,
                "failed_count": failed_count,
                "coordination_policy": resolved_policy,
                "strategy_name": strategy_context.get("strategy_name"),
                "stop_evaluation": stop_evaluation,
            },
        )

        return SubRLMParallelDetailedResults(
            final_results,
            summary={
                "winner_branch_id": winner_branch_id,
                "cancelled_count": cancelled_count,
                "failed_count": failed_count,
                "task_ids_by_branch": {
                    str(branch_id): task_id
                    for branch_id, task_id in sorted(branch_task_ids.items())
                },
                "batch_task_id": batch_task_id,
                "strategy": strategy_context,
                "stop_evaluation": stop_evaluation,
            },
        )

    # ------------------------------------------------------------------
    # Public wrappers
    # ------------------------------------------------------------------

    def sub_rlm_parallel(
        tasks: "list[str]",
        context: str = "",
        max_iterations: int = 8,
        timeout_s: float = 60.0,
        max_workers: int = 5,
        return_artifacts: bool = False,
        coordination_policy: str | None = None,
        system_prompts: "list[str | None] | None" = None,
        models: "list[str | None] | None" = None,
        interaction_modes: "list[str | None] | None" = None,
    ) -> "list[str] | list[SubRLMArtifactResult]":
        """
        Executa N tarefas independentes em paralelo, retorna lista de respostas.

        As tarefas rodam simultaneamente em threads separadas. Cada uma tem
        seu próprio namespace REPL — sem compartilhamento de variáveis entre os
        filhos, e sem compartilhamento com o pai.

        Args:
            tasks:         Lista de strings, cada uma é uma sub-tarefa independente.
            context:       Contexto comum prefixado em todas as tarefas (opcional).
            max_iterations: Iterações máximas por tarefa filho. Default 8.
            timeout_s:     Timeout por tarefa (não total). Default 60s.
            max_workers:   Máximo de tarefas simultâneas. Default 5.
                           Cuidado: cada worker usa ~memória + 1 LM call ativo.
            return_artifacts: Se True, retorna list[SubRLMArtifactResult] em vez de list[str].

        Returns:
            Lista de strings na mesma ordem das tasks de entrada.
            Tarefas que falharam retornam a mensagem de erro prefixada com
            '[ERRO branch N] ' para que o LLM identifique e lide com falhas
            individuais sem quebrar as demais.
            Se return_artifacts=True, retorna list[SubRLMArtifactResult].

        Raises:
            SubRLMDepthError: Se depth já atingiu max_depth (nenhuma tarefa executa).
            SubRLMError: Se TODAS as tarefas falharem (all-fail é erro fatal).

        Exemplos:
            # ETL paralelo de 3 arquivos — leva tempo de 1 arquivo
            csvs = sub_rlm_parallel([
                "lê /tmp/jan.csv, remove NaN, retorna JSON",
                "lê /tmp/fev.csv, remove NaN, retorna JSON",
                "lê /tmp/mar.csv, remove NaN, retorna JSON",
            ])
            totais = sub_rlm(f"Agrega estes 3 JSONs e calcula totais: {csvs}")

            # Com timeout curto por tarefa
            resultados = sub_rlm_parallel(tasks, timeout_s=30.0, max_workers=3)

            # Lacuna 5: Artefatos em paralelo
            arts = sub_rlm_parallel(
                ["cria parse_log()", "cria validate_row()"],
                return_artifacts=True,
            )
        """
        if not tasks:
            return []

        detailed_results = _run_parallel_core(
            tasks=tasks,
            context=context,
            max_iterations=max_iterations,
            timeout_s=timeout_s,
            max_workers=max_workers,
            return_artifacts=return_artifacts,
            coordination_policy=coordination_policy,
            system_prompts=system_prompts,
            models=models,
            interaction_modes=interaction_modes,
        )

        if return_artifacts:
            result_arts: list[SubRLMArtifactResult] = []
            failed_count = 0
            for r in detailed_results:
                artifact_obj = getattr(r, "_artifact_obj", None)
                if r.ok and isinstance(artifact_obj, SubRLMArtifactResult):
                    result_arts.append(artifact_obj)
                elif r.ok and r.answer is not None:
                    result_arts.append(SubRLMArtifactResult(answer=r.answer, artifacts={}, depth=parent.depth + 1))
                else:
                    failed_count += 1
                    result_arts.append(SubRLMArtifactResult(
                        answer=r.error or f"[ERRO branch {r.branch_id}] sem resposta",
                        artifacts={}, depth=parent.depth + 1,
                    ))
            if failed_count == len(tasks):
                raise SubRLMError(
                    f"sub_rlm_parallel: todas as {len(tasks)} tarefas falharam."
                )
            return result_arts

        result: list[str] = []
        failed_count = 0
        for r in detailed_results:
            if r.ok and r.answer is not None:
                result.append(r.answer)
            else:
                if r.status != "cancelled":
                    failed_count += 1
                result.append(r.error or f"[ERRO branch {r.branch_id}] sem resposta")

        if failed_count == len(tasks):
            raise SubRLMError(
                f"sub_rlm_parallel: todas as {len(tasks)} tarefas falharam. "
                f"Verifique erros: {[r for r in result]}"
            )

        return result

    def sub_rlm_parallel_detailed(
        tasks: "list[str]",
        context: str = "",
        max_iterations: int = 8,
        timeout_s: float = 60.0,
        max_workers: int = 5,
        coordination_policy: str | None = None,
        models: "list[str | None] | None" = None,
    ) -> "SubRLMParallelDetailedResults":
        """
        Igual a sub_rlm_parallel(), mas retorna SubRLMParallelTaskResult por tarefa.
        Útil quando o LLM precisa inspecionar individualmente quais falharam e por quê.

        Exemplo:
            resultados = sub_rlm_parallel_detailed(["task A", "task B", "task C"])
            sucessos = [r for r in resultados if r.ok]
            falhas   = [r for r in resultados if not r.ok]
            for f in falhas:
                print(f"Branch {f.branch_id} falhou: {f.error}")
        """
        return _run_parallel_core(
            tasks=tasks,
            context=context,
            max_iterations=max_iterations,
            timeout_s=timeout_s,
            max_workers=max_workers,
            return_artifacts=False,
            coordination_policy=coordination_policy,
            system_prompts=None,
            models=models,
            interaction_modes=None,
        )

    sub_rlm_parallel.__name__ = "sub_rlm_parallel"
    setattr(sub_rlm_parallel, "_parent_depth", parent.depth)
    setattr(sub_rlm_parallel, "_parent_max_depth", parent.max_depth)

    sub_rlm_parallel_detailed.__name__ = "sub_rlm_parallel_detailed"
    setattr(sub_rlm_parallel_detailed, "_parent_depth", parent.depth)
    setattr(sub_rlm_parallel_detailed, "_parent_max_depth", parent.max_depth)

    return sub_rlm_parallel, sub_rlm_parallel_detailed
