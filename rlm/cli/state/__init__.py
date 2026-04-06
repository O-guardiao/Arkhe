"""Subpacote de estado do launcher — importações lazy sob demanda."""

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
    "remove_pid": "rlm.cli.state.pid",
    "write_pid": "rlm.cli.state.pid",
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        mod = importlib.import_module(_LAZY[name])
        value = getattr(mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
