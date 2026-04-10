from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


DispatchClass = Literal[
    "deterministic",
    "llm_required",
    "task_agent_required",
    "reject",
]

EventPriority = Literal["low", "normal", "high", "urgent"]
TaskDispatchRoute = Literal["internal_evaluator", "internal_planner", "internal_text_worker", "spawn_child_rlm"]


def _dict_factory() -> dict[str, Any]:
    return {}


def _str_tuple_factory() -> tuple[str, ...]:
    return ()


def _dict_tuple_factory() -> tuple[dict[str, Any], ...]:
    return ()


@dataclass(frozen=True, slots=True)
class DaemonSessionState:
    session_id: str
    client_id: str
    user_id: str
    status: str
    originating_channel: str = ""
    active_channels: tuple[str, ...] = field(default_factory=_str_tuple_factory)
    last_activity_at: str = ""
    context_refs: tuple[dict[str, Any], ...] = field(default_factory=_dict_tuple_factory)
    channel_context: dict[str, Any] = field(default_factory=_dict_factory)
    agent_state: dict[str, Any] = field(default_factory=_dict_factory)
    llm_policy: dict[str, Any] = field(default_factory=_dict_factory)
    delivery_context: dict[str, Any] = field(default_factory=_dict_factory)
    metadata: dict[str, Any] = field(default_factory=_dict_factory)


@dataclass(frozen=True, slots=True)
class ChannelEvent:
    event_id: str
    timestamp: float
    channel: str
    client_id: str
    session: DaemonSessionState | None
    user_id: str = ""
    thread_id: str = ""
    message_type: str = "text"
    text: str = ""
    payload: dict[str, Any] = field(default_factory=_dict_factory)
    attachments: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=_dict_factory)
    priority: EventPriority = "normal"


@dataclass(frozen=True, slots=True)
class RecursionResult:
    session_id: str
    route: DispatchClass
    content: str = ""
    actions: tuple[dict[str, Any], ...] = ()
    artifacts: tuple[dict[str, Any], ...] = ()
    memory_writes: tuple[dict[str, Any], ...] = ()
    channel_updates: tuple[dict[str, Any], ...] = ()
    metrics: dict[str, Any] = field(default_factory=_dict_factory)


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
