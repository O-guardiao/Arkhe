"""Diagnóstico de alinhamento do launcher-state."""

from __future__ import annotations

from typing import Any

from rlm.cli.context import CliContext
from rlm.cli.service_runtime import port_accepting_connections
from rlm.cli.state.launcher import (
    LauncherState,
    _default_state,
    load_launcher_state,
    summarize_launcher_state,
)
from rlm.cli.state.pid import pid_alive, read_pid_file


def _diagnosis_severity(classification: str) -> str:
    if classification in {"external-process", "stale-after-crash"}:
        return "warning"
    if classification == "no-state":
        return "info"
    return "ok"


def build_launcher_state_diagnosis(context: CliContext, *, health_online: bool) -> dict[str, Any]:
    state_path = context.paths.launcher_state_path
    state_exists = state_path.exists()
    state = load_launcher_state(context) if state_exists else _default_state(context)
    api_pid_path = context.paths.runtime_dir / "api.pid"
    ws_pid_path = context.paths.runtime_dir / "ws.pid"
    api_pid = read_pid_file(api_pid_path)
    ws_pid = read_pid_file(ws_pid_path)
    api_pid_alive = bool(api_pid and pid_alive(api_pid))
    ws_pid_alive = bool(ws_pid and pid_alive(ws_pid))
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
