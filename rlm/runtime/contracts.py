from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol, cast, runtime_checkable

from rlm.core.security.execution_policy import RuntimeExecutionPolicy


ApprovalCallback = Callable[[str, str], bool]


@dataclass(frozen=True, slots=True)
class RuntimeThreatReport:
    is_suspicious: bool = False
    threat_level: str = "clean"
    patterns_found: list[str] = field(default_factory=list)
    sanitized_text: str = ""


@runtime_checkable
class RuntimePolicyPort(Protocol):
    def infer_runtime_execution_policy(
        self,
        query_text: str,
        *,
        client_id: str = "",
        prompt_plan: Any | None = None,
        default_model: str | None = None,
    ) -> RuntimeExecutionPolicy: ...


@runtime_checkable
class RuntimeSecurityPort(Protocol):
    def audit_input(self, text: str, session_id: str = "") -> RuntimeThreatReport: ...

    def audit_code(self, code: str) -> None: ...


@runtime_checkable
class RuntimeApprovalPort(Protocol):
    def request(
        self,
        description: str,
        code: str = "",
        session_id: str = "unknown",
        timeout_s: float | None = None,
        request_id: str | None = None,
    ) -> bool: ...

    def approve(self, request_id: str, resolved_by: str = "human") -> bool: ...

    def deny(self, request_id: str, resolved_by: str = "human") -> bool: ...

    def list_pending(self) -> list[dict[str, Any]]: ...

    def get_record(self, request_id: str) -> dict[str, Any] | None: ...

    def stats(self) -> dict[str, Any]: ...

    def make_repl_fn(self, session_id: str) -> ApprovalCallback: ...


@runtime_checkable
class RuntimeVaultPort(Protocol):
    def get_tools(self, rlm_session: Any) -> dict[str, Any]: ...


@dataclass(slots=True)
class RuntimeGuard:
    policy: RuntimePolicyPort
    security: RuntimeSecurityPort
    approvals: RuntimeApprovalPort
    vaults: RuntimeVaultPort
    exec_approval_required: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeRecursionControls:
    paused: bool = False
    pause_reason: str = ""
    focused_branch_id: int | None = None
    fixed_winner_branch_id: int | None = None
    branch_priorities: dict[str, int] = field(default_factory=dict)
    last_checkpoint_path: str = "-"
    last_operator_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], asdict(self))


@dataclass(frozen=True, slots=True)
class RuntimeRecursionBranchView:
    branch_id: int | None = None
    task_id: int | None = None
    parent_task_id: int | None = None
    parent_branch_id: int | None = None
    depth: int | None = None
    role: str | None = None
    mode: str = ""
    title: str = ""
    status: str = ""
    final_status: str | None = None
    duration_ms: float | None = None
    error_type: str | None = None
    error_message: str | None = None
    operator_focused: bool = False
    operator_fixed_winner: bool = False
    operator_priority: int | None = None
    created_at: Any = None
    updated_at: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], asdict(self))


@dataclass(frozen=True, slots=True)
class RuntimeRecursionEvent:
    operation: str = "unknown"
    topic: str = ""
    sender_id: int | None = None
    receiver_id: int | None = None
    payload_preview: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], asdict(self))


@dataclass(frozen=True, slots=True)
class RuntimeRecursionSummary:
    winner_branch_id: int | None = None
    cancelled_count: int = 0
    failed_count: int = 0
    total_tasks: int = 0
    branch_count: int = 0
    branch_status_counts: dict[str, int] = field(default_factory=dict)
    strategy: dict[str, Any] = field(default_factory=dict)
    stop_evaluation: dict[str, Any] = field(default_factory=dict)
    focused_branch_id: int | None = None
    fixed_winner_branch_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], asdict(self))


@dataclass(frozen=True, slots=True)
class RuntimeRecursionProjection:
    attached: bool = False
    active_branch_id: int | None = None
    controls: RuntimeRecursionControls = field(default_factory=RuntimeRecursionControls)
    summary: RuntimeRecursionSummary = field(default_factory=RuntimeRecursionSummary)
    branches: list[RuntimeRecursionBranchView] = field(default_factory=list)
    events: list[RuntimeRecursionEvent] = field(default_factory=list)
    latest_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], asdict(self))


@dataclass(frozen=True, slots=True)
class RuntimeDaemonMemoryScope:
    session_id: str = ""
    channel: str = ""
    actor: str = ""
    active_channels: list[str] = field(default_factory=list)
    workspace_scope: str = ""
    agent_depth: int | None = None
    branch_id: int | None = None
    agent_role: str = ""
    parent_session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], asdict(self))


@dataclass(frozen=True, slots=True)
class RuntimeDaemonMemoryAccess:
    counts: dict[str, int] = field(default_factory=dict)
    last_scope: RuntimeDaemonMemoryScope = field(default_factory=RuntimeDaemonMemoryScope)

    def to_dict(self) -> dict[str, Any]:
        payload = cast(dict[str, Any], asdict(self))
        counts = payload.pop("counts", {})
        payload.update(counts)
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeDaemonChannelRuntime:
    total: int = 0
    running: int = 0
    healthy: int = 0
    registered_channels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], asdict(self))


@dataclass(frozen=True, slots=True)
class RuntimeDaemonProjection:
    name: str = "main"
    running: bool = False
    ready: bool = False
    draining: bool = False
    inflight_dispatches: int = 0
    active_sessions: int = 0
    attached_channels: dict[str, int] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    warm_runtime: dict[str, int] = field(default_factory=dict)
    outbox: dict[str, Any] = field(default_factory=dict)
    channel_runtime: RuntimeDaemonChannelRuntime = field(default_factory=RuntimeDaemonChannelRuntime)
    memory_access: RuntimeDaemonMemoryAccess = field(default_factory=RuntimeDaemonMemoryAccess)

    def to_dict(self) -> dict[str, Any]:
        payload = cast(dict[str, Any], asdict(self))
        payload["memory_access"] = self.memory_access.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeProjection:
    tasks: dict[str, Any] = field(default_factory=dict)
    attachments: dict[str, Any] = field(default_factory=dict)
    timeline: dict[str, Any] = field(default_factory=dict)
    recursive_session: dict[str, Any] = field(default_factory=dict)
    coordination: dict[str, Any] = field(default_factory=dict)
    controls: dict[str, Any] = field(default_factory=dict)
    strategy: dict[str, Any] = field(default_factory=dict)
    recursion: RuntimeRecursionProjection = field(default_factory=RuntimeRecursionProjection)
    daemon: RuntimeDaemonProjection | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = cast(dict[str, Any], asdict(self))
        payload["recursion"] = self.recursion.to_dict()
        if self.daemon is None:
            payload.pop("daemon", None)
        else:
            payload["daemon"] = self.daemon.to_dict()
        return payload