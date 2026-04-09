"""Detecção de ambiente, constantes e helpers de I/O para .env."""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from rlm.cli.wizard.prompter import WizardPrompter

# ── Detecção de ambiente ────────────────────────────────────────────────── #


class _Env:
    """Informações sobre o ambiente de execução atual."""

    def __init__(self) -> None:
        self.system = platform.system()          # Linux | Darwin | Windows
        self.is_wsl = self._detect_wsl()
        self.is_linux = self.system == "Linux"
        self.is_macos = self.system == "Darwin"
        self.is_windows = self.system == "Windows"
        self.has_systemd = self._detect_systemd()
        self.has_launchd = self.is_macos
        self.uv_path = self._which("uv")
        self.python = sys.executable

    @staticmethod
    def _detect_wsl() -> bool:
        try:
            return "microsoft" in Path("/proc/version").read_text().lower()
        except Exception:
            return False

    @staticmethod
    def _detect_systemd() -> bool:
        if platform.system() != "Linux":
            return False
        return (
            Path("/run/systemd/system").exists()
            or Path("/sys/fs/cgroup/systemd").exists()
        )

    @staticmethod
    def _which(name: str) -> str | None:
        return shutil.which(name)


# ── Constantes ──────────────────────────────────────────────────────────── #

_LLM_SECTION_KEYS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "RLM_MODEL",
    "RLM_MODEL_PLANNER",
    "RLM_MODEL_WORKER",
    "RLM_MODEL_EVALUATOR",
    "RLM_MODEL_FAST",
    "RLM_MODEL_MINIREPL",
]

_SERVER_SECTION_KEYS = ["RLM_API_HOST", "RLM_API_PORT", "RLM_WS_HOST", "RLM_WS_PORT"]

_SECURITY_SECTION_KEYS = [
    "RLM_WS_TOKEN",
    "RLM_INTERNAL_TOKEN",
    "RLM_ADMIN_TOKEN",
    "RLM_HOOK_TOKEN",
    "RLM_API_TOKEN",
]

_CHANNEL_SECTION_KEYS = [
    # Telegram
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_OWNER_CHAT_ID",
    # Discord
    "DISCORD_BOT_TOKEN",
    "DISCORD_APP_PUBLIC_KEY",
    "DISCORD_APP_ID",
    # WhatsApp
    "WHATSAPP_TOKEN",
    "WHATSAPP_PHONE_ID",
    "WHATSAPP_VERIFY_TOKEN",
    # Slack
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    # Allowed chats (controle de acesso Telegram)
    "RLM_ALLOWED_CHATS",
]

_MANAGED_ENV_SECTIONS = [
    ("# --- LLM ---", _LLM_SECTION_KEYS),
    ("# --- Servidor ---", _SERVER_SECTION_KEYS),
    ("# --- Canais ---", _CHANNEL_SECTION_KEYS),
    ("# --- Segurança ---", _SECURITY_SECTION_KEYS),
]

_MANAGED_ENV_KEYS = {
    key
    for _section_title, keys in _MANAGED_ENV_SECTIONS
    for key in keys
}

_MODEL_ROLE_SPECS = [
    ("RLM_MODEL_PLANNER", "Planner", "orquestração raiz"),
    ("RLM_MODEL_WORKER", "Worker", "subagentes e delegação"),
    ("RLM_MODEL_EVALUATOR", "Evaluator", "crítica e validação"),
    ("RLM_MODEL_FAST", "Fast", "fast-path e respostas operacionais"),
    ("RLM_MODEL_MINIREPL", "MiniREPL", "classificação e loops baratos"),
]

