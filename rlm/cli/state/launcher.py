"""Dataclasses de estado do launcher e funções de persistência."""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, cast

from rlm.cli.context import CliContext


# --------------------------------------------------------------------------- #
# Helpers internos                                                             #
# --------------------------------------------------------------------------- #

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_payload_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    typed_value = cast(dict[object, object], value)
    return {str(key): item for key, item in typed_value.items()}


def _coerce_schema_version(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("schema_version inválido")


def _coerce_dataclass_payload(
    cls: type[LauncherMetadata] | type[RuntimeArtifacts],
    payload: dict[str, object],
) -> dict[str, str]:
    allowed = {field_info.name for field_info in fields(cls)}
    return {
        key: str(value)
        for key, value in payload.items()
        if key in allowed
    }


# --------------------------------------------------------------------------- #
# Dataclasses                                                                  #
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Builders internos                                                            #
# --------------------------------------------------------------------------- #

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
    metadata_payload = _as_payload_dict(payload.get("metadata"))
    bootstrap_payload = _as_payload_dict(payload.get("last_valid_bootstrap"))
    artifacts_payload = _as_payload_dict(payload.get("runtime_artifacts"))
    return LauncherState(
        schema_version=_coerce_schema_version(payload.get("schema_version", 1)),
        updated_at=str(payload.get("updated_at", "")),
        last_known_status=str(payload.get("last_known_status", "stopped")),
        last_launch_mode=str(payload.get("last_launch_mode", "")),
        last_operation=str(payload.get("last_operation", "")),
        metadata=LauncherMetadata(**_coerce_dataclass_payload(LauncherMetadata, metadata_payload)),
        last_valid_bootstrap=BootstrapRecord(
            source=str(bootstrap_payload.get("source", "")),
            mode=str(bootstrap_payload.get("mode", "")),
            succeeded_at=str(bootstrap_payload.get("succeeded_at", "")),
            api_enabled=bool(bootstrap_payload.get("api_enabled", False)),
            ws_enabled=bool(bootstrap_payload.get("ws_enabled", False)),
        ),
        runtime_artifacts=RuntimeArtifacts(**_coerce_dataclass_payload(RuntimeArtifacts, artifacts_payload)),
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


# --------------------------------------------------------------------------- #
# Load / Save / Update                                                         #
# --------------------------------------------------------------------------- #

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

    try:
        state = _state_from_dict(_as_payload_dict(payload))
    except (TypeError, ValueError):
        return _default_state(context, project_root=project_root, env_path=env_path)

    state.metadata = _metadata_for_context(context, project_root=project_root, env_path=env_path)
    state.runtime_artifacts = _artifacts_for_context(
        context,
        daemon_manager=state.runtime_artifacts.daemon_manager,
        daemon_definition=state.runtime_artifacts.daemon_definition,
    )
    state.updated_at = _utc_now()
    return state


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


# --------------------------------------------------------------------------- #
# mark_* mutations                                                             #
# --------------------------------------------------------------------------- #

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
