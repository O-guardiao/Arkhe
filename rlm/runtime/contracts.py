from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

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