_PROVIDER_MODEL_OPTIONS: dict[str, list[dict[str, str]]] = {
    "openai": [
        {"value": "gpt-5.4-mini", "label": "gpt-5.4-mini", "hint": "equilíbrio custo/qualidade (recomendado)"},
        {"value": "gpt-5.4", "label": "gpt-5.4", "hint": "mais capaz para planejamento"},
        {"value": "gpt-5.4-nano", "label": "gpt-5.4-nano", "hint": "rápido e barato para fast-path"},
        {"value": "gpt-5-nano", "label": "gpt-5-nano", "hint": "mínimo custo para tarefas curtas"},
        {"value": "gpt-4o-mini", "label": "gpt-4o-mini", "hint": "fallback estável e barato"},
        {"value": "gpt-4o", "label": "gpt-4o", "hint": "legado multimodal"},
        {"value": "o3-mini", "label": "o3-mini", "hint": "raciocínio avançado"},
    ],
    "anthropic": [
        {"value": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4", "hint": "equilíbrio custo/qualidade"},
        {"value": "claude-3-5-haiku-latest", "label": "Claude 3.5 Haiku", "hint": "rápido e barato"},
        {"value": "claude-opus-4-20250514", "label": "Claude Opus 4", "hint": "máxima capacidade"},
    ],
    "google": [
        {"value": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "hint": "rápido"},
        {"value": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "hint": "avançado"},
    ],
    "custom": [],
}

_PROVIDER_ROLE_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "RLM_MODEL_WORKER": "gpt-5.4-mini",
        "RLM_MODEL_EVALUATOR": "gpt-5.4-mini",
        "RLM_MODEL_FAST": "gpt-5.4-nano",
        "RLM_MODEL_MINIREPL": "gpt-5-nano",
    },
    "anthropic": {
        "RLM_MODEL_WORKER": "claude-3-5-haiku-latest",
        "RLM_MODEL_EVALUATOR": "claude-3-5-haiku-latest",
        "RLM_MODEL_FAST": "claude-3-5-haiku-latest",
        "RLM_MODEL_MINIREPL": "claude-3-5-haiku-latest",
    },
    "google": {
        "RLM_MODEL_WORKER": "gemini-2.5-flash",
        "RLM_MODEL_EVALUATOR": "gemini-2.5-flash",
        "RLM_MODEL_FAST": "gemini-2.5-flash",
        "RLM_MODEL_MINIREPL": "gemini-2.5-flash",
    },
}


# ── Helpers (.env I/O) ──────────────────────────────────────────────────── #


def _resolve_env_path(project_root: Path) -> Path:
    """Decide onde salvar o .env: na raiz do projeto ou em ~/.rlm/.env."""
    local = project_root / ".env"
    if local.exists():
        return local
    return local


