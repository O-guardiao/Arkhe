from __future__ import annotations

from pathlib import Path


def test_pyproject_includes_mcp_as_base_dependency() -> None:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")

    assert '"mcp>=1.0.0"' in text
    assert "[project.optional-dependencies]" in text
    assert "mcp = []" in text