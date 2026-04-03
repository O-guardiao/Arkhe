"""
_sub_rlm_helpers — Funções auxiliares privadas para sub_rlm.

Extraído de sub_rlm.py para separação de responsabilidades.
Contém:
- Helpers de interação com o parent RLM (registro de eventos, tarefas, bus)
- Funções de resolução de estratégia e coordenação
- Heurísticas de stop condition para execução paralela
- Construção de contexto recursivo e guidance MCTS
- Utilidades de formatação (preview, merge, extract)

Todos os símbolos são prefixados com _ (privados) — usados apenas por sub_rlm.py.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, TYPE_CHECKING

from rlm.core.engine._sub_rlm_types import SubRLMArtifactResult

if TYPE_CHECKING:
    from rlm.core.engine.rlm import RLM


# ---------------------------------------------------------------------------
# Parent environment access
# ---------------------------------------------------------------------------

def _get_parent_env(parent: "RLM") -> Any | None:
    return getattr(parent, "_persistent_env", None)


# ---------------------------------------------------------------------------
# Parent event / bus helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Parent task management
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Strategy resolution
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# String utilities
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# MCTS archive helpers
# ---------------------------------------------------------------------------

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
        from typing import cast
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


# ---------------------------------------------------------------------------
# Parallel heuristics & stop condition evaluation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Recursive guidance context builder
# ---------------------------------------------------------------------------

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
