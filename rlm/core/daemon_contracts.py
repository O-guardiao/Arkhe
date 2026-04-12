"""
Tipos de contrato compartilhados entre core e daemon.

Estes tipos são dataclasses puros (sem dependências de camada) que definem
a interface de comunicação entre core (L0) e daemon (L3).  Vivem em core
para que possam ser importados por qualquer camada sem violar a hierarquia.

Canônico: ``rlm.core.daemon_contracts``
Re-exportado por ``rlm.daemon.contracts`` para backward compat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


TaskDispatchRoute = Literal[
    "internal_evaluator",
    "internal_planner",
    "internal_text_worker",
    "spawn_child_rlm",
]


def _dict_factory() -> dict[str, Any]:
    return {}


@dataclass(frozen=True, slots=True)
class DaemonTaskRequest:
    session_id: str = ""
    client_id: str = ""
    task: str = ""
    context: str = ""
    model: str | None = None
    model_role: str = "worker"
    max_iterations: int = 8
    timeout_s: float = 300.0
    interaction_mode: str = "repl"
    metadata: dict[str, Any] = field(default_factory=_dict_factory)


@dataclass(frozen=True, slots=True)
class DaemonTaskResult:
    route: TaskDispatchRoute
    response: str
    metadata: dict[str, Any] = field(default_factory=_dict_factory)


# Roles que sub_rlm auto-divert para text mode (sem REPL).
AUTO_DIVERT_TEXT_ROLES: frozenset[str] = frozenset({
    "fast",
    "response",
    "simple",
    "simple_inspect",
    "micro",
    "minirepl",
    "evaluator",
})
