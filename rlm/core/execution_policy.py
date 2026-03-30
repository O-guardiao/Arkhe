from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ModelRouteConfig:
    planner_model: str
    worker_model: str
    evaluator_model: str
    fast_model: str
    minirepl_model: str


@dataclass(frozen=True, slots=True)
class RuntimeExecutionPolicy:
    task_class: str
    allow_recursion: bool
    allow_role_orchestrator: bool
    max_iterations_override: int | None = None
    root_model_override: str | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class ModelPrice:
    model_name: str
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


@dataclass(frozen=True, slots=True)
class CostSlice:
    label: str
    model_name: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    calls: int = 1


SIMPLE_VERBS = (
    "verifique",
    "verificar",
    "liste",
    "listar",
    "mostre",
    "mostrar",
    "conte",
    "contar",
    "quantos",
    "quantas",
    "quais",
    "qual",
    "cheque",
    "confirme",
)

SIMPLE_TARGETS = (
    "diretor",
    "memoria",
    "memorias",
    "sessao",
    "sessoes",
    "session",
    "state_dir",
    "memory.db",
    "memory status",
    "status",
    "caminho",
    "path",
    "unificad",
    "global",
    "total",
)

COMPLEX_MARKERS = (
    "implemente",
    "implementar",
    "refatore",
    "refatorar",
    "arquitetura",
    "migr",
    "pesquise",
    "pesquisar",
    "analise profunda",
    "compare arquiteturas",
    "workflow",
    "pipeline",
    "sub_rlm",
    "subagent",
    "agente",
    "parallel",
    "paralel",
    "recurs",
    "planeje",
    "projeto",
    "codigo",
)

SIMPLE_SKILL_NAMES = {
    "filesystem",
    "sqlite",
    "telegram_get_updates",
}


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return normalized.lower()


def get_model_route_config(default_model: str | None = None) -> ModelRouteConfig:
    planner_model = (
        os.environ.get("RLM_MODEL_PLANNER")
        or os.environ.get("RLM_MODEL")
        or default_model
        or "gpt-4o-mini"
    )
    worker_model = (
        os.environ.get("RLM_MODEL_WORKER")
        or os.environ.get("RLM_SUBAGENT_MODEL")
        or planner_model
    )
    evaluator_model = os.environ.get("RLM_MODEL_EVALUATOR") or worker_model
    fast_model = os.environ.get("RLM_MODEL_FAST") or os.environ.get("RLM_FAST_MODEL") or worker_model
    minirepl_model = os.environ.get("RLM_MODEL_MINIREPL") or os.environ.get("RLM_MINIREPL_MODEL") or fast_model
    return ModelRouteConfig(
        planner_model=planner_model,
        worker_model=worker_model,
        evaluator_model=evaluator_model,
        fast_model=fast_model,
        minirepl_model=minirepl_model,
    )


def infer_runtime_execution_policy(
    query_text: str,
    *,
    client_id: str = "",
    prompt_plan: Any | None = None,
    default_model: str | None = None,
) -> RuntimeExecutionPolicy:
    text = _normalize_text(query_text.strip())
    routes = get_model_route_config(default_model)
    if not text:
        return RuntimeExecutionPolicy(
            task_class="default",
            allow_recursion=True,
            allow_role_orchestrator=True,
            note="empty_query",
        )

    expanded_skills = _extract_skill_names(prompt_plan)
    has_simple_verb = any(verb in text for verb in SIMPLE_VERBS)
    has_simple_target = any(target in text for target in SIMPLE_TARGETS)
    has_complex_marker = any(marker in text for marker in COMPLEX_MARKERS)
    simple_skill_set = bool(expanded_skills) and expanded_skills.issubset(SIMPLE_SKILL_NAMES)
    asks_for_short_check = has_simple_verb and has_simple_target

    if (asks_for_short_check or simple_skill_set) and not has_complex_marker:
        return RuntimeExecutionPolicy(
            task_class="simple_inspect",
            allow_recursion=False,
            allow_role_orchestrator=False,
            max_iterations_override=3,
            root_model_override=routes.fast_model,
            note="simple local verification path",
        )

    return RuntimeExecutionPolicy(
        task_class="default",
        allow_recursion=True,
        allow_role_orchestrator=True,
        note="full recursive runtime path",
    )


def resolve_subagent_model(
    parent: Any,
    *,
    requested_model: str | None = None,
    model_role: str = "worker",
    child_depth: int | None = None,
) -> str | None:
    if requested_model:
        return requested_model

    parent_kwargs = getattr(parent, "backend_kwargs", None) or {}
    default_model = parent_kwargs.get("model_name")
    routes = get_model_route_config(str(default_model) if default_model is not None else None)

    if child_depth == 1:
        other_kwargs = getattr(parent, "other_backend_kwargs", None) or []
        if other_kwargs:
            model_name = (other_kwargs[0] or {}).get("model_name")
            if model_name:
                return str(model_name)

    normalized_role = _normalize_text(model_role)
    if normalized_role == "planner":
        return routes.planner_model
    if normalized_role == "evaluator":
        return routes.evaluator_model
    if normalized_role in {"fast", "response", "simple", "simple_inspect"}:
        return routes.fast_model
    if normalized_role in {"minirepl", "micro"}:
        return routes.minirepl_model
    return routes.worker_model


def build_backend_kwargs(base_kwargs: dict[str, Any] | None, model_name: str | None) -> dict[str, Any] | None:
    updated = dict(base_kwargs or {})
    if model_name:
        updated["model_name"] = model_name
    return updated or None


def parse_price_table(markdown_text: str) -> dict[str, ModelPrice]:
    prices: dict[str, ModelPrice] = {}
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("model/"):
            continue
        match = re.match(r"^([^/]+)/\s*\$?([0-9.]+)/\s*\$?([0-9.]+)/\s*\$?([0-9.]+)/", line)
        if not match:
            continue
        model_name = match.group(1).strip()
        prices[model_name] = ModelPrice(
            model_name=model_name,
            input_per_million=float(match.group(2)),
            cached_input_per_million=float(match.group(3)),
            output_per_million=float(match.group(4)),
        )
    return prices


def estimate_cost_for_slice(prices: dict[str, ModelPrice], slice_spec: CostSlice) -> float:
    price = prices[slice_spec.model_name]
    uncached_input_tokens = max(0, int(slice_spec.input_tokens) - int(slice_spec.cached_input_tokens))
    per_call = (
        (uncached_input_tokens / 1_000_000.0) * price.input_per_million
        + (int(slice_spec.cached_input_tokens) / 1_000_000.0) * price.cached_input_per_million
        + (int(slice_spec.output_tokens) / 1_000_000.0) * price.output_per_million
    )
    return per_call * max(1, int(slice_spec.calls))


def estimate_architecture_cost(prices: dict[str, ModelPrice], slices: Iterable[CostSlice]) -> float:
    return sum(estimate_cost_for_slice(prices, slice_spec) for slice_spec in slices)


def _extract_skill_names(prompt_plan: Any | None) -> set[str]:
    if prompt_plan is None:
        return set()
    expanded = getattr(prompt_plan, "expanded_skills", None) or []
    names: set[str] = set()
    for skill in expanded:
        name = getattr(skill, "name", "")
        if name:
            names.add(str(name))
    return names