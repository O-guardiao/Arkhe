"""REPL tool factory for LocalREPL.

Creates all closure-based tools (task, attachment, timeline, recursive session,
critic_fuzz, mcts_explore, inter-process channels) bound to a LocalREPL
environment instance.

Each factory function captures the ``env`` reference so closures can call
``env.record_runtime_event()``, ``env._task_ledger``, etc. at call-time.

Extracted from local_repl.py during responsibility separation refactoring.
"""

from __future__ import annotations

import hashlib
import pathlib
from typing import Any, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from rlm.environments.local_repl import LocalREPL


# =========================================================================
# Scaffold Tools (task, attachment, timeline, recursive session)
# =========================================================================

def build_scaffold_tools(env: LocalREPL) -> dict[str, Any]:
    """Create all scaffold tool closures bound to *env*.

    Returns a dict of ``{name: callable}`` that will be injected into the
    REPL globals and tracked in ``_runtime_scaffold_refs`` for restoration.
    """

    # --- Task API -----------------------------------------------------------

    def task_create(
        title: str,
        parent: int | None = None,
        status: str = "not-started",
        note: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = env._task_ledger.create(
            title,
            parent_id=parent,
            status=status,
            note=note,
            metadata=metadata,
        )
        env.record_runtime_event(
            "task.created",
            {
                "task_id": entry["task_id"],
                "title": entry["title"],
                "status": entry["status"],
                "parent_id": entry["parent_id"],
            },
        )
        return entry

    def task_start(
        title: str,
        parent: int | None = None,
        note: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = env._task_ledger.start(title, parent_id=parent, note=note, metadata=metadata)
        env.record_runtime_event(
            "task.started",
            {
                "task_id": entry["task_id"],
                "title": entry["title"],
                "parent_id": entry["parent_id"],
            },
        )
        return entry

    def task_update(
        task_id: int,
        status: str | None = None,
        note: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        current: bool | None = None,
    ) -> dict[str, Any]:
        entry = env._task_ledger.update(
            task_id,
            status=status,
            note=note,
            title=title,
            metadata=metadata,
            current=current,
        )
        env.record_runtime_event(
            "task.updated",
            {
                "task_id": entry["task_id"],
                "status": entry["status"],
                "current": env._task_ledger.current(),
            },
        )
        return entry

    def task_list(status: str | None = None) -> list[dict[str, Any]]:
        return env._task_ledger.list(status)

    def task_current() -> dict[str, Any] | None:
        return env._task_ledger.current()

    def task_set_current(task_id: int | None) -> dict[str, Any] | None:
        entry = env._task_ledger.set_current(task_id)
        env.record_runtime_event(
            "task.current_changed",
            {"task_id": task_id, "current": entry},
        )
        return entry

    # --- Attachment API -------------------------------------------------------

    def attach_text(
        label: str,
        content: str,
        kind: str = "text",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attachment = env._context_attachments.add_text(
            label,
            content,
            kind=kind,
            metadata=metadata,
        )
        env.record_runtime_event(
            "attachment.added",
            {
                "attachment_id": attachment["attachment_id"],
                "kind": attachment["kind"],
                "label": attachment["label"],
            },
        )
        return attachment

    def attach_context(
        label: str,
        payload: Any,
        kind: str = "context",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attachment = env._context_attachments.add_context(
            label,
            payload,
            kind=kind,
            metadata=metadata,
        )
        env.record_runtime_event(
            "attachment.added",
            {
                "attachment_id": attachment["attachment_id"],
                "kind": attachment["kind"],
                "label": attachment["label"],
            },
        )
        return attachment

    def attach_file(
        path: str,
        label: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        str_path = str(path)
        env._auditor.check_path_access(str_path)
        target = pathlib.Path(str_path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"file not found: {target}")
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        s = max(1, int(start_line)) - 1 if start_line is not None else 0
        e = min(len(lines), int(end_line)) if end_line is not None else len(lines)
        content = "".join(lines[s:e])
        attachment = env._context_attachments.add_text(
            label or target.name,
            content,
            kind="file",
            metadata={
                **dict(metadata or {}),
                "start_line": s + 1,
                "end_line": e,
            },
            source_ref=str(target),
        )
        env.record_runtime_event(
            "attachment.added",
            {
                "attachment_id": attachment["attachment_id"],
                "kind": attachment["kind"],
                "label": attachment["label"],
                "source_ref": attachment["source_ref"],
            },
        )
        return attachment

    def attachment_list(
        kind: str | None = None,
        pinned_only: bool | None = None,
    ) -> list[dict[str, Any]]:
        return env._context_attachments.list(
            kind=kind,
            pinned_only=pinned_only,
            include_content=False,
        )

    def attachment_get(attachment_id: str) -> dict[str, Any] | None:
        return env._context_attachments.get(attachment_id, include_content=True)

    def attachment_pin(attachment_id: str, pinned: bool = True) -> dict[str, Any]:
        attachment = env._context_attachments.pin(attachment_id, pinned=pinned)
        env.record_runtime_event(
            "attachment.pinned",
            {
                "attachment_id": attachment_id,
                "pinned": pinned,
            },
        )
        return attachment

    # --- Timeline API --------------------------------------------------------

    def timeline_recent(limit: int = 20, event_type: str | None = None) -> list[dict[str, Any]]:
        return env._execution_timeline.recent(limit=limit, event_type=event_type)

    def timeline_mark(
        event_type: str,
        data: dict[str, Any] | None = None,
        origin: str = "manual",
    ) -> dict[str, Any]:
        return env.record_runtime_event(event_type, data, origin=origin)

    # --- Recursive Session API -----------------------------------------------

    def recursive_message(
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        branch_id: int | None = None,
    ) -> dict[str, Any]:
        return env.record_recursive_message(
            role,
            content,
            metadata=metadata,
            branch_id=branch_id,
        )

    def recursive_messages(
        limit: int = 20,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        return env.recent_recursive_messages(limit=limit, role=role)

    def recursive_event(
        event_type: str,
        payload: dict[str, Any] | None = None,
        branch_id: int | None = None,
        source: str = "runtime",
        visibility: str = "internal",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        return env.emit_recursive_event(
            event_type,
            payload=payload,
            branch_id=branch_id,
            source=source,
            visibility=visibility,
            correlation_id=correlation_id,
        )

    def recursive_events(
        limit: int = 20,
        event_type: str | None = None,
        branch_id: int | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        return env.recent_recursive_events(
            limit=limit,
            event_type=event_type,
            branch_id=branch_id,
            source=source,
        )

    def recursive_command(
        command_type: str,
        payload: dict[str, Any] | None = None,
        status: str = "queued",
        branch_id: int | None = None,
    ) -> dict[str, Any]:
        return env.queue_recursive_command(
            command_type,
            payload=payload,
            status=status,
            branch_id=branch_id,
        )

    def recursive_command_update(
        command_id: int,
        status: str,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return env.update_recursive_command(
            command_id,
            status=status,
            outcome=outcome,
        )

    def recursive_commands(
        limit: int = 20,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return env.recent_recursive_commands(limit=limit, status=status)

    def recursive_session_state() -> dict[str, Any]:
        return env.get_recursive_session_state()

    def active_recursive_strategy() -> dict[str, Any] | None:
        current = env.get_active_recursive_strategy()
        return dict(current) if current is not None else None

    return {
        "task_create": task_create,
        "task_start": task_start,
        "task_update": task_update,
        "task_list": task_list,
        "task_current": task_current,
        "task_set_current": task_set_current,
        "attach_text": attach_text,
        "attach_context": attach_context,
        "attach_file": attach_file,
        "attachment_list": attachment_list,
        "attachment_get": attachment_get,
        "attachment_pin": attachment_pin,
        "timeline_recent": timeline_recent,
        "timeline_mark": timeline_mark,
        "recursive_message": recursive_message,
        "recursive_messages": recursive_messages,
        "recursive_event": recursive_event,
        "recursive_events": recursive_events,
        "recursive_command": recursive_command,
        "recursive_command_update": recursive_command_update,
        "recursive_commands": recursive_commands,
        "recursive_session_state": recursive_session_state,
        "active_recursive_strategy": active_recursive_strategy,
    }


# =========================================================================
# Inter-process Tools (parent_log, check_cancel)
# =========================================================================

def build_interprocess_tools(env: LocalREPL) -> dict[str, Any]:
    """Create parent_log / check_cancel tools if applicable.

    Returns an empty dict when no inter-process channels are configured.
    """
    tools: dict[str, Any] = {}

    parent_log_queue = getattr(env, "_parent_log_queue", None)
    if parent_log_queue is not None:
        _pq = parent_log_queue

        def parent_log(msg: str) -> None:
            """Envia uma mensagem de progresso para o pai que iniciou este agente.

            Módulo fire-and-forget: nunca bloqueia. Se a fila estiver cheia,
            a mensagem é descartada silenciosamente (não interrompe a tarefa).

            Args:
                msg: Texto de progresso (ex: 'processando linha 42 de 1000').

            Uso::
                parent_log(f"Iniciando análise de {len(dados)} registros")
                # ... trabalho ...
                parent_log("Análise concluída, calculando KPIs")
            """
            try:
                _pq.put_nowait(str(msg))
            except Exception:  # fila cheia ou fechada — não trava o filho
                pass

        tools["parent_log"] = parent_log

    cancel_event = getattr(env, "_cancel_event", None)
    if cancel_event is not None:
        _ce = cancel_event

        def check_cancel() -> bool:
            """Verifica se o pai pediu cancelamento desta tarefa.

            Deve ser chamado periodicamente entre etapas longas.
            Retorna True quando o pai chamou ``handle.cancel()``.

            Uso recomendado::

                if check_cancel():
                    parent_log("cancelado pelo pai, encerrando limpo")
                    FINAL_VAR("cancelado")
            """
            return bool(_ce and _ce.is_set())

        tools["check_cancel"] = check_cancel

    return tools


# =========================================================================
# Critic Fuzz Tool
# =========================================================================

def build_critic_fuzz_tool(env: LocalREPL) -> Any:
    """Create the critic_fuzz REPL tool bound to *env*."""

    def _critic_fuzz(candidate_code: str, context: str = "", max_rounds: int = 3) -> dict:
        """Run adversarial fuzzing on candidate code.

        Args:
            candidate_code: Python code string to test.
            context: What the code is supposed to do.
            max_rounds: Max Adversary/Engineer rounds (default 3, controls cost).

        Returns:
            Dict with keys: winner, final_code, rounds, discovered_rules.
        """
        from rlm.tools.critic import run_critic_fuzzer
        import dataclasses

        memory_fn = env.globals.get("memory_analyze")
        report = run_critic_fuzzer(
            candidate_code=candidate_code,
            context=context,
            llm_query_fn=env._llm_query,
            execute_fn=env.execute_code,
            max_rounds=max_rounds,
            memory_analyze_fn=memory_fn,
        )
        # Return as plain dict for REPL readability
        return {
            "winner": report.winner,
            "final_code": report.final_code,
            "rounds": len(report.rounds),
            "discovered_rules": report.discovered_rules,
            "round_details": [dataclasses.asdict(r) for r in report.rounds],
        }

    return _critic_fuzz


# =========================================================================
# MCTS Explore Tool
# =========================================================================

def build_mcts_explore_tool(env: LocalREPL) -> Any:
    """Create the mcts_explore REPL tool bound to *env*."""

    def _mcts_explore(
        approaches: list[str],
        context: str = "",
        max_depth: int = 2,
        rounds: int = 1,
        archive_key: str | None = None,
        evaluators: list[str] | None = None,
        evaluator_weights: dict[str, float] | None = None,
        evaluator_thresholds: dict[str, float] | None = None,
    ) -> dict:
        """Run parallel MCTS exploration over candidate code approaches.

        Args:
            approaches: List of Python code strings (one per branch).
            context: Human-readable description of what the code tries to do.
            max_depth: Steps per branch before scoring (default 2).
            rounds: If > 1, refine candidates using elite feedback across rounds.
            archive_key: Optional stable key to reuse elites across calls.
            evaluators: Optional names of REPL callables used as problem-grounded evaluators.
            evaluator_weights: Optional per-evaluator weights.
            evaluator_thresholds: Optional per-evaluator prune thresholds.

        Returns:
            Dict with keys: winner_branch, winner_score, final_code,
                            seeded_vars (variables injected from winner into this REPL).

        Cost: 1 REPL execution per approach per depth step. No LLM calls.
        """
        from rlm.core.orchestration.mcts import EvaluationStage, MCTSOrchestrator, ProgramArchive, evolutionary_branch_search

        resolved_evaluators = list(evaluators or [])
        if not resolved_evaluators and callable(env.globals.get("evaluate")):
            resolved_evaluators = ["evaluate"]

        def _invoke_named_evaluator(name: str, snapshot: dict[str, Any]) -> float:
            fn = env.locals.get(name) or env.globals.get(name)
            if not callable(fn):
                raise ValueError(f"Evaluator '{name}' was not found as a callable in the REPL")
            try:
                value = fn(snapshot)
            except TypeError:
                value = fn(
                    snapshot["code"],
                    snapshot["stdout"],
                    snapshot["stderr"],
                    snapshot["locals"],
                )
            if isinstance(value, dict):
                if name in value:
                    return float(value[name])
                if len(value) == 1:
                    return float(next(iter(value.values())))
                raise ValueError(
                    f"Evaluator '{name}' returned multiple metrics; provide a wrapper that returns one scalar"
                )
            return float(cast(Any, value))

        stage_specs = []
        for evaluator_name in resolved_evaluators:
            stage_specs.append(
                EvaluationStage(
                    name=evaluator_name,
                    evaluator=lambda snapshot, stage_name=evaluator_name: _invoke_named_evaluator(stage_name, snapshot),
                    weight=float((evaluator_weights or {}).get(evaluator_name, 1.0)),
                    min_score=(evaluator_thresholds or {}).get(evaluator_name),
                )
            )

        orchestrator = MCTSOrchestrator(
            lm_handler_address=env.lm_handler_address,
            max_depth=max_depth,
            evaluation_stages=stage_specs,
        )
        archive_store = env._mcts_archives
        resolved_archive_key = archive_key or hashlib.sha1(
            (context or "|".join(approaches)).encode("utf-8", errors="ignore")
        ).hexdigest()[:16]
        archive = archive_store.setdefault(resolved_archive_key, ProgramArchive())

        def _replay_llm(_: str) -> str:
            return "\n---BRANCH---\n".join(approaches)

        if rounds > 1:
            search_result = evolutionary_branch_search(
                context or "MCTS REPL exploration",
                len(approaches),
                _replay_llm,
                orchestrator,
                rounds=rounds,
                elite_count=min(2, len(approaches)),
                archive=archive,
            )
            best = search_result["best_branch"]
            history = search_result["history"]
        else:
            branch_code_blocks = [[code] for code in approaches]
            best = orchestrator.run(branch_code_blocks)
            archive.update(orchestrator.top_results(include_pruned=False))
            history = []

        # Seed winner's variables into the current REPL namespace
        seeded = []
        for k, v in best.repl_locals.items():
            if not k.startswith("_"):
                env.locals[k] = v
                seeded.append(k)

        env.record_runtime_event(
            "mcts.archive.updated",
            {
                "archive_key": resolved_archive_key,
                "archive_size": archive.size(),
                "evaluators": resolved_evaluators,
                "winner_branch": best.branch_id,
                "winner_score": best.total_score,
            },
            origin="mcts",
        )

        return {
            "winner_branch": best.branch_id,
            "winner_score": best.total_score,
            "final_code": best.final_code,
            "seeded_vars": seeded,
            "all_branch_scores": {r.branch_id: r.total_score for r in orchestrator.top_results(include_pruned=True)},
            "history": history,
            "archive_key": resolved_archive_key,
            "archive_size": archive.size(),
            "evaluators": resolved_evaluators,
            "winner_metrics": best.aggregated_metrics,
        }

    return _mcts_explore
