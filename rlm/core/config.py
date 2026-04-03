"""
rlm.core.config — Configuração estruturada do RLM via rlm.toml + .env overlay.

Hierarquia de precedência (maior vence):
  1. Variáveis de ambiente (.env) — secrets + overrides operacionais
  2. rlm.toml — configuração estruturada, commitável, revisável
  3. Defaults hardcoded — garante que o sistema sempre sobe

Uso:
    from rlm.core.config import load_config, get_config
    cfg = load_config()           # na inicialização (api.py lifespan)
    cfg = get_config()            # depois, em qualquer módulo
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rlm.core.structured_log import get_logger

_log = get_logger("config")

# ── Compat: tomllib (3.11+) ou tomli (backport) ──────────────────────────
try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[import-untyped,no-redef]


# ── Dataclasses ───────────────────────────────────────────────────────────

@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    db_path: str = "rlm_sessions.db"
    state_root: str = "./rlm_states"
    skills_dir: str = "./rlm/skills"


@dataclass
class AgentConfig:
    model: str = "gpt-4o-mini"
    max_iterations: int = 30
    timeout: int = 120
    max_errors: int = 5


@dataclass
class MessageBusConfig:
    enabled: bool = False


@dataclass
class ProfileConfig:
    name: str = "default"
    description: str = ""
    model: str = "gpt-4o-mini"
    max_iterations: int = 30
    timeout: int = 120
    context_hint: str = ""
    permissions: list[str] = field(default_factory=list)
    response_style: str = "detailed"
    max_response_tokens: int = 2048
    exec_approval_required: bool = False


@dataclass
class ChannelConfig:
    disabled: bool = False
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    owner_chat_id: str = ""


@dataclass
class RLMConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    message_bus: MessageBusConfig = field(default_factory=MessageBusConfig)
    profiles: dict[str, ProfileConfig] = field(default_factory=dict)
    channels: dict[str, ChannelConfig] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def get_profile(self, name: str) -> ProfileConfig:
        """Retorna perfil por nome, fallback para 'default'."""
        return self.profiles.get(name, self.profiles.get("default", ProfileConfig()))


# ── Singleton ─────────────────────────────────────────────────────────────

_config_instance: RLMConfig | None = None
_config_lock = threading.Lock()


def load_config(toml_path: str = "rlm.toml") -> RLMConfig:
    """
    Carrega e retorna RLMConfig.  Idempotente — segunda chamada
    retorna a instância já carregada.

    Precedência: env var > rlm.toml > defaults hardcoded.
    """
    global _config_instance
    with _config_lock:
        if _config_instance is not None:
            return _config_instance

        path = Path(toml_path)
        raw: dict[str, Any] = {}

        if path.exists():
            with open(path, "rb") as f:
                raw = tomllib.load(f)
            _log.info(f"Loaded config from {path}")
        else:
            _log.info(f"No rlm.toml found at {path} — using defaults + env")

        # ── Server ────────────────────────────────────────────────────
        srv = raw.get("server", {})
        server = ServerConfig(
            host=os.getenv("RLM_HOST", srv.get("host", "0.0.0.0")),
            port=int(os.getenv("RLM_PORT", str(srv.get("port", 8000)))),
            db_path=os.getenv("RLM_DB_PATH", srv.get("db_path", "rlm_sessions.db")),
            state_root=os.getenv("RLM_STATE_ROOT", srv.get("state_root", "./rlm_states")),
            skills_dir=os.getenv("RLM_SKILLS_DIR", srv.get("skills_dir", "./rlm/skills")),
        )

        # ── Agent ─────────────────────────────────────────────────────
        ag = raw.get("agent", {})
        agent = AgentConfig(
            model=os.getenv("RLM_MODEL", ag.get("model", "gpt-4o-mini")),
            max_iterations=int(os.getenv("RLM_MAX_ITERATIONS", str(ag.get("max_iterations", 30)))),
            timeout=int(os.getenv("RLM_TIMEOUT", str(ag.get("timeout", 120)))),
            max_errors=int(os.getenv("RLM_MAX_ERRORS", str(ag.get("max_errors", 5)))),
        )

        # ── MessageBus ────────────────────────────────────────────────
        mb = raw.get("message_bus", {})
        message_bus = MessageBusConfig(
            enabled=os.getenv("RLM_USE_MESSAGE_BUS", str(mb.get("enabled", False))).lower()
            in ("true", "1", "yes"),
        )

        # ── Profiles ─────────────────────────────────────────────────
        profiles: dict[str, ProfileConfig] = {}
        for p in raw.get("profiles", []):
            known_fields = {f for f in ProfileConfig.__dataclass_fields__}
            filtered = {k: v for k, v in p.items() if k in known_fields}
            pc = ProfileConfig(**filtered)
            profiles[pc.name] = pc

        # Garante perfil "default" sempre presente
        if "default" not in profiles:
            profiles["default"] = ProfileConfig(name="default")

        # ── Channels ──────────────────────────────────────────────────
        channels: dict[str, ChannelConfig] = {}
        for ch_name, ch_data in raw.get("channels", {}).items():
            if isinstance(ch_data, dict):
                known = {f for f in ChannelConfig.__dataclass_fields__}
                filtered = {k: v for k, v in ch_data.items() if k in known}
                channels[ch_name] = ChannelConfig(**filtered)

        _config_instance = RLMConfig(
            server=server,
            agent=agent,
            message_bus=message_bus,
            profiles=profiles,
            channels=channels,
            raw=raw,
        )
        return _config_instance


def get_config() -> RLMConfig:
    """Retorna config já carregada. Levanta RuntimeError se load_config() não foi chamado."""
    if _config_instance is None:
        raise RuntimeError("Config não carregada. Chame load_config() no lifespan.")
    return _config_instance


def _reset_config() -> None:
    """Reset para testes. Não chamar em produção."""
    global _config_instance
    with _config_lock:
        _config_instance = None