def _load_existing_env(path: Path) -> dict[str, str]:
    """Lê .env existente em dict, preservando comentários no return."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_env(path: Path, values: dict[str, str]) -> None:
    """Escreve (ou atualiza) arquivo .env, preservando entradas não-RLM."""
    extra_lines: list[str] = []

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                extra_lines.append(line)
                continue
            if "=" in stripped:
                k, _, v = stripped.partition("=")
                k = k.strip()
                if k not in _MANAGED_ENV_KEYS:
                    extra_lines.append(line)

    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# RLM — gerado por `rlm setup`", ""]

    # Chaves gerenciadas pelo wizard
    for section_title, keys in _MANAGED_ENV_SECTIONS:
        written = False
        for k in keys:
            if k in values:
                if not written:
                    lines.append(section_title)
                    written = True
                lines.append(f"{k}={values[k]}")
        if written:
            lines.append("")

    # Entradas extras que vieram do .env anterior
    if extra_lines:
        lines.append("# --- outros ---")
        lines.extend(extra_lines)
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _test_openai_key(api_key: str) -> bool:
    """Testa a chave OpenAI com uma chamada mínima. Retorna True se OK."""
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        client.models.list()
        return True
    except Exception:
        return False


def _get_model_options(provider: str) -> list[dict[str, str]]:
    """Retorna catálogo de modelos do provider com opção manual embutida."""
    options = [dict(option) for option in _PROVIDER_MODEL_OPTIONS.get(provider, _PROVIDER_MODEL_OPTIONS["custom"])]
    if not any(option["value"] == "custom" for option in options):
        options.append(
            {
                "value": "custom",
                "label": "Digitar nome do modelo",
                "hint": "informar manualmente",
            }
        )
    return options


def _prompt_model_name(
    p: WizardPrompter,
    provider: str,
    message: str,
    default: str,
    options: list[dict[str, str]],
) -> str:
    """Seleciona um modelo do catálogo ou aceita entrada manual."""
    if provider == "custom":
        return p.text(
            message,
            default=default,
            placeholder="ex: gpt-5.4-mini, claude-sonnet-4 ou @openai/gpt-5-nano",
        )

    option_values = {option["value"] for option in options}
    initial_value = default if default in option_values else "custom"
    selected = p.select(message, options=options, initial_value=initial_value)
    if selected == "custom":
        return p.text(
            f"{message} (manual)",
            default=default,
            placeholder="ex: gpt-5.4-mini, claude-sonnet-4 ou @openai/gpt-5-nano",
        )
    return str(selected)


def _build_role_model_defaults(
    existing: dict[str, str],
    provider: str,
    base_model: str,
) -> dict[str, str]:
    """Monta defaults para os papéis de modelo usando base, existing e presets."""
    provider_defaults = _PROVIDER_ROLE_DEFAULTS.get(provider, {})
    worker_default = existing.get("RLM_MODEL_WORKER") or provider_defaults.get("RLM_MODEL_WORKER") or base_model
    fast_default = existing.get("RLM_MODEL_FAST") or provider_defaults.get("RLM_MODEL_FAST") or worker_default
    return {
        "RLM_MODEL_PLANNER": existing.get("RLM_MODEL_PLANNER") or base_model,
        "RLM_MODEL_WORKER": worker_default,
        "RLM_MODEL_EVALUATOR": existing.get("RLM_MODEL_EVALUATOR") or provider_defaults.get("RLM_MODEL_EVALUATOR") or worker_default,
        "RLM_MODEL_FAST": fast_default,
        "RLM_MODEL_MINIREPL": existing.get("RLM_MODEL_MINIREPL") or provider_defaults.get("RLM_MODEL_MINIREPL") or fast_default,
    }


def _format_role_model_summary(values: dict[str, str]) -> str:
    """Gera resumo compacto de roteamento por papel."""
    parts: list[str] = []
    for env_name, label, _description in _MODEL_ROLE_SPECS:
        model_name = values.get(env_name)
        if model_name:
            parts.append(f"{label}={model_name}")
    return "  • " + "\n  • ".join(parts) if parts else ""


def _summarize_existing_config(existing: dict[str, str]) -> str:
    """Gera resumo textual da config existente para exibição."""
    lines: list[str] = []
    if existing.get("OPENAI_API_KEY"):
        k = existing["OPENAI_API_KEY"]
        lines.append(f"  • OpenAI API Key: sk-…{k[-6:]}")
    if existing.get("ANTHROPIC_API_KEY"):
        lines.append(f"  • Anthropic API Key: …{existing['ANTHROPIC_API_KEY'][-6:]}")
    if existing.get("RLM_MODEL"):
        lines.append(f"  • Modelo base: {existing['RLM_MODEL']}")
    role_summary = _format_role_model_summary(existing)
    if role_summary:
        lines.append(role_summary)
    if existing.get("RLM_API_HOST"):
        lines.append(
            f"  • API: {existing.get('RLM_API_HOST', '?')}:{existing.get('RLM_API_PORT', '?')}"
        )
    if existing.get("RLM_WS_HOST"):
        lines.append(
            f"  • WebSocket: {existing.get('RLM_WS_HOST', '?')}:{existing.get('RLM_WS_PORT', '?')}"
        )
    token_count = sum(1 for k in existing if k.endswith("_TOKEN"))
    if token_count:
        lines.append(f"  • Tokens de segurança: {token_count} configurados")
    return "\n".join(lines) if lines else "  (vazio)"


def _probe_server(host: str, port: str) -> bool:
    """Testa se o servidor RLM está respondendo (best-effort)."""
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except Exception:
        return False
