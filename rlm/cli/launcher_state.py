from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rlm.cli.context import CliContext
from rlm.cli.service_runtime import port_accepting_connections


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class LauncherMetadata:
    project_root: str = ""
    cwd: str = ""
    env_path: str = ""
    python_executable: str = ""
    platform: str = ""


@dataclass(slots=True)
class BootstrapRecord:
    source: str = ""
    mode: str = ""
    succeeded_at: str = ""
    api_enabled: bool = False
    ws_enabled: bool = False


@dataclass(slots=True)
class RuntimeArtifacts:
    runtime_dir: str = ""
    log_dir: str = ""
    api_pid_file: str = ""
    ws_pid_file: str = ""
    api_log_file: str = ""
    ws_log_file: str = ""
    daemon_manager: str = ""
    daemon_definition: str = ""


@dataclass(slots=True)
class LauncherState:
    schema_version: int = 1
    updated_at: str = ""
    last_known_status: str = "stopped"
    last_launch_mode: str = ""
    last_operation: str = ""
    metadata: LauncherMetadata = field(default_factory=LauncherMetadata)
    last_valid_bootstrap: BootstrapRecord = field(default_factory=BootstrapRecord)
    runtime_artifacts: RuntimeArtifacts = field(default_factory=RuntimeArtifacts)


def _metadata_for_context(
    context: CliContext,
    *,
    project_root: Path | None = None,
    env_path: Path | None = None,
) -> LauncherMetadata:
    return LauncherMetadata(
        project_root=str((project_root or context.paths.project_root).resolve()),
        cwd=str(context.cwd.resolve()),
        env_path=str((env_path or context.paths.env_path).resolve()),
        python_executable=sys.executable,
        platform=platform.platform(),
    )


def _artifacts_for_context(
    context: CliContext,
    *,
    daemon_manager: str = "",
    daemon_definition: str = "",
) -> RuntimeArtifacts:
    return RuntimeArtifacts(
        runtime_dir=str(context.paths.runtime_dir.resolve()),
        log_dir=str(context.paths.log_dir.resolve()),
        api_pid_file=str((context.paths.runtime_dir / "api.pid").resolve()),
        ws_pid_file=str((context.paths.runtime_dir / "ws.pid").resolve()),
        api_log_file=str((context.paths.log_dir / "api.log").resolve()),
        ws_log_file=str((context.paths.log_dir / "ws.log").resolve()),
        daemon_manager=daemon_manager,
        daemon_definition=daemon_definition,
    )


def _state_from_dict(payload: dict[str, object]) -> LauncherState:
    metadata_payload = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    bootstrap_payload = payload.get("last_valid_bootstrap") if isinstance(payload.get("last_valid_bootstrap"), dict) else {}
    artifacts_payload = payload.get("runtime_artifacts") if isinstance(payload.get("runtime_artifacts"), dict) else {}
    return LauncherState(
        schema_version=int(payload.get("schema_version", 1)),
        updated_at=str(payload.get("updated_at", "")),
        last_known_status=str(payload.get("last_known_status", "stopped")),
        last_launch_mode=str(payload.get("last_launch_mode", "")),
        last_operation=str(payload.get("last_operation", "")),
        metadata=LauncherMetadata(**{k: str(v) for k, v in metadata_payload.items()}),
        last_valid_bootstrap=BootstrapRecord(
            source=str(bootstrap_payload.get("source", "")),
            mode=str(bootstrap_payload.get("mode", "")),
            succeeded_at=str(bootstrap_payload.get("succeeded_at", "")),
            api_enabled=bool(bootstrap_payload.get("api_enabled", False)),
            ws_enabled=bool(bootstrap_payload.get("ws_enabled", False)),
        ),
        runtime_artifacts=RuntimeArtifacts(**{k: str(v) for k, v in artifacts_payload.items()}),
    )


def _default_state(
    context: CliContext,
    *,
    project_root: Path | None = None,
    env_path: Path | None = None,
) -> LauncherState:
    return LauncherState(
        updated_at=_utc_now(),
        metadata=_metadata_for_context(context, project_root=project_root, env_path=env_path),
        runtime_artifacts=_artifacts_for_context(context),
    )


def load_launcher_state(
    context: CliContext,
    *,
    project_root: Path | None = None,
    env_path: Path | None = None,
) -> LauncherState:
    path = context.paths.launcher_state_path
    if not path.exists():
        return _default_state(context, project_root=project_root, env_path=env_path)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_state(context, project_root=project_root, env_path=env_path)

    state = _state_from_dict(payload if isinstance(payload, dict) else {})
    state.metadata = _metadata_for_context(context, project_root=project_root, env_path=env_path)
    state.runtime_artifacts = _artifacts_for_context(
        context,
        daemon_manager=state.runtime_artifacts.daemon_manager,
        daemon_definition=state.runtime_artifacts.daemon_definition,
    )
    state.updated_at = _utc_now()
    return state


def summarize_launcher_state(state: LauncherState) -> str:
    parts: list[str] = []
    if state.last_known_status:
        parts.append(f"status={state.last_known_status}")
    if state.last_launch_mode:
        parts.append(f"modo={state.last_launch_mode}")
    if state.last_valid_bootstrap.succeeded_at:
        parts.append(f"bootstrap={state.last_valid_bootstrap.succeeded_at}")
    if state.runtime_artifacts.daemon_manager:
        parts.append(f"daemon={state.runtime_artifacts.daemon_manager}")
    if state.last_operation:
        parts.append(f"op={state.last_operation}")
    return ", ".join(parts) if parts else "sem histórico operacional"


def _diagnosis_severity(classification: str) -> str:
    if classification in {"external-process", "stale-after-crash"}:
        return "warning"
    if classification == "no-state":
        return "info"
    return "ok"


