"""RLM Service Manager facade.

Mantém a API pública estável do CLI, mas delega a implementação para
submódulos menores de runtime, installers e update.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, cast

from rlm.cli.context import CliContext
from rlm.cli.json_output import build_cli_json_envelope
from rlm.cli.output import HAS_RICH, _err, _info, _ok, _warn
from rlm.cli.state.diagnosis import build_launcher_state_diagnosis
from rlm.cli.state.launcher import (
    mark_bootstrap_success,
    mark_daemon_installed,
    mark_runtime_status,
    mark_stopped,
    mark_update,
    summarize_launcher_state,
)
from rlm.cli.state.pid import pid_alive as _pid_alive, read_pid_file as _read_pid, remove_pid as _remove_pid, write_pid as _write_pid
from rlm.cli.service_installers import install_launchd_service_impl, install_systemd_service_impl
from rlm.cli.service_runtime import (
    ServiceRuntimeLayout,
    port_accepting_connections,
    runtime_mode,
    services_are_running as services_are_running_impl,
    show_runtime_status,
    start_runtime,
    stop_runtime,
)
from rlm.cli.service_update import update_installation_impl
from rlm.cli.service_wireguard import add_wireguard_peer_impl


# --------------------------------------------------------------------------- #
# Configuração  (lazy — evita side-effect no import)                           #
# --------------------------------------------------------------------------- #

_SERVICE_NAME = "rlm"

# Patchable overrides — testes usam patch("rlm.cli.service._PID_DIR", ...) etc.
_PID_DIR: Path | None = None
_PID_API: Path | None = None
_PID_WS: Path | None = None
_LOG_DIR: Path | None = None


def _default_paths() -> tuple[Path, Path]:
    """Retorna (pid_dir, log_dir) do contexto padrão sem load_env."""
    ctx = CliContext.from_environment(load_env=False)
    return ctx.paths.runtime_dir, ctx.paths.log_dir


def _runtime_layout() -> ServiceRuntimeLayout:
    if _PID_DIR is not None:
        pid_dir = _PID_DIR
        log_dir = _LOG_DIR or pid_dir.parent / "log"
    else:
        pid_dir, log_dir = _default_paths()
    return ServiceRuntimeLayout(
        pid_dir=pid_dir,
        pid_api=_PID_API or pid_dir / "api.pid",
        pid_ws=_PID_WS or pid_dir / "ws.pid",
        log_dir=_LOG_DIR or log_dir,
        service_name=_SERVICE_NAME,
    )


# --------------------------------------------------------------------------- #
# Rich Table (re-export para show_status)                                      #
# --------------------------------------------------------------------------- #

try:
    from rich.table import Table
except ImportError:
    Table = None


def _load_env_settings() -> dict[str, str]:
    context = CliContext.from_environment(load_env=True)
    return dict(context.env)


def _service_context(context: CliContext | None = None, *, load_env: bool = True) -> CliContext:
    if context is not None:
        if load_env:
            context.load_env_file(override=False)
        return context
    return CliContext.from_environment(load_env=load_env)


def _os_getuid() -> int:
    getter = getattr(os, "getuid", None)
    if callable(getter):
        return cast(Callable[[], int], getter)()
    return 0


def _os_geteuid() -> int:
    getter = getattr(os, "geteuid", None)
    if callable(getter):
        return cast(Callable[[], int], getter)()
    return 0


def start_services(
    foreground: bool = False,
    api_only: bool = False,
    ws_only: bool = False,
    context: CliContext | None = None,
) -> int:
    current_context = _service_context(context, load_env=True)
    rc = start_runtime(
        current_context,
        _runtime_layout(),
        foreground=foreground,
        api_only=api_only,
        ws_only=ws_only,
        write_pid=_write_pid,
        remove_pid=_remove_pid,
        ok=_ok,
        warn=_warn,
        err=_err,
        info=_info,
    )
    if rc == 0:
        mark_bootstrap_success(
            current_context,
            source="start",
            mode=runtime_mode(foreground=foreground, api_only=api_only, ws_only=ws_only),
            api_enabled=not ws_only,
            ws_enabled=ws_only or not api_only,
        )
    return rc


def stop_services(context: CliContext | None = None) -> int:
    current_context = _service_context(context, load_env=False)
    rc = stop_runtime(
        _runtime_layout(),
        read_pid=_read_pid,
        remove_pid=_remove_pid,
        pid_alive=_pid_alive,
        ok=_ok,
        warn=_warn,
        err=_err,
        info=_info,
    )
    if rc == 0:
        mark_stopped(current_context)
    return rc


def _build_status_snapshot(context: CliContext) -> dict[str, Any]:
    layout = _runtime_layout()
    api_pid = _read_pid(layout.pid_api)
    ws_pid = _read_pid(layout.pid_ws)
    api_running = bool(api_pid and _pid_alive(api_pid))
    ws_running = bool(ws_pid and _pid_alive(ws_pid))
    api_port_open = port_accepting_connections(context.api_host(), context.api_port())
    ws_port_open = port_accepting_connections(context.ws_host(), context.ws_port())
    launcher = build_launcher_state_diagnosis(context, health_online=api_port_open)
    state = mark_runtime_status(context, api_running=api_running, ws_running=ws_running)

    return {
        "runtime": {
            "api": {
                "pid": api_pid,
                "running": api_running,
                "port_open": api_port_open,
                "url": f"{context.api_base_url()}/",
                "docs_url": context.docs_url(),
                "log_file": str((layout.log_dir / "api.log").resolve()),
            },
            "ws": {
                "pid": ws_pid,
                "running": ws_running,
                "port_open": ws_port_open,
                "url": context.ws_base_url(),
                "log_file": str((layout.log_dir / "ws.log").resolve()),
            },
            "webchat_url": context.webchat_url(),
        },
        "launcher_state": launcher,
        "persisted_state_summary": summarize_launcher_state(state),
    }


def show_status(context: CliContext | None = None, json_output: bool = False) -> int:
    current_context = _service_context(context, load_env=True)
    if json_output:
        payload = _build_status_snapshot(current_context)
        envelope = build_cli_json_envelope(
            "status",
            payload,
            severity=str(payload.get("launcher_state", {}).get("severity", "info")),
        )
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
        return 0

    rc, api_running, ws_running = show_runtime_status(
        current_context,
        _runtime_layout(),
        read_pid=_read_pid,
        pid_alive=_pid_alive,
        info=_info,
        warn=_warn,
        has_rich=HAS_RICH,
        table_cls=Table,
    )
    state = mark_runtime_status(current_context, api_running=api_running, ws_running=ws_running)
    _info(f"Launcher state: {summarize_launcher_state(state)}")
    return rc


def _services_are_running() -> bool:
    return services_are_running_impl(_runtime_layout(), read_pid=_read_pid, pid_alive=_pid_alive)


def update_installation(
    check_only: bool = False,
    restart: bool = True,
    target_path: str | None = None,
    context: CliContext | None = None,
) -> int:
    current_context = _service_context(context, load_env=True)
    rc = update_installation_impl(
        current_context,
        check_only=check_only,
        restart=restart,
        target_path=target_path,
        info=_info,
        ok=_ok,
        err=_err,
        services_are_running=_services_are_running,
        stop_services=lambda: stop_services(context=current_context),
        start_services=lambda: start_services(foreground=False, context=current_context),
    )
    if rc == 0 and not check_only:
        mark_update(current_context, restarted=restart and _services_are_running())
    return rc


def install_systemd_service(project_root: Path, env_path: Path) -> int:
    rc, unit_file = install_systemd_service_impl(
        project_root,
        env_path,
        service_name=_SERVICE_NAME,
        ok=_ok,
        err=_err,
        info=_info,
    )
    if rc == 0:
        current_context = CliContext.from_environment(load_env=False)
        mark_daemon_installed(
            current_context,
            manager="systemd",
            definition_path=unit_file,
            project_root=project_root,
            env_path=env_path,
        )
    return rc


def install_launchd_service(project_root: Path, env_path: Path) -> int:
    _, log_dir = _default_paths()
    rc, plist_path = install_launchd_service_impl(
        project_root,
        env_path,
        log_dir,
        os_getuid=_os_getuid,
        ok=_ok,
        info=_info,
    )
    if rc == 0:
        current_context = CliContext.from_environment(load_env=False)
        mark_daemon_installed(
            current_context,
            manager="launchd",
            definition_path=plist_path,
            project_root=project_root,
            env_path=env_path,
        )
    return rc


# --------------------------------------------------------------------------- #
# WireGuard peer add                                                           #
# --------------------------------------------------------------------------- #

def add_wireguard_peer(name: str, pubkey: str, ip: str) -> int:
    return add_wireguard_peer_impl(
        name,
        pubkey,
        ip,
        wg_conf=Path("/etc/wireguard/wg0.conf"),
        os_geteuid=_os_geteuid,
        ok=_ok,
        warn=_warn,
        err=_err,
        info=_info,
    )
