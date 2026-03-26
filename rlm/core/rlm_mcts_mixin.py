"""
RLMMCTSMixin — exploração evolutiva por Monte Carlo Tree Search.

Responsabilidades extraídas de rlm.py:
- _build_mcts_evaluation_stages : constrói estágios de avaliação a partir do env (staticmethod)
- _attach_mcts_archive          : serializa resultado do MCTS no contexto do env (staticmethod)
- _set_active_mcts_strategy     : registra estratégia vencedora no estado do RLM
- _clear_active_mcts_strategy   : limpa estratégia ativa após completion
- _run_mcts_preamble            : executa pré-exploração MCTS e semeia namespace vencedor
"""
from __future__ import annotations

import hashlib
from typing import Any

from rlm.core.lm_handler import LMHandler
from rlm.environments import BaseEnv
from rlm.core.mcts import MCTSOrchestrator, ProgramArchive, evolutionary_branch_search


class RLMMCTSMixin:
    """
    Mixin com responsabilidades de exploração via MCTS antes do loop principal.

    Todos os atributos referenciados via ``self`` (event_bus, verbose, etc.)
    são definidos em ``RLM.__init__``. Este mixin é projetado para ser herdado
    exclusivamente pela classe ``RLM``.
    """

    @staticmethod
    def _build_mcts_evaluation_stages(environment: BaseEnv) -> list[Any]:
        from rlm.core.mcts import EvaluationStage

        evaluator_candidates: list[tuple[str, Any]] = []
        env_globals = getattr(environment, "globals", {}) or {}
        env_locals = getattr(environment, "locals", {}) or {}
        for name in ("evaluate", "score_candidate", "evaluate_candidate"):
            fn = env_locals.get(name) or env_globals.get(name)
            if callable(fn):
                evaluator_candidates.append((name, fn))

        stages: list[EvaluationStage] = []
        for evaluator_name, evaluator_fn in evaluator_candidates:
            def _invoke(snapshot: dict[str, Any], fn=evaluator_fn, name=evaluator_name) -> float:
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
                return float(value)

            stages.append(EvaluationStage(name=evaluator_name, evaluator=_invoke))
        return stages

    @staticmethod
    def _attach_mcts_archive(
        environment: BaseEnv,
        archive_key: str,
        archive: ProgramArchive,
        round_history: list[dict[str, Any]],
        best_branch: Any,
    ) -> None:
        attach_context = getattr(environment, "globals", {}).get("attach_context") if hasattr(environment, "globals") else None
        if not callable(attach_context):
            return
        try:
            attach_context(
                f"mcts_archive_{archive_key}",
                {
                    "archive_key": archive_key,
                    "archive_size": archive.size(),
                    "best_branch": {
                        "branch_id": best_branch.branch_id,
                        "total_score": best_branch.total_score,
                        "aggregated_metrics": dict(best_branch.aggregated_metrics),
                        "final_code": best_branch.final_code,
                        "strategy_name": best_branch.strategy_name,
                        "strategy": dict(best_branch.strategy or {}),
                    },
                    "archive_entries": [
                        {
                            "branch_id": branch.branch_id,
                            "total_score": branch.total_score,
                            "aggregated_metrics": dict(branch.aggregated_metrics),
                            "pruned_reason": branch.pruned_reason,
                            "final_code": branch.final_code,
                            "strategy_name": branch.strategy_name,
                            "strategy": dict(branch.strategy or {}),
                        }
                        for branch in archive.sample(6)
                    ],
                    "round_history": round_history,
                },
                kind="mcts_archive",
                metadata={"archive_key": archive_key, "rounds": len(round_history)},
            )
        except Exception:
            pass

    def _set_active_mcts_strategy(
        self,
        environment: BaseEnv,
        best_branch: Any,
        *,
        archive_key: str | None = None,
    ) -> None:
        strategy_payload = dict(best_branch.strategy or {})
        strategy_name = best_branch.strategy_name or strategy_payload.get("name")
        if strategy_name:
            strategy_payload.setdefault("strategy_name", strategy_name)
            strategy_payload.setdefault("name", strategy_name)
        if archive_key:
            strategy_payload.setdefault("archive_key", archive_key)
            self._active_mcts_archive_key = archive_key
        self._active_recursive_strategy = dict(strategy_payload) if strategy_payload else None
        setter = getattr(environment, "set_active_recursive_strategy", None)
        if callable(setter):
            try:
                setter(self._active_recursive_strategy, origin="mcts_winner")
            except Exception:
                pass

    def _clear_active_mcts_strategy(self, environment: BaseEnv) -> None:
        self._active_recursive_strategy = None
        self._active_mcts_archive_key = None
        clearer = getattr(environment, "clear_active_recursive_strategy", None)
        if callable(clearer):
            try:
                clearer(origin="completion")
            except Exception:
                pass

    def _run_mcts_preamble(
        self,
        prompt: Any,
        mcts_branches: int,
        lm_handler: LMHandler,
        environment: BaseEnv,
        message_history: list[dict[str, Any]],
    ) -> None:
        """Executa MCTS pre-exploration e injeta resultado no message_history."""
        _mcts_prompt = prompt if isinstance(prompt, str) else str(prompt)
        archive_key = hashlib.sha1(
            _mcts_prompt.strip().encode("utf-8", errors="ignore")
        ).hexdigest()[:16]
        archive_store = getattr(self, "_mcts_archives", None)
        if archive_store is None:
            archive_store = {}
            self._mcts_archives = archive_store
        archive = archive_store.setdefault(archive_key, ProgramArchive())

        _mcts_extra = {}
        if hasattr(environment, "globals"):
            for _tool_name in (
                "sub_rlm", "rlm_query", "sub_rlm_parallel",
                "sub_rlm_parallel_detailed", "rlm_query_batched",
                "sub_rlm_async", "async_bus", "AsyncHandle",
                "SubRLMParallelTaskResult",
            ):
                if _tool_name in environment.globals:
                    _mcts_extra[_tool_name] = environment.globals[_tool_name]

        orchestrator = MCTSOrchestrator(
            lm_handler_address=lm_handler.address if hasattr(lm_handler, "address") else None,
            branches=mcts_branches,
            max_depth=2,
            evaluation_stages=self._build_mcts_evaluation_stages(environment),
            event_bus=self.event_bus,
            extra_globals=_mcts_extra,
        )

        def _direct_llm(p: str) -> str:
            return lm_handler.completion(p)

        try:
            search_result = evolutionary_branch_search(
                _mcts_prompt, mcts_branches, _direct_llm, orchestrator,
                rounds=2, elite_count=min(2, mcts_branches), archive=archive,
            )
            best_branch = search_result["best_branch"]
            round_history = search_result["history"]
            self._attach_mcts_archive(environment, archive_key, archive, round_history, best_branch)
            self._set_active_mcts_strategy(environment, best_branch, archive_key=archive_key)

            if hasattr(environment, "locals") and best_branch.repl_locals:
                for k, v in best_branch.repl_locals.items():
                    if not k.startswith("_"):
                        environment.locals[k] = v

            if self.event_bus:
                self.event_bus.emit("mcts_complete", {
                    "best_branch": best_branch.branch_id,
                    "best_score": best_branch.total_score,
                    "seeded_vars": list(best_branch.repl_locals.keys()),
                    "rounds": len(round_history),
                    "winner_metrics": best_branch.aggregated_metrics,
                    "archive_key": archive_key,
                    "archive_size": archive.size(),
                })

            best_round = max(round_history, key=lambda item: item["best_score"])
            mcts_note = (
                f"\n[MCTS PRE-EXPLORATION: Ran {mcts_branches} parallel branches across "
                f"{len(round_history)} rounds. Best branch (score={best_branch.total_score:.1f}, "
                f"round={best_round['round']}, strategy={best_branch.strategy_name or 'unknown'}) found: "
                f"{best_branch.final_code[:200]}]"
            )
            message_history[-1]["content"] += mcts_note

        except Exception as _mcts_err:
            if self.verbose.enabled:
                print(f"[MCTS] Pre-exploration failed: {_mcts_err}. Continuing normally.")
