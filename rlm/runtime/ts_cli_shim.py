from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cli_package_dir() -> Path:
    return _repo_root() / "packages" / "cli"


def _terminal_package_dir() -> Path:
    return _repo_root() / "packages" / "terminal"


def _should_use_legacy_cli() -> bool:
    value = (os.environ.get("RLM_USE_PYTHON_CLI_LEGACY") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _should_route_to_legacy_cli(argv: list[str]) -> bool:
    if not argv:
        return False
    return argv[0] == "update"


def _node_binary() -> str | None:
    return shutil.which("node")


def _npm_binary() -> str | None:
    candidates = ["npm.cmd", "npm"] if os.name == "nt" else ["npm"]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _ensure_node_package_ready(
    package_dir: Path,
    npm: str,
    *,
    install_error: str,
    build_error: str,
) -> None:
    if not (package_dir / "node_modules").exists():
        install = subprocess.run([npm, "install"], cwd=package_dir, check=False)
        if install.returncode != 0:
            raise RuntimeError(install_error)

    dist_entry = package_dir / "dist" / "index.js"
    if not dist_entry.exists():
        build = subprocess.run([npm, "run", "build"], cwd=package_dir, check=False)
        if build.returncode != 0 or not dist_entry.exists():
            raise RuntimeError(build_error)


def _ensure_cli_dist(package_dir: Path) -> Path:
    dist_entry = package_dir / "dist" / "index.js"
    if dist_entry.exists():
        return dist_entry

    npm = _npm_binary()
    if npm is None:
        raise RuntimeError(
            "npm não encontrado no PATH. Defina RLM_USE_PYTHON_CLI_LEGACY=true para usar a CLI Python legada."
        )

    terminal_dir = _terminal_package_dir()
    if terminal_dir.exists():
        _ensure_node_package_ready(
            terminal_dir,
            npm,
            install_error="Falha ao instalar dependências de packages/terminal via npm install.",
            build_error="Falha ao compilar packages/terminal via npm run build.",
        )

    _ensure_node_package_ready(
        package_dir,
        npm,
        install_error="Falha ao instalar dependências de packages/cli via npm install.",
        build_error="Falha ao compilar packages/cli via npm run build.",
    )

    return dist_entry


def _run_typescript_cli(argv: list[str]) -> int:
    package_dir = _cli_package_dir()
    if not package_dir.exists():
        raise FileNotFoundError(f"packages/cli não encontrado em {package_dir}")

    node = _node_binary()
    if node is None:
        raise FileNotFoundError("Node.js não encontrado no PATH")

    dist_entry = _ensure_cli_dist(package_dir)
    completed = subprocess.run([node, str(dist_entry), *argv], cwd=_repo_root(), check=False)
    return completed.returncode


def _run_legacy_cli(argv: list[str]) -> int:
    from rlm.cli.main import main as legacy_main

    try:
        legacy_main(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 0
    return 0


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)

    if _should_use_legacy_cli() or _should_route_to_legacy_cli(args):
        raise SystemExit(_run_legacy_cli(args))

    try:
        raise SystemExit(_run_typescript_cli(args))
    except FileNotFoundError:
        raise SystemExit(_run_legacy_cli(args))
