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
"""
from __future__ import annotations

import queue as _queue_mod
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING, cast

from rlm.core.security.execution_policy import build_backend_kwargs, resolve_subagent_model
from rlm.core.types import ClientBackend, EnvironmentType

if TYPE_CHECKING:
    from rlm.core.engine.rlm import RLM


def _record_parent_runtime_event(
    parent: "RLM",
    event_type: str,
    data: dict[str, Any] | None = None,
) -> None:
    env = getattr(parent, "_persistent_env", None)
    recorder = getattr(env, "record_runtime_event", None)
    if callable(recorder):
        try:
            recorder(event_type, data, origin="sub_rlm")
        except Exception:
            pass

def _attach_parent_bus(parent: "RLM", bus: Any) -> None:
    env = getattr(parent, "_persistent_env", None)
    attach = getattr(env, "attach_sibling_bus", None)
    if callable(attach):
        try:
            attach(bus)
        except Exception:
            pass


def _get_parent_env(parent: "RLM") -> Any | None:
    return getattr(parent, "_persistent_env", None)


def _register_parent_subagent_task(
    parent: "RLM",
    *,
    mode: str,
    title: str,
    branch_id: int | None,
    child_depth: int,
    task_preview: str,
    task_id: int | None = None,
    parent_task_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> int | None:
    env = _get_parent_env(parent)
    if env is None:
        return task_id

    task_metadata = {
        "mode": mode,
        "branch_id": branch_id,
        "child_depth": child_depth,
        "task_preview": task_preview,
    }
    if metadata:
        task_metadata.update(dict(metadata))
    if task_id is not None:
        update = getattr(env, "update_subagent_task", None)
        if callable(update):
            try:
                update(task_id=task_id, branch_id=branch_id, metadata=task_metadata)
                return task_id
            except Exception:
                return task_id
        return task_id

    register = getattr(env, "register_subagent_task", None)
    if callable(register):
        try:
            task = register(
                mode=mode,
                title=title,
                branch_id=branch_id,
                parent_task_id=parent_task_id,
                metadata=task_metadata,
            )
            if isinstance(task, dict) and "task_id" in task:
                return int(task["task_id"])
            return None
        except Exception:
            return None
    return None


def _update_parent_subagent_task(
    parent: "RLM",
    *,
    task_id: int | None,
    branch_id: int | None,
    status: str,
    note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if task_id is None:
        return
    env = _get_parent_env(parent)
    if env is None:
        return
    update = getattr(env, "update_subagent_task", None)
    if callable(update):
        try:
            update(
                task_id=task_id,
                branch_id=branch_id,
                status=status,
                note=note,
                metadata=metadata,
            )
        except Exception:
            pass


def _ensure_parallel_batch_root(
    parent: "RLM",
    *,
    task_count: int,
    coordination_policy: str,
) -> int | None:
    env = _get_parent_env(parent)
    if env is None:
        return None
    create = getattr(env, "create_runtime_task", None)
    current_task_id = getattr(env, "current_runtime_task_id", None)
    if not callable(create):
        return None
    parent_task_id = current_task_id() if callable(current_task_id) else None
    try:
        task = create(
            f"[parallel batch] {task_count} branches",
            parent_task_id=parent_task_id,
            status="in-progress",
            metadata={
                "mode": "parallel_batch",
                "task_count": task_count,
                "coordination_policy": coordination_policy,
            },
            current=True,
        )
        if isinstance(task, dict) and "task_id" in task:
            return int(task["task_id"])
    except Exception:
        return None
    return None


def _set_parent_parallel_summary(
    parent: "RLM",
    *,
    winner_branch_id: int | None,
    cancelled_count: int,
    failed_count: int,
    total_tasks: int,
    strategy: dict[str, Any] | None = None,
    stop_evaluation: dict[str, Any] | None = None,
) -> None:
    env = _get_parent_env(parent)
    summary = getattr(env, "set_parallel_summary", None)
    if callable(summary):
        try:
            summary(
                winner_branch_id=winner_branch_id,
                cancelled_count=cancelled_count,
                failed_count=failed_count,
                total_tasks=total_tasks,
                strategy=strategy,
                stop_evaluation=stop_evaluation,
            )
        except Exception:
            pass


def _get_parent_active_recursive_strategy(parent: "RLM") -> dict[str, Any] | None:
    strategy = getattr(parent, "_active_recursive_strategy", None)
    if isinstance(strategy, dict) and strategy:
        return dict(strategy)
    env = _get_parent_env(parent)
    getter = getattr(env, "get_active_recursive_strategy", None)
    if callable(getter):
        try:
            value = getter()
        except Exception:
            return None
        if isinstance(value, dict) and value:
            return dict(value)
    return None


def _resolve_parallel_strategy(
    parent: "RLM",
    *,
    coordination_policy: str | None,
) -> dict[str, Any]:
    active_strategy = _get_parent_active_recursive_strategy(parent) or {}
    resolved_policy = str(
        coordination_policy
        or active_strategy.get("coordination_policy")
        or "stop_on_solution"
    ).strip() or "stop_on_solution"
    return {
        "strategy_name": active_strategy.get("strategy_name") or active_strategy.get("name"),
        "coordination_policy": resolved_policy,
        "stop_condition": active_strategy.get("stop_condition", ""),
        "repl_search_mode": active_strategy.get("repl_search_mode", ""),
        "meta_prompt": active_strategy.get("meta_prompt", ""),
        "archive_key": active_strategy.get("archive_key") or getattr(parent, "_active_mcts_archive_key", None),
        "source": "explicit" if coordination_policy else ("active_strategy" if active_strategy else "default"),
    }


def _string_preview(value: Any, *, limit: int = 180) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _extract_answer_text(answer: Any) -> str:
    if isinstance(answer, SubRLMArtifactResult):
        return str(answer.answer)
    return str(answer)


def _merge_context_fragments(*parts: str) -> str:
    chunks = [str(part).strip() for part in parts if str(part).strip()]
    return "\n\n".join(chunks)


def _get_parent_archive_snapshot(parent: "RLM", *, limit: int = 3) -> dict[str, Any] | None:
    strategy = _get_parent_active_recursive_strategy(parent) or {}
    archive_key = strategy.get("archive_key") or getattr(parent, "_active_mcts_archive_key", None)
    stores = [
        getattr(parent, "_mcts_archives", None),
        getattr(_get_parent_env(parent), "_mcts_archives", None),
    ]

    archive = None
    resolved_archive_key = archive_key
    for store in stores:
        if not isinstance(store, dict) or not store:
            continue
        if resolved_archive_key and resolved_archive_key in store:
            archive = store[resolved_archive_key]
            break
        if resolved_archive_key is None and len(store) == 1:
            resolved_archive_key, archive = next(iter(store.items()))
            break

    if archive is None:
        return None

    sample = getattr(archive, "sample", None)
    if not callable(sample):
        return None

    entries: list[dict[str, Any]] = []
    try:
        sampled = cast(list[Any], sample(limit))
    except Exception:
        return None

    for branch in sampled:
        entries.append(
            {
                "branch_id": getattr(branch, "branch_id", None),
                "strategy_name": getattr(branch, "strategy_name", None),
                "total_score": getattr(branch, "total_score", None),
                "final_code": _string_preview(getattr(branch, "final_code", ""), limit=140),
                "aggregated_metrics": dict(getattr(branch, "aggregated_metrics", {}) or {}),
            }
        )
    return {
        "archive_key": resolved_archive_key,
        "entries": entries,
    }


def _format_archive_guidance(archive_snapshot: dict[str, Any] | None) -> str:
    if not archive_snapshot or not archive_snapshot.get("entries"):
        return ""
    lines = [f"MCTS archive key: {archive_snapshot.get('archive_key')}"]
    for idx, entry in enumerate(archive_snapshot["entries"], start=1):
        lines.append(
            f"- Archive elite {idx}: strategy={entry.get('strategy_name') or 'unknown'}, "
            f"score={entry.get('total_score')}, hint={entry.get('final_code')}"
        )
    return "\n".join(lines)


def _compute_parallel_heuristics(
    *,
    total_tasks: int,
    answers: list[Any],
    errors: list[str | None],
) -> dict[str, Any]:
    success_texts = [
        _string_preview(_extract_answer_text(answer), limit=120).lower()
        for answer in answers
        if answer is not None
    ]
    success_count = len(success_texts)
    failed_count = sum(1 for err in errors if err is not None and not str(err).startswith("[CANCELLED branch"))
    cancelled_count = sum(1 for err in errors if err is not None and str(err).startswith("[CANCELLED branch"))
    unresolved_count = max(0, total_tasks - success_count - failed_count - cancelled_count)
    dominant_answer_ratio = 0.0
    if success_texts:
        dominant_answer_ratio = max(Counter(success_texts).values()) / max(1, success_count)
    completion_ratio = (success_count + failed_count + cancelled_count) / max(1, total_tasks)
    failure_ratio = failed_count / max(1, total_tasks)
    cancelled_ratio = cancelled_count / max(1, total_tasks)
    convergence_target = min(total_tasks, 2)
    converged = success_count >= convergence_target or dominant_answer_ratio >= 0.66
    stalled = success_count <= 1 and (failed_count + cancelled_count) >= max(1, total_tasks - success_count)
    return {
        "total_tasks": total_tasks,
        "success_count": success_count,
        "failed_count": failed_count,
        "cancelled_count": cancelled_count,
        "unresolved_count": unresolved_count,
        "completion_ratio": completion_ratio,
        "failure_ratio": failure_ratio,
        "cancelled_ratio": cancelled_ratio,
        "dominant_answer_ratio": dominant_answer_ratio,
        "convergence_target": convergence_target,
        "converged": converged,
        "stalled": stalled,
    }


def _infer_stop_condition_mode(stop_condition: str, coordination_policy: str) -> str:
    normalized = str(stop_condition or "").strip().lower()
    if coordination_policy == "switch_strategy" or any(token in normalized for token in ("stall", "stagn", "switch", "improv")):
        return "stagnation"
    if coordination_policy == "consensus_reached" or any(token in normalized for token in ("converg", "consensus", "agree")):
        return "convergence"
    return "first_success"


def _evaluate_stop_condition(
    *,
    stop_condition: str,
    coordination_policy: str,
    heuristics: dict[str, Any],
) -> dict[str, Any]:
    mode = _infer_stop_condition_mode(stop_condition, coordination_policy)
    if mode == "stagnation":
        reached = bool(heuristics.get("stalled"))
    elif mode == "convergence":
        reached = bool(heuristics.get("converged"))
    else:
        reached = int(heuristics.get("success_count", 0)) >= 1
    return {
        "mode": mode,
        "reached": reached,
        "heuristics": dict(heuristics),
    }


def _build_recursive_guidance_context(
    parent: "RLM",
    *,
    strategy_context: dict[str, Any] | None,
    branch_id: int | None,
    phase_label: str,
    winner_text: str | None = None,
    stop_evaluation: dict[str, Any] | None = None,
) -> str:
    archive_snapshot = _get_parent_archive_snapshot(parent)
    fragments: list[str] = []
    if strategy_context and any(strategy_context.get(key) for key in ("strategy_name", "coordination_policy", "stop_condition", "repl_search_mode")):
        fragments.append(
            "[RECURSIVE STRATEGY GUIDANCE]\n"
            f"Phase: {phase_label}\n"
            f"Branch: {branch_id if branch_id is not None else 'serial'}\n"
            f"Strategy: {strategy_context.get('strategy_name') or 'unknown'}\n"
            f"Coordination policy: {strategy_context.get('coordination_policy') or 'unknown'}\n"
            f"Stop condition: {strategy_context.get('stop_condition') or 'n/a'}\n"
            f"REPL search mode: {strategy_context.get('repl_search_mode') or 'n/a'}"
        )
    archive_guidance = _format_archive_guidance(archive_snapshot)
    if archive_guidance:
        fragments.append("[MCTS ARCHIVE GUIDANCE]\n" + archive_guidance)
    if winner_text:
        fragments.append("[WINNING EVIDENCE]\n" + _string_preview(winner_text, limit=220))
    if stop_evaluation:
        fragments.append(
            "[STOP HEURISTICS]\n"
            f"Mode: {stop_evaluation.get('mode')}\n"
            f"Reached: {stop_evaluation.get('reached')}\n"
            f"Heuristics: {_string_preview(stop_evaluation.get('heuristics', {}), limit=240)}"
        )
    return "\n\n".join(fragment for fragment in fragments if fragment)


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
# Core function factory
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
    # Unwrap RLMSession → RLM core.  No pipeline do servidor,
    # orchestrate_roles passa session.rlm_instance (RLMSession) mas
    # sub_rlm precisa do RLM real (.depth, .max_depth, .backend etc).
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
            max_iterations: Número máximo de iterações do filho. Default 15.
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
        import time

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
        # _rlm_cls permite injeção em testes sem circular import.
        if _rlm_cls is not None:
            _cls = _rlm_cls
        else:
            # Lazy import evita circular: rlm.py → sub_rlm.py → rlm.py
            from rlm.core.engine.rlm import RLM as _cls

        # ── Prepare env_kwargs (inject sibling bus when provided) ──────────
        _env_kwargs = parent.environment_kwargs.copy() if parent.environment_kwargs else {}
        if _sibling_bus is not None:
            _env_kwargs["_sibling_bus"] = _sibling_bus
            _env_kwargs["_sibling_branch_id"] = _sibling_branch_id
        if _cancel_event is not None:
            _env_kwargs["_cancel_event"] = _cancel_event

        # Lacuna 1: Compartilhar memória do pai com filhos
        _parent_memory = getattr(parent, "_shared_memory", None)
        if _parent_memory is None:
            # Tenta obter do environment persistente do pai
            _penv = getattr(parent, "_persistent_env", None)
            if _penv is not None:
                _parent_memory = getattr(_penv, "_memory", None)
        if _parent_memory is not None:
            _env_kwargs["_parent_memory"] = _parent_memory
            # Multichannel: propagar canal de origem do pai para filhos
            _parent_ctx = getattr(_parent_memory, "_agent_context", None)
            if _parent_ctx is not None and getattr(_parent_ctx, "channel", None) is not None:
                _env_kwargs["_originating_channel"] = _parent_ctx.channel

        child_model = resolve_subagent_model(
            parent,
            requested_model=model,
            model_role=model_role,
            child_depth=child_depth,
        )

        child = _cls(
            backend=cast(ClientBackend, parent.backend),
            backend_kwargs=build_backend_kwargs(parent.backend_kwargs, child_model),
            environment=cast(EnvironmentType, parent.environment_type),
            environment_kwargs=_env_kwargs if _env_kwargs else None,
            depth=child_depth,
            max_depth=parent.max_depth,
            max_iterations=max(1, min(max_iterations, 50)),  # clamp 1-50
            custom_system_prompt=system_prompt,  # Fix B: permite text-only agents
            interaction_mode=interaction_mode,
            verbose=False,   # filho silencioso por padrão
            event_bus=parent.event_bus,  # Lacuna 4: EventBus propaga
        )
        if strategy_context:
            child._active_recursive_strategy = dict(strategy_context)
            child._active_mcts_archive_key = strategy_context.get("archive_key")
        parent_archive_store = getattr(parent, "_mcts_archives", None)
        if isinstance(parent_archive_store, dict):
            child._mcts_archives = parent_archive_store

        # Lacuna 2: Propagar CancelToken do pai para filho serial
        _parent_token = getattr(parent, "_cancel_token", None)
        if _parent_token is not None and hasattr(_parent_token, "is_cancelled"):
            from rlm.core.lifecycle.cancellation import CancellationTokenSource
            _child_cts = CancellationTokenSource(parent=_parent_token)
            child._cancel_token = _child_cts.token
            # Bridge: token cancelado → threading.Event sinalizado no REPL
            if _cancel_event is not None:
                _child_cts.token.on_cancelled(lambda evt=_cancel_event: evt.set())

        # ── Execute com timeout ───────────────────────────────────────────────
        result_holder: list[Any] = []
        error_holder: list[BaseException] = []
        t_start = time.perf_counter()

        def _run():
            try:
                if return_artifacts:
                    completion = child.completion(full_prompt, capture_artifacts=True)
                    result_holder.append((completion.response, completion.artifacts or {}))
                else:
                    completion = child.completion(full_prompt)
                    result_holder.append(completion.response)
            except Exception as exc:  # noqa: BLE001
                error_holder.append(exc)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout_s)
        elapsed = time.perf_counter() - t_start

        if thread.is_alive():
            # Thread ainda rodando → timeout
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


# Alias de tipo para anotações externas
SubRLMCallable = Any


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
    # Criado na primeira chamada a make_sub_rlm_async_fn; reutilizado depois.
    if not hasattr(parent, "_async_bus") or parent._async_bus is None:
        from rlm.core.comms.sibling_bus import SiblingBus as _SiblingBus  # noqa: PLC0415
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

        if _rlm_cls is not None:
            _cls = _rlm_cls
        else:
            from rlm.core.engine.rlm import RLM as _cls

        # Canal filho → pai: filho escreve via parent_log(), pai lê via handle.log_poll()
        log_queue: "_queue_mod.Queue[str]" = _queue_mod.Queue()

        # Canal pai → filho: pai chama handle.cancel(), filho lê via check_cancel()
        cancel_event = threading.Event()

        # ID único deste filho na rede P2P — monotônico, nunca reutilizado
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

        _env_kwargs = parent.environment_kwargs.copy() if parent.environment_kwargs else {}
        _env_kwargs["_parent_log_queue"] = log_queue
        _env_kwargs["_cancel_event"] = cancel_event
        # Injeta bus compartilhado: filho recebe sibling_publish/subscribe/peek/topics
        # mesmo caminho de sub_rlm_parallel — sem nova infraestrutura
        _env_kwargs["_sibling_bus"] = _bus
        _env_kwargs["_sibling_branch_id"] = branch_id

        # Lacuna 1: Compartilhar memória do pai com filhos async
        _parent_memory = getattr(parent, "_shared_memory", None)
        if _parent_memory is None:
            _penv = getattr(parent, "_persistent_env", None)
            if _penv is not None:
                _parent_memory = getattr(_penv, "_memory", None)
        if _parent_memory is not None:
            _env_kwargs["_parent_memory"] = _parent_memory
            # Multichannel: propagar canal de origem do pai para filhos async
            _parent_ctx = getattr(_parent_memory, "_agent_context", None)
            if _parent_ctx is not None and getattr(_parent_ctx, "channel", None) is not None:
                _env_kwargs["_originating_channel"] = _parent_ctx.channel

        child_model = resolve_subagent_model(
            parent,
            requested_model=model,
            model_role=model_role,
            child_depth=child_depth,
        )

        child = _cls(
            backend=cast(ClientBackend, parent.backend),
            backend_kwargs=build_backend_kwargs(parent.backend_kwargs, child_model),
            environment=cast(EnvironmentType, parent.environment_type),
            environment_kwargs=_env_kwargs if _env_kwargs else None,
            depth=child_depth,
            max_depth=parent.max_depth,
            max_iterations=max(1, min(max_iterations, 50)),
            verbose=False,
            event_bus=parent.event_bus,  # Lacuna 4: EventBus propaga
        )

        # Lacuna 2: Propagar CancelToken do pai para filho async
        _child_cts: CancellationTokenSource | None = None
        _parent_token = getattr(parent, "_cancel_token", None)
        if _parent_token is not None and hasattr(_parent_token, "is_cancelled"):
            from rlm.core.lifecycle.cancellation import CancellationTokenSource
            _child_cts = CancellationTokenSource(parent=_parent_token)
            child._cancel_token = _child_cts.token
            # Bridge: token cancelado → threading.Event sinalizado no REPL
            _child_cts.token.on_cancelled(lambda: cancel_event.set())

        result_holder: list[Any] = []
        error_holder: list[BaseException] = []

        def _run() -> None:
            try:
                completion = child.completion(full_prompt)
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

import concurrent.futures as _cf


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
    # Cache da função serial para reusar a lógica de spawn/timeout
    _serial_fn = make_sub_rlm_fn(parent, _rlm_cls=_rlm_cls)

    # ── Lacuna 3: SiblingBus unificado parallel+async ─────────────────────
    # Reutiliza o bus do pai (criado por make_sub_rlm_async_fn) se existir.
    # Se não, cria e armazena no pai para que async o encontre depois.
    from rlm.core.comms.sibling_bus import SiblingBus as _SiblingBus  # noqa: PLC0415
    if not hasattr(parent, "_async_bus") or parent._async_bus is None:
        parent._async_bus = _SiblingBus()
        parent._async_branch_counter = 0
    _sibling_bus_instance = parent._async_bus
    _attach_parent_bus(parent, _sibling_bus_instance)

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
            max_iterations: Iterações máximas por tarefa filho. Default 15.
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
        import time as _time

        if not tasks:
            return []

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

        # Depth guard antecipado — antes de criar qualquer thread
        child_depth = parent.depth + 1
        if child_depth >= parent.max_depth:
            raise SubRLMDepthError(
                f"sub_rlm_parallel: profundidade máxima atingida "
                f"(depth={parent.depth}, max_depth={parent.max_depth}). "
                f"Nenhuma das {len(tasks)} tarefas foi executada."
            )

        _n = min(len(tasks), max(1, max_workers))
        answers: list[Any] = [None] * len(tasks)
        errors:  list[str | None] = [None] * len(tasks)
        cancel_events = {i: threading.Event() for i in range(len(tasks))}
        branch_task_ids: dict[int, int | None] = {}
        winner_branch_id: int | None = None
        winner_lock = threading.Lock()
        successful_branch_ids: set[int] = set()
        switch_signal_published = False
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

        def _register_success(branch_id: int) -> None:
            nonlocal winner_branch_id, consensus_signal_published
            with winner_lock:
                successful_branch_ids.add(branch_id)
                if winner_branch_id is None:
                    winner_branch_id = branch_id

                heuristics = _compute_parallel_heuristics(
                    total_tasks=len(tasks),
                    answers=answers,
                    errors=errors,
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

        def _run_one(
            branch_id: int,
            task: str,
            extra_context: str = "",
            system_prompt: "str | None" = None,
            model: str | None = None,
            interaction_mode: str = "repl",
        ) -> tuple[int, Any, str | None]:
            """Executa uma tarefa, retorna (branch_id, answer, error)."""
            try:
                answer = _serial_fn(
                    task,
                    context=_merge_context_fragments(context, extra_context),
                    max_iterations=max_iterations,
                    timeout_s=timeout_s,
                    return_artifacts=return_artifacts,
                    system_prompt=system_prompt,
                    model=model,
                    interaction_mode=interaction_mode,
                    _task_id=branch_task_ids.get(branch_id),
                    _cancel_event=cancel_events[branch_id],
                    _sibling_bus=_sibling_bus_instance,
                    _sibling_branch_id=branch_id,
                )
                answer_text = answer.answer if isinstance(answer, SubRLMArtifactResult) else str(answer)
                if answer_text.startswith("[CANCELLED]"):
                    return branch_id, None, f"[CANCELLED branch {branch_id}] {answer_text}"
                return branch_id, answer, None
            except SubRLMDepthError:
                raise  # propaga imediatamente — depth é fatal para todos
            except Exception as exc:
                return branch_id, None, f"[ERRO branch {branch_id}] {exc}"

        _total_timeout = timeout_s * _n + 60.0
        # Normalizar system_prompts: lista com 1 prompt por branch (ou None)
        _sys_prompts: list[str | None] = [None] * _n
        if system_prompts is not None:
            for _sp_idx in range(min(len(system_prompts), _n)):
                _sys_prompts[_sp_idx] = system_prompts[_sp_idx]
        _interaction_modes: list[str] = ["repl"] * _n
        if interaction_modes is not None:
            for _mode_idx in range(min(len(interaction_modes), _n)):
                _mode = interaction_modes[_mode_idx]
                if _mode is not None:
                    _interaction_modes[_mode_idx] = _mode
        _models: list[str | None] = [None] * _n
        if models is not None:
            for _model_idx in range(min(len(models), _n)):
                _models[_model_idx] = models[_model_idx]
        executor = _cf.ThreadPoolExecutor(
            max_workers=_n,
            thread_name_prefix="sub-rlm-parallel",
        )
        try:
            futures = {
                executor.submit(_run_one, i, task, "", _sys_prompts[i], _models[i], _interaction_modes[i]): i
                for i, task in enumerate(tasks)
            }
            try:
                completed_iter = _cf.as_completed(futures, timeout=_total_timeout)
                for future in completed_iter:
                    try:
                        bid, answer, err = future.result()
                        answers[bid] = answer
                        errors[bid]  = err
                        if err is None and answer is not None:
                            _register_success(bid)
                    except SubRLMDepthError:
                        for f in futures:
                            f.cancel()
                        raise
                    except Exception as exc:
                        bid = futures[future]
                        errors[bid] = f"[ERRO branch {bid}] execução interna: {exc}"
            except _cf.TimeoutError:
                # Tempo total excedido — registra as branches que não responderam
                for f, bid in futures.items():
                    if not f.done():
                        errors[bid] = f"[ERRO branch {bid}] timeout total ({_total_timeout:.0f}s)"
                        _update_parent_subagent_task(
                            parent,
                            task_id=branch_task_ids.get(bid),
                            branch_id=bid,
                            status="blocked",
                            note=errors[bid],
                        )
                        f.cancel()
        finally:
            if callable(remove_observer):
                remove_observer(_coordination_observer)
            executor.shutdown(wait=False, cancel_futures=True)

        phase_one_stop_evaluation = _evaluate_stop_condition(
            stop_condition=str(strategy_context.get("stop_condition", "")),
            coordination_policy=resolved_policy,
            heuristics=_compute_parallel_heuristics(
                total_tasks=len(tasks),
                answers=answers,
                errors=errors,
            ),
        )

        if resolved_policy == "switch_strategy" and phase_one_stop_evaluation["reached"]:
            replan_targets = [
                branch_id
                for branch_id, (answer, err) in enumerate(zip(answers, errors))
                if branch_id != winner_branch_id and (answer is None or err is not None)
            ]
            if replan_targets:
                switch_signal_published = True
                winner_text = None
                if winner_branch_id is not None and answers[winner_branch_id] is not None:
                    winner_text = _extract_answer_text(answers[winner_branch_id])
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
                            _sys_prompts[branch_id],
                            _models[branch_id],
                            _interaction_modes[branch_id],
                        ): branch_id
                        for branch_id in replan_targets
                    }
                    for future in _cf.as_completed(replan_futures, timeout=_total_timeout):
                        branch_id = replan_futures[future]
                        try:
                            bid, answer, err = future.result()
                            answers[bid] = answer
                            errors[bid] = err
                            if err is None and answer is not None:
                                _register_success(bid)
                        except Exception as exc:
                            errors[branch_id] = f"[ERRO branch {branch_id}] replan interno: {exc}"
                finally:
                    replan_executor.shutdown(wait=False, cancel_futures=True)

        # Montar resultado final
        if return_artifacts:
            # Lacuna 5: Retornar SubRLMArtifactResult list
            result_arts: list[SubRLMArtifactResult] = []
            failed_count = 0
            for i, (ans, err) in enumerate(zip(answers, errors)):
                if ans is not None and isinstance(ans, SubRLMArtifactResult):
                    result_arts.append(ans)
                elif ans is not None:
                    # Fallback: resposta string embrulhada em artifact vazio
                    result_arts.append(SubRLMArtifactResult(answer=str(ans), artifacts={}, depth=parent.depth + 1))
                else:
                    failed_count += 1
                    result_arts.append(SubRLMArtifactResult(
                        answer=err or f"[ERRO branch {i}] sem resposta",
                        artifacts={}, depth=parent.depth + 1,
                    ))
            if failed_count == len(tasks):
                raise SubRLMError(
                    f"sub_rlm_parallel: todas as {len(tasks)} tarefas falharam."
                )
            return result_arts

        result: list[str] = []
        failed_count = 0
        cancelled_count = 0
        for i, (ans, err) in enumerate(zip(answers, errors)):
            if ans is not None:
                result.append(ans)
            else:
                if err is not None and err.startswith("[CANCELLED branch"):
                    cancelled_count += 1
                elif err is not None:
                    failed_count += 1
                result.append(err or f"[ERRO branch {i}] sem resposta")

        if failed_count == len(tasks):
            raise SubRLMError(
                f"sub_rlm_parallel: todas as {len(tasks)} tarefas falharam. "
                f"Verifique erros: {[r for r in result]}"
            )

        stop_evaluation = _evaluate_stop_condition(
            stop_condition=str(strategy_context.get("stop_condition", "")),
            coordination_policy=resolved_policy,
            heuristics=_compute_parallel_heuristics(
                total_tasks=len(tasks),
                answers=answers,
                errors=errors,
            ),
        )

        _record_parent_runtime_event(
            parent,
            "subagent.parallel_finished",
            {
                "task_count": len(tasks),
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
            total_tasks=len(tasks),
            strategy=strategy_context,
            stop_evaluation=stop_evaluation,
        )
        _update_parent_subagent_task(
            parent,
            task_id=batch_task_id,
            branch_id=None,
            status="completed" if failed_count < len(tasks) else "blocked",
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

        return result

    def sub_rlm_parallel_detailed(
        tasks: "list[str]",
        context: str = "",
        max_iterations: int = 15,
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
        import time as _time

        if not tasks:
            return SubRLMParallelDetailedResults([])

        child_depth = parent.depth + 1
        if child_depth >= parent.max_depth:
            raise SubRLMDepthError(
                f"sub_rlm_parallel_detailed: profundidade máxima atingida "
                f"(depth={parent.depth}, max_depth={parent.max_depth})."
            )

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

        _n = min(len(tasks), max(1, max_workers))
        detail_results: list[SubRLMParallelTaskResult | None] = [None] * len(tasks)
        cancel_events = {i: threading.Event() for i in range(len(tasks))}
        branch_task_ids: dict[int, int | None] = {}
        winner_branch_id: int | None = None
        winner_lock = threading.Lock()
        successful_branch_ids: set[int] = set()
        switch_signal_published = False
        consensus_signal_published = False

        for branch_id, task in enumerate(tasks):
            task_preview = task[:160]
            branch_task_ids[branch_id] = _register_parent_subagent_task(
                parent,
                mode="parallel_detailed",
                title=f"[parallel detailed b{branch_id}] {task_preview}",
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

        def _register_success(branch_id: int) -> None:
            nonlocal winner_branch_id, consensus_signal_published
            with winner_lock:
                successful_branch_ids.add(branch_id)
                if winner_branch_id is None:
                    winner_branch_id = branch_id
                current_answers = [item.answer if item is not None and item.ok else None for item in detail_results]
                current_errors = [item.error if item is not None else None for item in detail_results]
                stop_state = _evaluate_stop_condition(
                    stop_condition=str(strategy_context.get("stop_condition", "")),
                    coordination_policy=resolved_policy,
                    heuristics=_compute_parallel_heuristics(
                        total_tasks=len(tasks),
                        answers=current_answers,
                        errors=current_errors,
                    ),
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

        def _run_one_detailed(branch_id: int, task: str, extra_context: str = "") -> SubRLMParallelTaskResult:
            import time as _t
            t0 = _t.perf_counter()
            try:
                answer = _serial_fn(
                    task,
                    context=_merge_context_fragments(context, extra_context),
                    max_iterations=max_iterations,
                    timeout_s=timeout_s,
                    _task_id=branch_task_ids.get(branch_id),
                    _cancel_event=cancel_events[branch_id],
                    _sibling_bus=_sibling_bus_instance,
                    _sibling_branch_id=branch_id,
                )
                answer_text = answer.answer if isinstance(answer, SubRLMArtifactResult) else str(answer)
                status = "cancelled" if answer_text.startswith("[CANCELLED]") else "completed"
                result = SubRLMParallelTaskResult(
                    task=task, branch_id=branch_id, ok=True,
                    answer=answer_text, error=None,
                    elapsed_s=_t.perf_counter() - t0,
                    task_id=branch_task_ids.get(branch_id),
                    parent_task_id=batch_task_id,
                    status=status,
                )
                detail_results[branch_id] = result
                if status == "completed":
                    _register_success(branch_id)
                return result
            except Exception as exc:
                error_text = str(exc)
                status = "cancelled" if error_text.startswith("[CANCELLED]") else "blocked"
                return SubRLMParallelTaskResult(
                    task=task, branch_id=branch_id, ok=False,
                    answer=None, error=error_text,
                    elapsed_s=_t.perf_counter() - t0,
                    task_id=branch_task_ids.get(branch_id),
                    parent_task_id=batch_task_id,
                    status=status,
                )

        _total_timeout = timeout_s * _n + 60.0
        executor = _cf.ThreadPoolExecutor(
            max_workers=_n,
            thread_name_prefix="sub-rlm-parallel-det",
        )
        try:
            futures = {
                executor.submit(_run_one_detailed, i, task): i
                for i, task in enumerate(tasks)
            }
            try:
                completed_iter = _cf.as_completed(futures, timeout=_total_timeout)
                for future in completed_iter:
                    bid = futures[future]
                    try:
                        detail_results[bid] = future.result()
                    except Exception as exc:
                        detail_results[bid] = SubRLMParallelTaskResult(
                            task=tasks[bid], branch_id=bid, ok=False,
                            answer=None, error=f"thread interna: {exc}",
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
                            error=f"timeout total ({_total_timeout:.0f}s)",
                            elapsed_s=_total_timeout,
                            task_id=branch_task_ids.get(bid),
                            parent_task_id=batch_task_id,
                            status="blocked",
                        )
                        f.cancel()
        finally:
            if callable(remove_observer):
                remove_observer(_coordination_observer)
            executor.shutdown(wait=False, cancel_futures=True)

        phase_one_stop_evaluation = _evaluate_stop_condition(
            stop_condition=str(strategy_context.get("stop_condition", "")),
            coordination_policy=resolved_policy,
            heuristics=_compute_parallel_heuristics(
                total_tasks=len(tasks),
                answers=[item.answer if item is not None and item.ok else None for item in detail_results],
                errors=[item.error if item is not None else None for item in detail_results],
            ),
        )

        if resolved_policy == "switch_strategy" and phase_one_stop_evaluation["reached"]:
            replan_targets = [
                branch_id
                for branch_id, item in enumerate(detail_results)
                if branch_id != winner_branch_id and (item is None or not item.ok)
            ]
            if replan_targets:
                switch_signal_published = True
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
                        phase_label="parallel_detailed_phase_2_replan",
                        winner_text=winner_text,
                        stop_evaluation=phase_one_stop_evaluation,
                    )
                    for branch_id in replan_targets
                }
                replan_executor = _cf.ThreadPoolExecutor(
                    max_workers=max(1, min(_n, len(replan_targets))),
                    thread_name_prefix="sub-rlm-parallel-det-phase2",
                )
                try:
                    replan_futures = {
                        replan_executor.submit(_run_one_detailed, branch_id, tasks[branch_id], replan_contexts[branch_id]): branch_id
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
                                answer=None, error=f"replan interno: {exc}",
                                elapsed_s=0.0,
                                task_id=branch_task_ids.get(bid),
                                parent_task_id=batch_task_id,
                                status="blocked",
                            )
                finally:
                    replan_executor.shutdown(wait=False, cancel_futures=True)

        final_results = [r for r in detail_results if r is not None]
        cancelled_count = sum(1 for r in final_results if r.status == "cancelled")
        failed_count = sum(1 for r in final_results if r.status == "blocked")
        stop_evaluation = _evaluate_stop_condition(
            stop_condition=str(strategy_context.get("stop_condition", "")),
            coordination_policy=resolved_policy,
            heuristics=_compute_parallel_heuristics(
                total_tasks=len(tasks),
                answers=[item.answer if item is not None and item.ok else None for item in detail_results],
                errors=[item.error if item is not None else None for item in detail_results],
            ),
        )

        _set_parent_parallel_summary(
            parent,
            winner_branch_id=winner_branch_id,
            cancelled_count=cancelled_count,
            failed_count=failed_count,
            total_tasks=len(tasks),
            strategy=strategy_context,
            stop_evaluation=stop_evaluation,
        )
        _update_parent_subagent_task(
            parent,
            task_id=batch_task_id,
            branch_id=None,
            status="completed" if failed_count < len(tasks) else "blocked",
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

    sub_rlm_parallel.__name__ = "sub_rlm_parallel"
    setattr(sub_rlm_parallel, "_parent_depth", parent.depth)
    setattr(sub_rlm_parallel, "_parent_max_depth", parent.max_depth)

    sub_rlm_parallel_detailed.__name__ = "sub_rlm_parallel_detailed"
    setattr(sub_rlm_parallel_detailed, "_parent_depth", parent.depth)
    setattr(sub_rlm_parallel_detailed, "_parent_max_depth", parent.max_depth)

    return sub_rlm_parallel, sub_rlm_parallel_detailed


# Public alias para testes
SubRLMParallelCallable = Any
