from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rlm.cli.context import CliContext
from rlm.cli.service_update import update_installation_impl


def _make_context(*, cwd: Path, home: Path, env: dict[str, str] | None = None) -> CliContext:
    return CliContext(env=env or {}, cwd=cwd, home=home)


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_update_uses_installed_repo_when_cwd_is_not_checkout(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    repo = home / ".arkhe" / "repo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()

    cwd = tmp_path / "workspace"
    cwd.mkdir()
    context = _make_context(cwd=cwd, home=home)
    monkeypatch.setattr(CliContext, "has_tool", lambda self, name: True)
    monkeypatch.setattr("rlm.cli.service_update._package_checkout_root", lambda: tmp_path / "not-a-checkout")

    calls: list[tuple[tuple[str, ...], Path]] = []

    def fake_run(cmd, cwd=None, capture_output=None, text=None):
        calls.append((tuple(cmd), Path(cwd)))
        command = tuple(cmd)
        if command[:3] == ("git", "status", "--porcelain"):
            return _completed()
        if command[:4] == ("git", "rev-parse", "--abbrev-ref", "HEAD"):
            return _completed(stdout="main\n")
        if command[:3] == ("git", "fetch", "origin"):
            return _completed()
        if command[:3] == ("git", "rev-list", "--left-right"):
            return _completed(stdout="0 0\n")
        raise AssertionError(f"comando inesperado: {command}")

    monkeypatch.setattr("rlm.cli.service_update.subprocess.run", fake_run)

    rc = update_installation_impl(
        context,
        check_only=True,
        restart=False,
        target_path=None,
        info=lambda msg: None,
        ok=lambda msg: None,
        err=lambda msg: None,
        services_are_running=lambda: False,
        stop_services=lambda: 0,
        start_services=lambda: 0,
    )

    assert rc == 0
    assert all(call_cwd == repo for _, call_cwd in calls)


def test_update_resolves_explicit_path_inside_checkout(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "deploy" / "repo"
    nested = repo / "rlm" / "cli"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    context = _make_context(cwd=tmp_path, home=tmp_path / "home")
    monkeypatch.setattr(CliContext, "has_tool", lambda self, name: True)

    observed_cwds: list[Path] = []

    def fake_run(cmd, cwd=None, capture_output=None, text=None):
        observed_cwds.append(Path(cwd))
        command = tuple(cmd)
        if command[:3] == ("git", "status", "--porcelain"):
            return _completed()
        if command[:4] == ("git", "rev-parse", "--abbrev-ref", "HEAD"):
            return _completed(stdout="main\n")
        if command[:3] == ("git", "fetch", "origin"):
            return _completed()
        if command[:3] == ("git", "rev-list", "--left-right"):
            return _completed(stdout="0 0\n")
        raise AssertionError(f"comando inesperado: {command}")

    monkeypatch.setattr("rlm.cli.service_update.subprocess.run", fake_run)

    rc = update_installation_impl(
        context,
        check_only=True,
        restart=False,
        target_path=str(nested),
        info=lambda msg: None,
        ok=lambda msg: None,
        err=lambda msg: None,
        services_are_running=lambda: False,
        stop_services=lambda: 0,
        start_services=lambda: 0,
    )

    assert rc == 0
    assert observed_cwds
    assert all(path == repo for path in observed_cwds)