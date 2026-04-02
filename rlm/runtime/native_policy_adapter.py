from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from rlm.core.security.execution_policy import (
    RuntimeExecutionPolicy,
    build_policy_decision_input,
    runtime_execution_policy_from_mapping,
)
from rlm.runtime.contracts import RuntimePolicyPort


logger = logging.getLogger(__name__)

_POLICY_VERSION = 1
_NATIVE_POLICY_MODE_ENV = "RLM_NATIVE_POLICY_MODE"
_NATIVE_POLICY_BIN_ENV = "RLM_NATIVE_POLICY_BIN"
_NATIVE_POLICY_TIMEOUT_ENV = "RLM_NATIVE_POLICY_TIMEOUT_MS"


class SubprocessRuntimePolicyPort:
    def __init__(
        self,
        *,
        binary_path: Path,
        timeout_s: float,
        fallback: RuntimePolicyPort,
    ) -> None:
        self._binary_path = binary_path
        self._timeout_s = timeout_s
        self._fallback = fallback

    def infer_runtime_execution_policy(
        self,
        query_text: str,
        *,
        client_id: str = "",
        prompt_plan: Any | None = None,
        default_model: str | None = None,
    ) -> RuntimeExecutionPolicy:
        payload = build_policy_decision_input(
            query_text,
            client_id=client_id,
            prompt_plan=prompt_plan,
            default_model=default_model,
        )
        try:
            completed = subprocess.run(
                [str(self._binary_path)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or f"exit code {completed.returncode}")
            response = json.loads(completed.stdout)
            if not isinstance(response, dict):
                raise TypeError("native policy response must be a JSON object")
            response_version = response.get("policy_version")
            if response_version not in (None, _POLICY_VERSION):
                raise ValueError(f"unsupported native policy version: {response_version}")
            return runtime_execution_policy_from_mapping(response)
        except (
            OSError,
            RuntimeError,
            TimeoutError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as exc:
            logger.warning(
                "Native policy bridge failed; using Python fallback: %s",
                exc,
            )
            return self._fallback.infer_runtime_execution_policy(
                query_text,
                client_id=client_id,
                prompt_plan=prompt_plan,
                default_model=default_model,
            )


def build_runtime_policy_port_from_env(*, fallback: RuntimePolicyPort) -> RuntimePolicyPort:
    mode = os.environ.get(_NATIVE_POLICY_MODE_ENV, "python").strip().lower()
    if mode not in {"native", "auto"}:
        return fallback

    binary_path = discover_native_policy_binary()
    if binary_path is None:
        if mode == "native":
            logger.warning("Native policy mode requested but no binary was found; using Python fallback")
        return fallback

    try:
        timeout_ms = int(os.environ.get(_NATIVE_POLICY_TIMEOUT_ENV, "800"))
    except ValueError:
        timeout_ms = 800
    return SubprocessRuntimePolicyPort(
        binary_path=binary_path,
        timeout_s=max(timeout_ms, 1) / 1000.0,
        fallback=fallback,
    )


def discover_native_policy_binary() -> Path | None:
    configured = os.environ.get(_NATIVE_POLICY_BIN_ENV)
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return candidate

    repo_root = Path(__file__).resolve().parents[2]
    binary_name = "arkhe-policy-core.exe" if os.name == "nt" else "arkhe-policy-core"
    candidates = [
        repo_root / "native" / "arkhe-policy-core" / "target" / "debug" / binary_name,
        repo_root / "native" / "arkhe-policy-core" / "target" / "release" / binary_name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None