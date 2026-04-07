from __future__ import annotations

from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest import MonkeyPatch

from rlm.runtime import ts_cli_shim


def test_ensure_cli_dist_prepares_terminal_before_cli(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    cli_dir = repo / "packages" / "cli"
    terminal_dir = repo / "packages" / "terminal"
    cli_dir.mkdir(parents=True)
    terminal_dir.mkdir(parents=True)

    monkeypatch.setattr(ts_cli_shim, "_repo_root", lambda: repo)
    monkeypatch.setattr(ts_cli_shim, "_npm_binary", lambda: "npm")

    calls: list[tuple[tuple[str, ...], Path]] = []

    def fake_run(
        cmd: Sequence[str],
        cwd: str | PathLike[str] | None = None,
        check: bool = False,
    ) -> SimpleNamespace:
        assert cwd is not None
        command = tuple(cmd)
        cwd_path = Path(cwd)
        calls.append((command, cwd_path))

        if cwd_path == terminal_dir and command == ("npm", "install"):
            (terminal_dir / "node_modules").mkdir()
            return SimpleNamespace(returncode=0)

        if cwd_path == terminal_dir and command == ("npm", "run", "build"):
            (terminal_dir / "dist").mkdir()
            (terminal_dir / "dist" / "index.js").write_text("export {};", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        if cwd_path == cli_dir and command == ("npm", "install"):
            assert (terminal_dir / "node_modules").exists()
            (cli_dir / "node_modules").mkdir()
            return SimpleNamespace(returncode=0)

        if cwd_path == cli_dir and command == ("npm", "run", "build"):
            (cli_dir / "dist").mkdir()
            (cli_dir / "dist" / "index.js").write_text("export {};", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        raise AssertionError(f"comando inesperado: {command} em {cwd_path}")

    monkeypatch.setattr(ts_cli_shim.subprocess, "run", fake_run)

    ensure_cli_dist = getattr(ts_cli_shim, "_ensure_cli_dist")
    typed_ensure_cli_dist = ensure_cli_dist
    typed_ensure_cli_dist = typed_ensure_cli_dist if callable(typed_ensure_cli_dist) else None
    assert typed_ensure_cli_dist is not None
    dist_entry = (typed_ensure_cli_dist)(cli_dir)

    assert dist_entry == cli_dir / "dist" / "index.js"
    assert calls == [
        (("npm", "install"), terminal_dir),
        (("npm", "run", "build"), terminal_dir),
        (("npm", "install"), cli_dir),
        (("npm", "run", "build"), cli_dir),
    ]


def test_main_routes_update_to_legacy_cli(monkeypatch: MonkeyPatch) -> None:
    def fake_typescript_cli(argv: list[str]) -> int:
        return 99

    def fake_legacy_cli(argv: list[str]) -> int:
        return 7

    monkeypatch.setattr(ts_cli_shim, "_should_use_legacy_cli", lambda: False)
    monkeypatch.setattr(ts_cli_shim, "_run_typescript_cli", fake_typescript_cli)
    monkeypatch.setattr(ts_cli_shim, "_run_legacy_cli", fake_legacy_cli)

    with pytest.raises(SystemExit) as exc_info:
        ts_cli_shim.main(["update"])

    assert exc_info.value.code == 7
