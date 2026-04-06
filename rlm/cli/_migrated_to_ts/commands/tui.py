from __future__ import annotations

from rlm.cli.context import CliContext, require_supported_runtime


def cmd_tui(args: object, *, context: CliContext | None = None) -> int:
    if not require_supported_runtime("arkhe tui"):
        return 1

    from rlm.cli.tui import run_workbench

    runtime_context = context or CliContext.from_environment()
    return run_workbench(
        runtime_context,
        client_id=getattr(args, "client_id", None),
        refresh_interval=float(getattr(args, "refresh_interval", 0.75)),
        once=bool(getattr(args, "once", False)),
    )