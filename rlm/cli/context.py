from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import MutableMapping
from urllib.parse import urlparse

# Backward-compat: re-export from new homes so existing "from rlm.cli.context import X" still works
from rlm.cli.output import print_error, print_success  # noqa: F401
from rlm.cli.checks import doctor_runtime_requirement, require_supported_runtime  # noqa: F401


def _discover_project_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return current


def resolve_operator_api_base_url(env: MutableMapping[str, str], api_base_url: str) -> str:
    operator_host = str(env.get("RLM_OPERATOR_HOST", "") or "").strip()
    if operator_host:
        return operator_host.rstrip("/")

    configured = str(api_base_url or "").strip() or "http://127.0.0.1:5000"
    parsed = urlparse(configured if "://" in configured else f"http://{configured}")
    scheme = parsed.scheme or "http"
    host = str(parsed.hostname or env.get("RLM_API_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"

    try:
        port = parsed.port or 5000
    except ValueError:
        port = 5000

    if port <= 0 or port > 65535:
        port = 5000

    return f"{scheme}://{host}:{port}"


@dataclass(frozen=True, slots=True)
class CliPaths:
    cwd: Path
    home: Path
    project_root: Path
    state_root: Path
    launcher_state_path: Path
    runtime_dir: Path
    log_dir: Path
    env_path: Path
    skills_dir: Path

    @classmethod
    def discover(cls, *, cwd: Path | None = None, home: Path | None = None, env: MutableMapping[str, str] | None = None) -> CliPaths:
        current_cwd = (cwd or Path.cwd()).resolve()
        current_home = (home or Path.home()).resolve()
        project_root = _discover_project_root(current_cwd)
        state_root = current_home / ".rlm"
        env_path = current_cwd / ".env"
        if not env_path.exists():
            env_path = state_root / ".env"
        current_env = env or os.environ
        configured_skills_dir = current_env.get("RLM_SKILLS_DIR", "").strip()
        skills_dir = Path(configured_skills_dir).resolve() if configured_skills_dir else project_root / "rlm" / "skills"
        return cls(
            cwd=current_cwd,
            home=current_home,
            project_root=project_root,
            state_root=state_root,
            launcher_state_path=state_root / "launcher-state.json",
            runtime_dir=state_root / "run",
            log_dir=state_root / "logs",
            env_path=env_path,
            skills_dir=skills_dir,
        )


@dataclass(slots=True)
class CliContext:
    env: MutableMapping[str, str] = field(default_factory=lambda: dict(os.environ))
    cwd: Path = field(default_factory=Path.cwd)
    home: Path = field(default_factory=Path.home)
    paths: CliPaths = field(init=False)

    def __post_init__(self) -> None:
        self.paths = CliPaths.discover(cwd=self.cwd, home=self.home, env=self.env)

    @classmethod
    def from_environment(cls, *, load_env: bool = True) -> CliContext:
        context = cls()
        if load_env:
            context.load_env_file(override=False)
        return context

    def resolve_env_path(self) -> Path:
        return self.paths.env_path

    def refresh_paths(self) -> CliPaths:
        self.paths = CliPaths.discover(cwd=self.cwd, home=self.home, env=self.env)
        return self.paths

    def load_env_file(self, *, override: bool = False) -> Path | None:
        env_path = self.resolve_env_path()
        if not env_path.exists():
            return None

        try:
            from dotenv import load_dotenv

            load_dotenv(str(env_path), override=override)
        except ImportError:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if override or key not in self.env:
                    self.env[key] = value

        self.refresh_paths()
        return env_path

    def has_tool(self, name: str) -> bool:
        return shutil.which(name) is not None

    def api_host(self) -> str:
        return self.env.get("RLM_API_HOST", "127.0.0.1")

    def api_port(self) -> int:
        raw = self.env.get("RLM_API_PORT", "5000")
        try:
            port = int(raw)
        except (ValueError, TypeError):
            port = 5000
        if port < 1 or port > 65535:
            port = 5000
        return port

    def ws_host(self) -> str:
        return self.env.get("RLM_WS_HOST", self.api_host())

    def ws_port(self) -> int:
        return int(self.env.get("RLM_WS_PORT", "8765"))

    def api_base_url(self) -> str:
        return f"http://{self.api_host()}:{self.api_port()}"

    def operator_api_base_url(self) -> str:
        return resolve_operator_api_base_url(self.env, self.api_base_url())

    def ws_base_url(self) -> str:
        return f"ws://{self.ws_host()}:{self.ws_port()}"

    def docs_url(self) -> str:
        return f"{self.api_base_url()}/docs"

    def webchat_url(self) -> str:
        return f"{self.api_base_url()}/webchat"