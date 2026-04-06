"""Backward-compat shim — DEPRECATED. Use ``rlm.cli.state.*`` diretamente.

Este módulo será removido numa próxima versão.
"""

import warnings as _warnings

_warnings.warn(
    "rlm.cli.launcher_state está deprecated; importe de rlm.cli.state.* diretamente.",
    DeprecationWarning,
    stacklevel=2,
)

_LAZY: dict[str, str] = {
    # diagnosis
    "build_launcher_state_diagnosis": "rlm.cli.state.diagnosis",
    "diagnose_launcher_state_alignment": "rlm.cli.state.diagnosis",
    # launcher
    "BootstrapRecord": "rlm.cli.state.launcher",
    "LauncherMetadata": "rlm.cli.state.launcher",
    "LauncherState": "rlm.cli.state.launcher",
    "RuntimeArtifacts": "rlm.cli.state.launcher",
    "load_launcher_state": "rlm.cli.state.launcher",
    "mark_bootstrap_success": "rlm.cli.state.launcher",
    "mark_daemon_installed": "rlm.cli.state.launcher",
    "mark_runtime_status": "rlm.cli.state.launcher",
    "mark_stopped": "rlm.cli.state.launcher",
    "mark_update": "rlm.cli.state.launcher",
    "save_launcher_state": "rlm.cli.state.launcher",
    "summarize_launcher_state": "rlm.cli.state.launcher",
    "update_launcher_state": "rlm.cli.state.launcher",
    # pid
    "pid_alive": "rlm.cli.state.pid",
    "read_pid_file": "rlm.cli.state.pid",
    # service_runtime
    "port_accepting_connections": "rlm.cli.service_runtime",
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        mod = importlib.import_module(_LAZY[name])
        value = getattr(mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
