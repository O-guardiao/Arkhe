from __future__ import annotations

import argparse

from rlm.cli.context import CliContext, require_supported_runtime


def cmd_setup(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Executa o wizard interativo de configuração."""
    _ = context if context is not None else CliContext.from_environment()
    if not require_supported_runtime("arkhe setup"):
        return 1
    from rlm.cli.wizard import run_wizard

    return run_wizard(flow=getattr(args, "flow", None))