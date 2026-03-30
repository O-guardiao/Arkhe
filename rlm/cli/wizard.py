"""RLM Wizard — onboarding interativo estilo OpenClaw com fluxos ramificados.

Chamado por ``arkhe setup [--flow quickstart|advanced]``.
Detecta SO, coleta configurações via menus interativos,
gera tokens de segurança, escreve o ``.env`` e instala daemon.

Arquitetura:
    WizardPrompter (ABC)  →  RichPrompter (impl CLI)
    run_wizard()          →  orquestrador de 8 etapas com branches
"""

from __future__ import annotations

import abc
import os
import platform
import secrets
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

T = TypeVar("T")

if TYPE_CHECKING:
    from rich.console import Console as RichConsole
else:
    RichConsole = Any


# ═══════════════════════════════════════════════════════════════════════════ #
# WizardPrompter — Interface abstrata (espelha OpenClaw WizardPrompter)     #
# ═══════════════════════════════════════════════════════════════════════════ #

class WizardCancelledError(Exception):
    """Levantada quando o usuário cancela (Ctrl+C ou ESC)."""

    def __init__(self, message: str = "wizard cancelado") -> None:
        super().__init__(message)


class WizardPrompter(abc.ABC):
    """Contrato de I/O para todas as interações do wizard.

    Permite trocar implementação (CLI, web, API remota) sem
    alterar a lógica de onboarding.
    """

    @abc.abstractmethod
    def intro(self, title: str) -> None: ...

    @abc.abstractmethod
    def outro(self, message: str) -> None: ...

    @abc.abstractmethod
    def note(self, message: str, title: str = "") -> None: ...

    @abc.abstractmethod
    def select(
        self,
        message: str,
        options: list[dict[str, Any]],
        initial_value: Any = None,
    ) -> Any: ...

    @abc.abstractmethod
    def text(
        self,
        message: str,
        default: str = "",
        placeholder: str = "",
        password: bool = False,
        validate: Any = None,
    ) -> str: ...

    @abc.abstractmethod
    def confirm(self, message: str, default: bool = True) -> bool: ...

    @abc.abstractmethod
    def progress(self, label: str) -> "_ProgressHandle": ...


class _ProgressHandle:
    """Handle para spinners/progress."""

    def update(self, msg: str) -> None: ...  # pragma: no cover
    def stop(self, msg: str = "") -> None: ...  # pragma: no cover


# ═══════════════════════════════════════════════════════════════════════════ #
# RichPrompter — Implementação CLI com Rich                                 #
# ═══════════════════════════════════════════════════════════════════════════ #

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.table import Table

    HAS_RICH = True
except ImportError:  # pragma: no cover
    Console = None
    Panel = None
    Confirm = None
    Prompt = None
    Rule = None
    Table = None
    HAS_RICH = False


class _RichProgressHandle(_ProgressHandle):
    """Spinner usando rich.status."""

    def __init__(self, console: Any, label: str) -> None:
        self._console = console
        self._status = console.status(label, spinner="dots")
        self._status.start()

    def update(self, msg: str) -> None:
        self._status.update(msg)

    def stop(self, msg: str = "") -> None:
        self._status.stop()
        if msg:
            self._console.print(msg)


class _PlainProgressHandle(_ProgressHandle):
    """Fallback sem rich."""

    def __init__(self, label: str) -> None:
        print(f"  ⏳ {label}")

    def update(self, msg: str) -> None:
        print(f"  … {msg}")

    def stop(self, msg: str = "") -> None:
        if msg:
            import re
            print(re.sub(r"\[/?[^\]]+\]", "", msg))


def _clean_markup(text: str) -> str:
    """Remove tags de markup Rich para fallback plain."""
    import re
    return re.sub(r"\[/?[^\]]+\]", "", text)


