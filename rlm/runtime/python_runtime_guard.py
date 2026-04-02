from __future__ import annotations

import os
from typing import Any

from rlm.core.security.exec_approval import ExecApprovalGate
from rlm.core.security.execution_policy import infer_runtime_execution_policy
from rlm.runtime.native_policy_adapter import build_runtime_policy_port_from_env
from rlm.core.security import REPLAuditor, auditor
from rlm.runtime.contracts import RuntimeGuard, RuntimeThreatReport
from rlm.tools.vault_tools import get_vault_tools


class PythonRuntimePolicyPort:
    def infer_runtime_execution_policy(
        self,
        query_text: str,
        *,
        client_id: str = "",
        prompt_plan: Any | None = None,
        default_model: str | None = None,
    ):
        return infer_runtime_execution_policy(
            query_text,
            client_id=client_id,
            prompt_plan=prompt_plan,
            default_model=default_model,
        )


class PythonRuntimeSecurityPort:
    def __init__(self, *, security_auditor: REPLAuditor | None = None) -> None:
        self._auditor = security_auditor or auditor

    def audit_input(self, text: str, session_id: str = "") -> RuntimeThreatReport:
        report = self._auditor.audit_input(text, session_id=session_id)
        return RuntimeThreatReport(
            is_suspicious=report.is_suspicious,
            threat_level=report.threat_level,
            patterns_found=list(report.patterns_found),
            sanitized_text=report.sanitized_text,
        )

    def audit_code(self, code: str) -> None:
        self._auditor.audit_code(code)


class PythonRuntimeVaultPort:
    def get_tools(self, rlm_session: Any) -> dict[str, Any]:
        return get_vault_tools(rlm_session)


def build_runtime_guard(
    *,
    approval_timeout_s: int = 60,
    exec_approval_required: bool = False,
    security_auditor: REPLAuditor | None = None,
) -> RuntimeGuard:
    fallback_policy = PythonRuntimePolicyPort()
    return RuntimeGuard(
        policy=build_runtime_policy_port_from_env(fallback=fallback_policy),
        security=PythonRuntimeSecurityPort(security_auditor=security_auditor),
        approvals=ExecApprovalGate(default_timeout_s=approval_timeout_s),
        vaults=PythonRuntimeVaultPort(),
        exec_approval_required=exec_approval_required,
    )


def build_runtime_guard_from_env() -> RuntimeGuard:
    approval_timeout_s = int(os.environ.get("RLM_EXEC_APPROVAL_TIMEOUT", "60"))
    exec_approval_required = os.environ.get("RLM_EXEC_APPROVAL_REQUIRED", "false").lower() == "true"
    return build_runtime_guard(
        approval_timeout_s=approval_timeout_s,
        exec_approval_required=exec_approval_required,
    )