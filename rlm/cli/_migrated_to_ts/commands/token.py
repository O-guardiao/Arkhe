from __future__ import annotations

import argparse
import secrets
from pathlib import Path

from rlm.cli.context import CliContext, print_error, print_success


def cmd_token_rotate(args: argparse.Namespace, *, context: CliContext | None = None) -> int:  # noqa: ARG001
    """Regenera todos os tokens de segurança no .env."""
    current_context = context if context is not None else CliContext.from_environment()
    env_path = current_context.paths.env_path
    if not env_path.exists():
        print_error(f"Arquivo .env não encontrado em {env_path}")
        return 1

    text = env_path.read_text(encoding="utf-8")
    lines: list[str] = []
    rotated: list[str] = []
    managed_tokens = (
        "RLM_WS_TOKEN",
        "RLM_INTERNAL_TOKEN",
        "RLM_ADMIN_TOKEN",
        "RLM_HOOK_TOKEN",
        "RLM_API_TOKEN",
    )

    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in managed_tokens:
            new_token = secrets.token_hex(32)
            lines.append(f"{key}={new_token}")
            rotated.append(key)
        else:
            lines.append(line)

    existing_rotated = set(rotated)
    for name in managed_tokens:
        if name not in existing_rotated:
            lines.append(f"{name}={secrets.token_hex(32)}")
            rotated.append(name)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for name in rotated:
        print_success(f"{name} → rotacionado em {env_path}")

    try:
        from rich.console import Console

        Console().print("\n[yellow]⚠[/]  Reinicie o servidor para aplicar os novos tokens.")
    except ImportError:
        print("\n⚠  Reinicie o servidor para aplicar os novos tokens.")

    return 0