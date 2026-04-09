"""Helpers de output para o CLI — rich quando disponível, fallback plain."""

from __future__ import annotations

import sys


def print_error(msg: str) -> None:
    try:
        from rich.console import Console

        Console(stderr=True).print(f"[bold red]✗ Erro:[/] {msg}")
    except ImportError:
        print(f"✗ Erro: {msg}", file=sys.stderr)


def print_success(msg: str) -> None:
    try:
        from rich.console import Console

        Console().print(f"[bold green]✓[/] {msg}")
    except ImportError:
        print(f"✓ {msg}")


# --------------------------------------------------------------------------- #
# Helpers de console usados pelo service facade e demais módulos              #
# --------------------------------------------------------------------------- #

_has_rich = False

try:
    from rich.console import Console
    from rich.table import Table

    _c = Console()
    _e = Console(stderr=True)

    def _ok(msg: str) -> None:   _c.print(f"[bold green]✓[/] {msg}")
    def _warn(msg: str) -> None: _c.print(f"[yellow]⚠[/]  {msg}")
    def _err(msg: str) -> None:  _e.print(f"[bold red]✗[/] {msg}")
    def _info(msg: str) -> None: _c.print(f"[dim]→[/] {msg}")

    _has_rich = True

except ImportError:
    Console = None  # type: ignore[assignment,misc]
    Table = None  # type: ignore[assignment,misc]

    def _ok(msg: str) -> None:   print(f"✓ {msg}")
    def _warn(msg: str) -> None: print(f"⚠  {msg}", file=sys.stderr)
    def _err(msg: str) -> None:  print(f"✗ {msg}", file=sys.stderr)
    def _info(msg: str) -> None: print(f"→ {msg}")


HAS_RICH = _has_rich


# Public aliases used by facade modules. Keep the underscore variants for
# backward compatibility with older internal imports.
ok = _ok
warn = _warn
err = _err
info = _info

__all__ = [
    "Console",
    "Table",
    "HAS_RICH",
    "err",
    "info",
    "ok",
    "print_error",
    "print_success",
    "warn",
]