class RichPrompter(WizardPrompter):
    """Implementação do WizardPrompter usando Rich."""

    def __init__(self) -> None:
        self._console: RichConsole | None = None
        if HAS_RICH:
            assert Console is not None
            self._console = Console()

    # --- output ---

    def _print(self, msg: str = "") -> None:
        if self._console:
            self._console.print(msg)
        else:
            print(_clean_markup(msg))

    def intro(self, title: str) -> None:
        if self._console:
            assert Panel is not None
            self._console.print(Panel(title, border_style="green", expand=False))
        else:
            print(f"\n{'═' * 60}")
            print(f"  {_clean_markup(title)}")
            print(f"{'═' * 60}")
        self._print()

    def outro(self, message: str) -> None:
        if self._console:
            assert Rule is not None
            self._console.print()
            self._console.print(Rule(style="green"))
            self._console.print(f"  {message}")
            self._console.print()
        else:
            print(f"\n{'─' * 60}")
            print(f"  {_clean_markup(message)}")

    def note(self, message: str, title: str = "") -> None:
        if self._console:
            assert Panel is not None
            self._console.print(Panel(message, title=title or None, expand=False))
        else:
            if title:
                print(f"\n[{_clean_markup(title)}]")
            print(_clean_markup(message))

    # --- input ---

    def select(
        self,
        message: str,
        options: list[dict[str, Any]],
        initial_value: Any = None,
    ) -> Any:
        """Menu de seleção única. options: [{"value": X, "label": "...", "hint": "..."}]."""
        self._print()
        self._print(f"[bold]{message}[/]" if self._console else _clean_markup(message))
        for i, opt in enumerate(options, 1):
            hint = f"  [dim]({opt['hint']})[/]" if opt.get("hint") else ""
            if self._console:
                self._print(f"  [cyan]{i}[/]) {opt['label']}{hint}")
            else:
                h = f"  ({opt.get('hint', '')})" if opt.get("hint") else ""
                print(f"  {i}) {_clean_markup(opt['label'])}{h}")

        # Encontrar default numérico
        default_idx = "1"
        if initial_value is not None:
            for i, opt in enumerate(options, 1):
                if opt["value"] == initial_value:
                    default_idx = str(i)
                    break

        while True:
            try:
                if self._console:
                    assert Prompt is not None
                    raw = Prompt.ask("Escolha", default=default_idx, console=self._console)
                else:
                    raw = input(f"  Escolha [{default_idx}]: ").strip() or default_idx
            except (KeyboardInterrupt, EOFError):
                raise WizardCancelledError()

            try:
                idx = int(raw)
                if 1 <= idx <= len(options):
                    return options[idx - 1]["value"]
            except ValueError:
                pass
            self._print("[yellow]  Opção inválida. Tente novamente.[/]" if self._console
                        else "  Opção inválida. Tente novamente.")

    def text(
        self,
        message: str,
        default: str = "",
        placeholder: str = "",
        password: bool = False,
        validate: Any = None,
    ) -> str:
        hint = f" ({placeholder})" if placeholder and not default else ""
        while True:
            try:
                if self._console:
                    assert Prompt is not None
                    raw = Prompt.ask(
                        f"{message}{hint}",
                        default=default or None,
                        password=password,
                        console=self._console,
                    )
                else:
                    suffix = f" [{default}]" if default else ""
                    raw = input(f"{_clean_markup(message)}{suffix}: ").strip()
                    if not raw:
                        raw = default
            except (KeyboardInterrupt, EOFError):
                raise WizardCancelledError()

            raw = (raw or "").strip()
            if validate:
                err = validate(raw)
                if err:
                    self._print(f"[yellow]  {err}[/]" if self._console else f"  {err}")
                    continue
            return raw

    def confirm(self, message: str, default: bool = True) -> bool:
        try:
            if self._console:
                assert Confirm is not None
                return Confirm.ask(message, default=default, console=self._console)
            else:
                hint = "S/n" if default else "s/N"
                raw = input(f"{_clean_markup(message)} [{hint}]: ").strip().lower()
                if not raw:
                    return default
                return raw in ("s", "sim", "y", "yes")
        except (KeyboardInterrupt, EOFError):
            raise WizardCancelledError()

    def progress(self, label: str) -> _ProgressHandle:
        if self._console:
            return _RichProgressHandle(self._console, label)
        return _PlainProgressHandle(label)


# ═══════════════════════════════════════════════════════════════════════════ #
# Compat aliases — funções usadas diretamente por código legado              #
# ═══════════════════════════════════════════════════════════════════════════ #

