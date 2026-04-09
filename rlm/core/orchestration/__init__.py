"""Superficie publica do subsistema de orquestracao.

Este pacote agrega contratos de coordenacao do runtime:
- handoffs explicitos entre papeis operacionais;
- exploracao MCTS e sandbox de branches;
- orquestracao worker/evaluator/human;
- scheduler para execucoes recorrentes;
- sibling bus para coordenacao entre filhos paralelos;
- supervisor com timeout, abort e queueing por sessao.

Como ``rlm.core`` importa ``orchestration`` no bootstrap do pacote raiz,
os simbolos sao resolvidos sob demanda para evitar eager imports de LocalREPL,
ThreadPoolExecutor e outros componentes pesados quando apenas o namespace core
precisa ser registrado.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from rlm.core.orchestration.handoff import HandoffRecord, VALID_HANDOFF_ROLES, make_handoff_fn
	from rlm.core.orchestration.mcts import (
		BranchResult,
		EvaluationStage,
		MCTSOrchestrator,
		ProgramArchive,
		RecursiveStrategy,
		SandboxREPL,
		default_recursive_strategies,
		default_score_fn,
		evolutionary_branch_search,
		generate_recursive_strategies,
		generate_refined_recursive_strategies,
	)
	from rlm.core.orchestration.role_orchestrator import (
		PENDING_HANDOFFS_KEY,
		orchestrate_roles,
		pop_pending_handoffs,
	)
	from rlm.core.orchestration.scheduler import (
		CronJob,
		RLMScheduler,
		compute_next_run,
		parse_at_timestamp,
		parse_interval_seconds,
	)
	from rlm.core.orchestration.sibling_bus import (
		ControlChannel,
		SIGNAL_TOPIC_MAP,
		SiblingBus,
		SiblingBusError,
		SiblingMessage,
		VALID_SIGNAL_TYPES,
	)
	from rlm.core.orchestration.supervisor import ExecutionResult, RLMSupervisor, SupervisorConfig

_LAZY_MODULES: dict[str, str] = {
	"handoff": "rlm.core.orchestration.handoff",
	"mcts": "rlm.core.orchestration.mcts",
	"role_orchestrator": "rlm.core.orchestration.role_orchestrator",
	"scheduler": "rlm.core.orchestration.scheduler",
	"sibling_bus": "rlm.core.orchestration.sibling_bus",
	"supervisor": "rlm.core.orchestration.supervisor",
}

_LAZY_ATTRS: dict[str, str] = {
	"VALID_HANDOFF_ROLES": "rlm.core.orchestration.handoff",
	"HandoffRecord": "rlm.core.orchestration.handoff",
	"make_handoff_fn": "rlm.core.orchestration.handoff",
	"BranchResult": "rlm.core.orchestration.mcts",
	"EvaluationStage": "rlm.core.orchestration.mcts",
	"MCTSOrchestrator": "rlm.core.orchestration.mcts",
	"ProgramArchive": "rlm.core.orchestration.mcts",
	"RecursiveStrategy": "rlm.core.orchestration.mcts",
	"SandboxREPL": "rlm.core.orchestration.mcts",
	"default_recursive_strategies": "rlm.core.orchestration.mcts",
	"default_score_fn": "rlm.core.orchestration.mcts",
	"evolutionary_branch_search": "rlm.core.orchestration.mcts",
	"generate_recursive_strategies": "rlm.core.orchestration.mcts",
	"generate_refined_recursive_strategies": "rlm.core.orchestration.mcts",
	"PENDING_HANDOFFS_KEY": "rlm.core.orchestration.role_orchestrator",
	"orchestrate_roles": "rlm.core.orchestration.role_orchestrator",
	"pop_pending_handoffs": "rlm.core.orchestration.role_orchestrator",
	"CronJob": "rlm.core.orchestration.scheduler",
	"RLMScheduler": "rlm.core.orchestration.scheduler",
	"compute_next_run": "rlm.core.orchestration.scheduler",
	"parse_at_timestamp": "rlm.core.orchestration.scheduler",
	"parse_interval_seconds": "rlm.core.orchestration.scheduler",
	"ControlChannel": "rlm.core.orchestration.sibling_bus",
	"SIGNAL_TOPIC_MAP": "rlm.core.orchestration.sibling_bus",
	"SiblingBus": "rlm.core.orchestration.sibling_bus",
	"SiblingBusError": "rlm.core.orchestration.sibling_bus",
	"SiblingMessage": "rlm.core.orchestration.sibling_bus",
	"VALID_SIGNAL_TYPES": "rlm.core.orchestration.sibling_bus",
	"ExecutionResult": "rlm.core.orchestration.supervisor",
	"RLMSupervisor": "rlm.core.orchestration.supervisor",
	"SupervisorConfig": "rlm.core.orchestration.supervisor",
}

__all__ = [
	"VALID_HANDOFF_ROLES",
	"HandoffRecord",
	"make_handoff_fn",
	"BranchResult",
	"EvaluationStage",
	"MCTSOrchestrator",
	"ProgramArchive",
	"RecursiveStrategy",
	"SandboxREPL",
	"default_recursive_strategies",
	"default_score_fn",
	"evolutionary_branch_search",
	"generate_recursive_strategies",
	"generate_refined_recursive_strategies",
	"PENDING_HANDOFFS_KEY",
	"orchestrate_roles",
	"pop_pending_handoffs",
	"CronJob",
	"RLMScheduler",
	"compute_next_run",
	"parse_at_timestamp",
	"parse_interval_seconds",
	"ControlChannel",
	"SIGNAL_TOPIC_MAP",
	"SiblingBus",
	"SiblingBusError",
	"SiblingMessage",
	"VALID_SIGNAL_TYPES",
	"ExecutionResult",
	"RLMSupervisor",
	"SupervisorConfig",
]


def __getattr__(name: str):
	if name in _LAZY_MODULES:
		module = importlib.import_module(_LAZY_MODULES[name])
		globals()[name] = module
		return module
	if name in _LAZY_ATTRS:
		module = importlib.import_module(_LAZY_ATTRS[name])
		value = getattr(module, name)
		globals()[name] = value
		return value
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
	return sorted(set(globals()) | set(__all__) | set(_LAZY_MODULES))
