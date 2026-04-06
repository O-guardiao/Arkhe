"""Fixtures compartilhadas do Arkhe test suite."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_env(tmp_path: Path) -> Path:
    """Retorna caminho de .env temporário."""
    return tmp_path / ".env"


def resolve_arkhe_cli() -> list[str]:
    """Resolve o executável arkhe: instalado via pip ou fallback dev-mode."""
    path = shutil.which("arkhe")
    if path:
        return [path]
    return [sys.executable, "-m", "rlm"]