_default_prompter = RichPrompter()
_console = getattr(_default_prompter, "_console", None)


def _print(msg: str = "", markup: bool = True) -> None:
    _default_prompter._print(msg)


def _ask(question: str, default: str = "", password: bool = False) -> str:
    return _default_prompter.text(question, default=default, password=password)


def _confirm(question: str, default: bool = True) -> bool:
    return _default_prompter.confirm(question, default=default)


def _rule(title: str = "") -> None:
    if _console:
        assert Rule is not None
        _console.print(Rule(title, style="dim"))
    else:
        print(f"\n{'─' * 50} {title}")


def _panel(body: str, title: str = "") -> None:
    _default_prompter.note(body, title=title)


# ═══════════════════════════════════════════════════════════════════════════ #
# Detecção de ambiente                                                       #
# ═══════════════════════════════════════════════════════════════════════════ #

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
        import shutil
        return shutil.which(name)


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

_MANAGED_ENV_SECTIONS = [
    ("# --- LLM ---", _LLM_SECTION_KEYS),
    ("# --- Servidor ---", _SERVER_SECTION_KEYS),
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


# ═══════════════════════════════════════════════════════════════════════════ #
# Helpers (.env I/O)                                                         #
# ═══════════════════════════════════════════════════════════════════════════ #

def _resolve_env_path(project_root: Path) -> Path:
    """Decide onde salvar o .env: na raiz do projeto ou em ~/.rlm/.env."""
    local = project_root / ".env"
    if local.exists():
        return local
    # Se não existe, prefere local ao projeto
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


# ═══════════════════════════════════════════════════════════════════════════ #
# Wizard principal — fluxo com ramificação QuickStart / Advanced             #
# ═══════════════════════════════════════════════════════════════════════════ #

def run_wizard(flow: str | None = None) -> int:
    """Executa o wizard interativo com fluxos QuickStart e Advanced.

    Args:
        flow: ``"quickstart"``, ``"advanced"`` ou ``None`` (pergunta ao usuário).

    Returns:
        Exit code (0 = sucesso).
    """
    p = RichPrompter()
    env = _Env()
    project_root = Path.cwd()
    env_path = _resolve_env_path(project_root)
    existing = _load_existing_env(env_path)

    try:
        return _run_onboarding(p, env, project_root, env_path, existing, flow)
    except WizardCancelledError:
        p.outro("[yellow]Setup cancelado pelo usuário.[/]")
        return 1


def _run_onboarding(
    p: WizardPrompter,
    env: _Env,
    project_root: Path,
    env_path: Path,
    existing: dict[str, str],
    flow: str | None,
) -> int:
    """Orquestrador central do onboarding — espelha runOnboardingWizard do OpenClaw."""

    # ══════════════════════════════════════════════ Etapa 1: Intro + Banner
    os_label = f"{env.system}" + (" (WSL)" if env.is_wsl else "")
    uv_label = f"✓ {env.uv_path}" if env.uv_path else "✗ ausente"

    p.intro(
        f"[bold cyan]Arkhe Setup Wizard[/]\n"
        f"Configuração interativa do [bold]Recursive Language Model[/]\n\n"
        f"[dim]Sistema:[/] [bold]{os_label}[/]  •  "
        f"[dim]Python:[/] [bold]{platform.python_version()}[/]  •  "
        f"[dim]uv:[/] [bold]{uv_label}[/]"
    )

    # ══════════════════════════════════════════════ Etapa 2: Aviso de segurança
    p.note(
        textwrap.dedent("""\
        O Arkhe executa modelos de linguagem que podem gerar
        código e comandos de sistema. Tokens de segurança serão
        gerados automaticamente para proteger WebSocket, API
        REST e rotas administrativas.

        É sua responsabilidade:
          • Não expor as portas sem autenticação
          • Manter a chave API LLM confidencial
          • Revisar comandos antes de executá-los"""),
        title="⚠ Aviso de segurança",
    )

    if not p.confirm("Entendo os riscos. Continuar?", default=True):
        raise WizardCancelledError("riscos não aceitos")

    # ══════════════════════════════════════════════ Etapa 3: Config existente
    config_action = "fresh"  # fresh | keep | modify | reset

    if existing:
        p.note(_summarize_existing_config(existing), title="Configuração existente detectada")

        config_action = p.select(
            "Como deseja proceder com a configuração existente?",
            options=[
                {"value": "keep", "label": "Manter valores atuais", "hint": "prosseguir sem alterar"},
                {"value": "modify", "label": "Modificar valores", "hint": "editar variáveis uma a uma"},
                {"value": "reset", "label": "Resetar tudo", "hint": "começar do zero"},
            ],
            initial_value="keep",
        )

        if config_action == "reset":
            scope = p.select(
                "O que resetar?",
                options=[
                    {"value": "config", "label": "Apenas configuração (.env)", "hint": "mantém dados"},
                    {"value": "full", "label": "Reset completo", "hint": "remove .env e regenera tudo"},
                ],
                initial_value="config",
            )
            if scope == "full":
                if env_path.exists():
                    env_path.unlink()
                p.note("Configuração removida. Começando do zero.", title="Reset")
            existing = {}
            config_action = "fresh"

    # ══════════════════════════════════════════════ Etapa 4: Escolher Flow
    if flow and flow in ("quickstart", "advanced"):
        chosen_flow = flow
    else:
        chosen_flow = p.select(
            "Modo de configuração",
            options=[
                {
                    "value": "quickstart",
                    "label": "⚡ QuickStart",
                    "hint": "API key + defaults automáticos → pronto em 30s",
                },
                {
                    "value": "advanced",
                    "label": "🔧 Avançado",
                    "hint": "configurar porta, bind, modelo, tokens individualmente",
                },
            ],
            initial_value="quickstart",
        )

    # Se existente com keep, pula etapas de coleta
    if config_action == "keep":
        config = dict(existing)
        p.note("Usando configuração existente sem alterações.", title="Config mantida")
    else:
        config = _collect_config(p, env, existing, chosen_flow, config_action)

    # ══════════════════════════════════════════════ Etapa 5: Probe servidor
    api_host = config.get("RLM_API_HOST", "127.0.0.1")
    api_port = config.get("RLM_API_PORT", "5000")
    ws_host = config.get("RLM_WS_HOST", "127.0.0.1")
    ws_port = config.get("RLM_WS_PORT", "8765")

    spinner = p.progress("Verificando se o servidor já está rodando…")
    api_alive = _probe_server(api_host, api_port)
    ws_alive = _probe_server(ws_host, ws_port)
    if api_alive or ws_alive:
        spinner.stop(f"[yellow]⚠[/]  Servidor detectado ("
                     f"API={'✓' if api_alive else '✗'} "
                     f"WS={'✓' if ws_alive else '✗'})")
        p.note(
            "O servidor Arkhe já está em execução.\n"
            "As novas configurações serão aplicadas após reinício.\n"
            "Use [bold]arkhe stop && arkhe start[/] após o setup.",
            title="Servidor ativo",
        )
    else:
        spinner.stop("[dim]Nenhum servidor ativo detectado.[/]")

    # ══════════════════════════════════════════════ Etapa 6: Salvar .env
    _write_env(env_path, config)
    p.note(f"Arquivo salvo em: {env_path}", title="✓ .env gravado")

    # ══════════════════════════════════════════════ Etapa 7: Daemon
    _setup_daemon(p, env, project_root, env_path, chosen_flow)

    # ══════════════════════════════════════════════ Etapa 8: Resumo + Finalização
    _show_summary(p, config)

    next_action = p.select(
        "O que fazer agora?",
        options=[
            {"value": "start", "label": "🚀 Iniciar servidor agora", "hint": "arkhe start"},
            {"value": "status", "label": "📊 Ver status", "hint": "arkhe status"},
            {"value": "later", "label": "⏰ Fazer isso depois", "hint": "sair do wizard"},
        ],
        initial_value="start",
    )

    if next_action == "start":
        p.note("Iniciando servidor…", title="Start")
        from rlm.cli.service import start_services
        start_services(foreground=False, api_only=False, ws_only=False)
        # Health check
        spinner = p.progress("Aguardando servidor ficar pronto…")
        import time
        started = False
        for _ in range(15):
            time.sleep(1)
            if _probe_server(api_host, api_port):
                started = True
                break
        if started:
            spinner.stop("[bold green]✓[/] Servidor Arkhe ativo e respondendo!")
        else:
            spinner.stop("[yellow]⚠[/]  Servidor não respondeu em 15s. Verifique com [bold]arkhe status[/]")

    elif next_action == "status":
        from rlm.cli.service import show_status
        show_status()

    p.outro("[bold green]✓ Onboarding completo![/]  Execute [bold cyan]arkhe status[/] a qualquer momento.")
    return 0


# ═══════════════════════════════════════════════════════════════════════════ #
# Coleta de configuração — ramifica por flow                                 #
# ═══════════════════════════════════════════════════════════════════════════ #

def _collect_config(
    p: WizardPrompter,
    env: _Env,
    existing: dict[str, str],
    flow: str,
    config_action: str,
) -> dict[str, str]:
    """Coleta todas as variáveis de config, respeitando o flow escolhido."""
    config: dict[str, str] = {}

    # ─────────────────────────────────── LLM Provider (ambos os flows)
    config.update(_step_llm_credentials(p, existing, flow))

    # ─────────────────────────────────── Servidor (QuickStart usa defaults)
    config.update(_step_server_config(p, existing, flow))

    # ─────────────────────────────────── Tokens de segurança
    config.update(_step_security_tokens(p, existing, flow))

    return config


def _step_llm_credentials(
    p: WizardPrompter,
    existing: dict[str, str],
    flow: str,
) -> dict[str, str]:
    """Coleta credenciais LLM — provider + modelo."""
    config: dict[str, str] = {}

    # Provider selection
    provider = p.select(
        "Provedor LLM",
        options=[
            {"value": "openai", "label": "OpenAI", "hint": "GPT-5.4, mini, nano"},
            {"value": "anthropic", "label": "Anthropic", "hint": "Claude 3.5, Claude 4"},
            {"value": "google", "label": "Google AI", "hint": "Gemini Pro, Gemini Flash"},
            {"value": "custom", "label": "Outro / OpenAI-compatível", "hint": "LM Studio, Ollama, etc."},
            {"value": "skip", "label": "Pular", "hint": "configurar depois"},
        ],
        initial_value="openai",
    )

    if provider == "skip":
        # Mantém existentes se houver
        for k in _LLM_SECTION_KEYS:
            if existing.get(k):
                config[k] = existing[k]
        return config

    # Modelo
    models = _get_model_options(provider)
    existing_model = existing.get("RLM_MODEL", "")

    if flow == "quickstart" and provider != "custom":
        # QuickStart: usa existente ou primeiro modelo recomendado automaticamente
        selected_model = existing_model or models[0]["value"]
        p.note(f"Modelo selecionado automaticamente: [bold]{selected_model}[/]", title="QuickStart")
    else:
        selected_model = _prompt_model_name(
            p,
            provider,
            "Modelo padrão",
            existing_model,
            models,
        )

    config["RLM_MODEL"] = selected_model

    has_role_models = any(existing.get(env_name) for env_name, _label, _description in _MODEL_ROLE_SPECS)
    route_mode_default = "single" if flow == "quickstart" else "recommended"
    if has_role_models:
        route_mode_default = "manual"

    route_mode = p.select(
        "Estratégia de modelos",
        options=[
            {"value": "single", "label": "Um único modelo", "hint": "usa apenas RLM_MODEL e limpa overrides antigos"},
            {"value": "recommended", "label": "Split recomendado", "hint": "preenche planner, worker, fast e minirepl automaticamente"},
            {"value": "manual", "label": "Escolher por papel", "hint": "configurar planner, worker, evaluator, fast e minirepl"},
        ],
        initial_value=route_mode_default,
    )

    role_defaults = _build_role_model_defaults(existing, provider, selected_model)
    if route_mode == "recommended":
        config.update(role_defaults)
        p.note(_format_role_model_summary(role_defaults), title="Modelos por papel")
    elif route_mode == "manual":
        for env_name, label, description in _MODEL_ROLE_SPECS:
            config[env_name] = _prompt_model_name(
                p,
                provider,
                f"Modelo para {label} ({description})",
                role_defaults[env_name],
                models,
            )
        p.note(_format_role_model_summary(config), title="Modelos por papel")
    else:
        p.note(
            f"  • Todos os papéis usarão [bold]{selected_model}[/]\n"
            "  • Overrides RLM_MODEL_* antigos serão removidos ao salvar",
            title="Modelos",
        )

    # API Key
    key_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "custom": "OPENAI_API_KEY",
    }
    key_name = key_map[provider]
    existing_key = existing.get(key_name, "")
    masked = f"…{existing_key[-6:]}" if len(existing_key) > 10 else ""

    if masked and flow == "quickstart":
        # QuickStart com key existente: mantém
        config[key_name] = existing_key
        p.note(f"Usando API key existente ({masked})", title="QuickStart")
    else:
        prompt_msg = f"API Key ({key_name})"
        if masked:
            prompt_msg += f"  [dim]atual: {masked}[/]"

        api_key = p.text(prompt_msg, default=existing_key, password=not bool(masked))
        if api_key:
            config[key_name] = api_key

        # Validar OpenAI key
        if provider == "openai" and api_key and api_key.startswith("sk-"):
            if p.confirm("Testar chave OpenAI agora?", default=False):
                spinner = p.progress("Verificando…")
                ok = _test_openai_key(api_key)
                if ok:
                    spinner.stop("[bold green]✓[/] Chave válida — conexão OK")
                else:
                    spinner.stop("[bold yellow]⚠[/]  Não validada — verifique depois")

    return config


