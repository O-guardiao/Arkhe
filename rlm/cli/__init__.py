"""Arkhe CLI package.

Expose a lightweight public API for parser/context/dispatch helpers and
resolve submodules lazily so package attributes such as ``rlm.cli.service``
and ``rlm.cli.tui`` behave consistently with the rest of the codebase.
"""

from __future__ import annotations

import importlib
import importlib.util

from rlm.cli.command_specs import ArgumentSpec, CommandSpec, get_command_specs
from rlm.cli.context import CliContext, CliPaths
from rlm.cli.dispatch import build_dispatch_tables, dispatch_nested_command
from rlm.cli.parser import build_parser

__all__ = [
    "ArgumentSpec",
    "CommandSpec",
    "CliContext",
    "CliPaths",
    "build_dispatch_tables",
    "build_parser",
    "dispatch_nested_command",
    "get_command_specs",
]


def __getattr__(name: str):
    """PEP 562 lazy submodule access for the CLI package.

    This keeps ``from rlm.cli import service`` and attribute access such as
    ``rlm.cli.tui`` stable without eagerly importing every CLI surface.
    """
    module_name = f"{__name__}.{name}"
    if importlib.util.find_spec(module_name) is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = importlib.import_module(module_name)
    globals()[name] = value
    return value
