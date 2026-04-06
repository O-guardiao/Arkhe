"""Checks de runtime para o CLI — validações de ambiente e pré-requisitos."""

from __future__ import annotations

import sys

from rlm.cli.output import print_error


def doctor_runtime_requirement() -> tuple[bool, str]:
    required = (3, 11)
    current = sys.version_info[:3]
    if current < required:
        return False, f"Python {current[0]}.{current[1]}.{current[2]} em uso; requer >= 3.11"
    return True, f"Python {current[0]}.{current[1]}.{current[2]}"


def require_supported_runtime(command_name: str) -> bool:
    ok, detail = doctor_runtime_requirement()
    if ok:
        return True
    print_error(f"{command_name} bloqueado: {detail}")
    return False