def _step_server_config(
    p: WizardPrompter,
    existing: dict[str, str],
    flow: str,
) -> dict[str, str]:
    """Coleta endereço/porta do servidor."""
    config: dict[str, str] = {}

    defaults = {
        "RLM_API_HOST": existing.get("RLM_API_HOST", "127.0.0.1"),
        "RLM_API_PORT": existing.get("RLM_API_PORT", "5000"),
        "RLM_WS_HOST": existing.get("RLM_WS_HOST", "127.0.0.1"),
        "RLM_WS_PORT": existing.get("RLM_WS_PORT", "8765"),
    }

    if flow == "quickstart":
        # QuickStart: usa defaults direto
        config.update(defaults)
        p.note(
            f"  • API REST:  {defaults['RLM_API_HOST']}:{defaults['RLM_API_PORT']}\n"
            f"  • WebSocket: {defaults['RLM_WS_HOST']}:{defaults['RLM_WS_PORT']}",
            title="Servidor (defaults)",
        )
        return config

    # Advanced: perguntar tudo
    def _validate_port(v: str) -> str | None:
        try:
            n = int(v)
            if not (1 <= n <= 65535):
                return "Porta deve estar entre 1 e 65535"
        except ValueError:
            return "Deve ser um número inteiro"
        return None

    bind_choice = p.select(
        "Bind do servidor (quem pode acessar?)",
        options=[
            {"value": "loopback", "label": "Loopback (127.0.0.1)", "hint": "apenas esta máquina"},
            {"value": "lan", "label": "LAN (0.0.0.0)", "hint": "acessível na rede local"},
            {"value": "custom", "label": "IP customizado", "hint": "definir manualmente"},
        ],
        initial_value="loopback",
    )

    if bind_choice == "loopback":
        host = "127.0.0.1"
    elif bind_choice == "lan":
        host = "0.0.0.0"
    else:
        host = p.text(
            "Endereço IP para bind",
            default=defaults["RLM_API_HOST"],
            validate=lambda v: None if v else "IP não pode ser vazio",
        )

    config["RLM_API_HOST"] = host
    config["RLM_WS_HOST"] = host

    config["RLM_API_PORT"] = p.text(
        "Porta da API REST",
        default=defaults["RLM_API_PORT"],
        validate=_validate_port,
    )
    config["RLM_WS_PORT"] = p.text(
        "Porta do WebSocket",
        default=defaults["RLM_WS_PORT"],
        validate=_validate_port,
    )

    return config


