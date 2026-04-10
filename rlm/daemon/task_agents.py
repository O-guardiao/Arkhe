from __future__ import annotations

from typing import Any, Protocol, cast

from rlm.core.security.execution_policy import resolve_subagent_model
from rlm.daemon.contracts import DaemonTaskRequest, DaemonTaskResult, TaskDispatchRoute


_INTERNAL_TEXT_ROLES = {
    "worker",
    "fast",
    "response",
    "simple",
    "simple_inspect",
    "micro",
    "minirepl",
}

# Roles where a single-shot LLM call is sufficient even without
# explicit interaction_mode="text".  Used by sub_rlm to auto-divert
# to the daemon task path when the caller forgot to set text mode
# but the role clearly doesn't need REPL iteration.
_AUTO_DIVERT_TEXT_ROLES = frozenset({
    "fast",
    "response",
    "simple",
    "simple_inspect",
    "micro",
    "minirepl",
    "evaluator",
})


class _WarmRuntimeClient(Protocol):
    model_name: str | None

    def completion(self, prompt: str) -> str: ...


class _WarmRuntimeHandler(Protocol):
    def get_client(self, model_name: str | None, *, depth: int) -> _WarmRuntimeClient: ...


class _WarmRuntimeOwner(Protocol):
    depth: int

    def ensure_warm_runtime(self) -> tuple[_WarmRuntimeHandler, Any]: ...


class TaskAgentRouter:
    """Decide quando uma subtarefa pode evitar spawn de child RLM."""

    def classify(self, request: DaemonTaskRequest) -> TaskDispatchRoute:
        normalized_role = request.model_role.strip().lower()
        text_only = bool(request.metadata.get("text_only"))
        return_artifacts = bool(request.metadata.get("return_artifacts"))

        if normalized_role == "planner" and text_only and not return_artifacts:
            return "internal_planner"
        if normalized_role == "evaluator" and not return_artifacts:
            return "internal_evaluator"
        if normalized_role in _INTERNAL_TEXT_ROLES and text_only and not return_artifacts:
            return "internal_text_worker"
        return "spawn_child_rlm"


class TextWorkerTaskAgent:
    """Executor leve para tarefas textuais sem REPL filho."""

    def run(self, owner: Any, request: DaemonTaskRequest) -> DaemonTaskResult:
        return self._run_with_route(owner, request, route="internal_text_worker")

    def _run_with_route(self, owner: Any, request: DaemonTaskRequest, *, route: TaskDispatchRoute) -> DaemonTaskResult:
        ensure_warm_runtime = getattr(owner, "ensure_warm_runtime", None)
        if not callable(ensure_warm_runtime):
            raise RuntimeError("TextWorkerTaskAgent requires an owner with ensure_warm_runtime().")

        runtime_owner = cast(_WarmRuntimeOwner, owner)
        lm_handler, _environment = runtime_owner.ensure_warm_runtime()
        owner_depth = int(getattr(runtime_owner, "depth", 0))
        model_name = resolve_subagent_model(
            runtime_owner,
            requested_model=request.model,
            model_role=request.model_role,
            child_depth=owner_depth + 1,
        )
        client = lm_handler.get_client(model_name, depth=owner_depth + 1)
        prompt = self._build_prompt(request)
        response = client.completion(prompt)
        return DaemonTaskResult(
            route=route,
            response=response,
            metadata={
                "model_name": client.model_name,
                "model_role": request.model_role,
            },
        )

    def _build_prompt(self, request: DaemonTaskRequest) -> str:
        sections: list[str] = []
        if request.context.strip():
            sections.append(request.context.strip())
        sections.append(request.task.strip())
        return "\n\n".join(section for section in sections if section)


class EvaluatorTaskAgent(TextWorkerTaskAgent):
    """Executor leve para avaliações textuais sem REPL filho."""

    def run(self, owner: Any, request: DaemonTaskRequest) -> DaemonTaskResult:
        return self._run_with_route(owner, request, route="internal_evaluator")


class PlannerTaskAgent(TextWorkerTaskAgent):
    """Executor leve para planejamento textual sem REPL filho."""

    def run(self, owner: Any, request: DaemonTaskRequest) -> DaemonTaskResult:
        return self._run_with_route(owner, request, route="internal_planner")


__all__ = ["EvaluatorTaskAgent", "PlannerTaskAgent", "TaskAgentRouter", "TextWorkerTaskAgent", "_AUTO_DIVERT_TEXT_ROLES"]