def _read_pid_file(pid_path: Path) -> int | None:
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/fi", f"PID eq {pid}", "/nh", "/fo", "csv"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return f'"{pid}"' in result.stdout or str(pid) in result.stdout
        except Exception:
            return False

    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def build_launcher_state_diagnosis(context: CliContext, *, health_online: bool) -> dict[str, Any]:
    state_path = context.paths.launcher_state_path
    state_exists = state_path.exists()
    state = load_launcher_state(context) if state_exists else _default_state(context)
    api_pid_path = context.paths.runtime_dir / "api.pid"
    ws_pid_path = context.paths.runtime_dir / "ws.pid"
    api_pid = _read_pid_file(api_pid_path)
    ws_pid = _read_pid_file(ws_pid_path)
    api_pid_alive = bool(api_pid and _pid_alive(api_pid))
    ws_pid_alive = bool(ws_pid and _pid_alive(ws_pid))
    api_port_open = port_accepting_connections(context.api_host(), context.api_port())
    ws_port_open = port_accepting_connections(context.ws_host(), context.ws_port())
    persisted_running = state.last_known_status == "running"
    local_runtime_active = health_online or api_pid_alive or ws_pid_alive or api_port_open or ws_port_open
    summary = summarize_launcher_state(state)

    if local_runtime_active and not api_pid_alive and not ws_pid_alive:
        classification = "external-process"
        status_symbol = "⚠"
        detail = f"processo externo ao launcher: runtime local ativo sem PID vivo do launcher ({summary})"
    elif not local_runtime_active and persisted_running:
        classification = "stale-after-crash"
        status_symbol = "⚠"
        detail = f"estado stale após crash: launcher-state ainda marca running, mas PID/porta locais estão inativos ({summary})"
    elif not state_exists and not local_runtime_active:
        classification = "no-state"
        status_symbol = "·"
        detail = "sem launcher-state local ainda"
    else:
        classification = "aligned"
        status_symbol = "✓"
        detail = summary

    return {
        "status_symbol": status_symbol,
        "severity": _diagnosis_severity(classification),
        "classification": classification,
        "detail": detail,
        "summary": summary,
        "signals": {
            "health_online": health_online,
            "api_pid": api_pid,
            "ws_pid": ws_pid,
            "api_pid_alive": api_pid_alive,
            "ws_pid_alive": ws_pid_alive,
            "api_port_open": api_port_open,
            "ws_port_open": ws_port_open,
            "state_exists": state_exists,
            "persisted_running": persisted_running,
        },
        "persisted": {
            "last_known_status": state.last_known_status,
            "last_launch_mode": state.last_launch_mode,
            "last_operation": state.last_operation,
            "last_valid_bootstrap_at": state.last_valid_bootstrap.succeeded_at,
            "daemon_manager": state.runtime_artifacts.daemon_manager,
        },
    }


def diagnose_launcher_state_alignment(context: CliContext, *, server_online: bool) -> tuple[str, str]:
    diagnosis = build_launcher_state_diagnosis(context, health_online=server_online)
    return str(diagnosis["status_symbol"]), str(diagnosis["detail"])


def save_launcher_state(context: CliContext, state: LauncherState) -> Path:
    path = context.paths.launcher_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")
    return path


def update_launcher_state(
    context: CliContext,
    mutator: Callable[[LauncherState], None],
    *,
    project_root: Path | None = None,
    env_path: Path | None = None,
) -> LauncherState:
    state = load_launcher_state(context, project_root=project_root, env_path=env_path)
    mutator(state)
    state.updated_at = _utc_now()
    save_launcher_state(context, state)
    return state


def mark_bootstrap_success(
    context: CliContext,
    *,
    source: str,
    mode: str,
    api_enabled: bool,
    ws_enabled: bool,
) -> LauncherState:
    def mutate(state: LauncherState) -> None:
        state.last_known_status = "running"
        state.last_launch_mode = mode
        state.last_operation = source
        state.last_valid_bootstrap = BootstrapRecord(
            source=source,
            mode=mode,
            succeeded_at=_utc_now(),
            api_enabled=api_enabled,
            ws_enabled=ws_enabled,
        )

    return update_launcher_state(context, mutate)


def mark_stopped(context: CliContext) -> LauncherState:
    def mutate(state: LauncherState) -> None:
        state.last_known_status = "stopped"
        state.last_operation = "stop"

    return update_launcher_state(context, mutate)


def mark_runtime_status(context: CliContext, *, api_running: bool, ws_running: bool) -> LauncherState:
    def mutate(state: LauncherState) -> None:
        if api_running and ws_running:
            state.last_known_status = "running"
            state.last_launch_mode = state.last_launch_mode or "background-combined"
        elif api_running:
            state.last_known_status = "running"
            state.last_launch_mode = "api-only"
        elif ws_running:
            state.last_known_status = "running"
            state.last_launch_mode = "ws-only"
        else:
            state.last_known_status = "stopped"

    return update_launcher_state(context, mutate)


def mark_daemon_installed(
    context: CliContext,
    *,
    manager: str,
    definition_path: Path,
    project_root: Path | None = None,
    env_path: Path | None = None,
) -> LauncherState:
    def mutate(state: LauncherState) -> None:
        state.last_operation = f"install:{manager}"
        state.runtime_artifacts.daemon_manager = manager
        state.runtime_artifacts.daemon_definition = str(definition_path.resolve())

    return update_launcher_state(context, mutate, project_root=project_root, env_path=env_path)


def mark_update(context: CliContext, *, restarted: bool) -> LauncherState:
    def mutate(state: LauncherState) -> None:
        state.last_operation = "update:restart" if restarted else "update"

    return update_launcher_state(context, mutate)