def _step_security_tokens(
    p: WizardPrompter,
    existing: dict[str, str],
    flow: str,
) -> dict[str, str]:
    """Gera ou mantém tokens de segurança."""
    config: dict[str, str] = {}

    token_specs = [
        ("RLM_WS_TOKEN", "WebSocket / observabilidade"),
        ("RLM_INTERNAL_TOKEN", "API interna /webhook/{client_id}"),
        ("RLM_ADMIN_TOKEN", "rotas administrativas e health"),
        ("RLM_HOOK_TOKEN", "webhooks externos /api/hooks"),
        ("RLM_API_TOKEN", "API OpenAI-compatible /v1"),
    ]

    if flow == "quickstart":
        # QuickStart: gera tudo automaticamente, mantém existentes
        generated = 0
        kept = 0
        for env_name, _label in token_specs:
            if existing.get(env_name):
                config[env_name] = existing[env_name]
                kept += 1
            else:
                config[env_name] = secrets.token_hex(32)
                generated += 1

        parts = []
        if generated:
            parts.append(f"{generated} gerados")
        if kept:
            parts.append(f"{kept} mantidos")
        p.note(
            f"Tokens de segurança: {', '.join(parts)}",
            title="Segurança (auto)",
        )
        return config

    # Advanced: perguntar por cada token existente
    for env_name, label in token_specs:
        existing_value = existing.get(env_name, "")
        if existing_value:
            regenerate = p.confirm(
                f"{env_name} ({label}) já existe (…{existing_value[-6:]}). Regenerar?",
                default=False,
            )
            config[env_name] = secrets.token_hex(32) if regenerate else existing_value
        else:
            config[env_name] = secrets.token_hex(32)

    p.note(f"{len(token_specs)} tokens de segurança configurados.", title="✓ Segurança")
    return config


