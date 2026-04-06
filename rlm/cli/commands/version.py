from __future__ import annotations

import argparse

from rlm.cli.context import CliContext


def cmd_version(args: argparse.Namespace, *, context: CliContext | None = None) -> int:  # noqa: ARG001
    """Exibe versão do Arkhe."""
    _ = context if context is not None else CliContext.from_environment()
    try:
        from importlib.metadata import version

        try:
            ver = version("arkhe")
        except Exception:
            ver = version("rlm")
    except Exception:
        ver = "0.1.0-dev"
    print(f"arkhe {ver}")
    return 0