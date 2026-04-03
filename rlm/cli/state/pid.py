"""Helpers de PID — fonte única (DRY) para todo o CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def read_pid_file(pid_path: Path) -> int | None:
    """Lê e retorna o PID de *pid_path*, ou ``None`` se inválido/ausente."""
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def pid_alive(pid: int) -> bool:
    """Retorna True se o processo *pid* está em execução.

    Windows: usa ``tasklist``; POSIX: ``os.kill(pid, 0)``.
    """
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/fi", f"PID eq {pid}", "/nh", "/fo", "csv"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return f'"{pid}"' in result.stdout or str(pid) in result.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def write_pid(pid_file: Path, pid: int) -> None:
    """Grava *pid* em *pid_file*, criando diretórios necessários."""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid))


def remove_pid(pid_file: Path) -> None:
    """Remove *pid_file* silenciosamente."""
    pid_file.unlink(missing_ok=True)