# ═══════════════════════════════════════════════════════════════════════════ #
# Daemon setup                                                               #
# ═══════════════════════════════════════════════════════════════════════════ #

def _setup_daemon(
    p: WizardPrompter,
    env: _Env,
    project_root: Path,
    env_path: Path,
    flow: str,
) -> None:
    """Instala daemon systemd/launchd — auto no quickstart, pergunta no advanced."""

    if not (env.has_systemd or env.has_launchd):
        p.note(
            "Nenhum gerenciador de serviços detectado (systemd/launchd).\n"
            "Use [bold]arkhe start[/] para iniciar manualmente.",
            title="Daemon",
        )
        return

    if flow == "quickstart":
        install = True
        p.note("Serviço será instalado automaticamente.", title="Daemon (QuickStart)")
    else:
        daemon_type = "systemd" if env.has_systemd else "launchd"
        install = p.confirm(
            f"Instalar serviço {daemon_type} para iniciar no boot?",
            default=True,
        )

    if not install:
        return

    spinner = p.progress("Instalando serviço…")
    try:
        if env.has_systemd:
            from rlm.cli.service import install_systemd_service
            rc = install_systemd_service(project_root=project_root, env_path=env_path)
        else:
            from rlm.cli.service import install_launchd_service
            rc = install_launchd_service(project_root=project_root, env_path=env_path)

        if rc == 0:
            spinner.stop("[bold green]✓[/] Serviço instalado com sucesso")
        else:
            spinner.stop("[yellow]⚠[/]  Serviço não pôde ser instalado (use arkhe start manualmente)")
    except Exception:
        spinner.stop("[yellow]⚠[/]  Erro ao instalar serviço")


# ═══════════════════════════════════════════════════════════════════════════ #
# Resumo final                                                               #
# ═══════════════════════════════════════════════════════════════════════════ #

def _show_summary(p: WizardPrompter, config: dict[str, str]) -> None:
    """Exibe resumo da configuração gerada."""
    lines: list[str] = []
    for k, v in config.items():
        if "KEY" in k or "TOKEN" in k:
            display = f"{'*' * min(len(v) - 6, 20)}…{v[-6:]}" if len(v) > 8 else "***"
        else:
            display = v
        lines.append(f"  {k} = {display}")
    p.note("\n".join(lines), title="Resumo da configuração")
