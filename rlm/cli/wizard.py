"""RLM Wizard — configuração interativa estilo `openclaw onboard`.

Chamado por `rlm setup`. Detecta o sistema operacional, coleta configurações,
gera tokens de segurança, escreve o .env e, opcionalmente, instala o daemon
(systemd no Linux, launchd no macOS).
"""

from __future__ import annotations

import os
import platform
import secrets
import sys
import textwrap
from pathlib import Path


# --------------------------------------------------------------------------- #
# Utilitários de console (rich já é dependência do projeto)                   #
# --------------------------------------------------------------------------- #

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.table import Table

    _console = Console()
    _err_console = Console(stderr=True)

    def _print(msg: str = "", markup: bool = True) -> None:
        _console.print(msg, markup=markup)

    def _ask(question: str, default: str = "", password: bool = False) -> str:
        return Prompt.ask(question, default=default, password=password, console=_console)

    def _confirm(question: str, default: bool = True) -> bool:
        return Confirm.ask(question, default=default, console=_console)

    def _rule(title: str = "") -> None:
        _console.print(Rule(title, style="dim"))

    def _panel(body: str, title: str = "") -> None:
        _console.print(Panel(body, title=title, expand=False))

    HAS_RICH = True

except ImportError:  # pragma: no cover
    HAS_RICH = False

    def _print(msg: str = "", markup: bool = True) -> None:  # type: ignore[misc]
        # Remove basic markup tags for fallback
        import re
        clean = re.sub(r"\[/?[^\]]+\]", "", msg)
        print(clean)

    def _ask(question: str, default: str = "", password: bool = False) -> str:  # type: ignore[misc]
        suffix = f" [{default}]" if default else ""
        raw = input(f"{question}{suffix}: ").strip()
        return raw if raw else default

    def _confirm(question: str, default: bool = True) -> bool:  # type: ignore[misc]
        hint = "S/n" if default else "s/N"
        raw = input(f"{question} [{hint}]: ").strip().lower()
        if not raw:
            return default
        return raw in ("s", "sim", "y", "yes")

    def _rule(title: str = "") -> None:  # type: ignore[misc]
        print(f"\n{'─' * 50} {title}")

    def _panel(body: str, title: str = "") -> None:  # type: ignore[misc]
        print(f"\n[{title}]\n{body}\n")


# --------------------------------------------------------------------------- #
# Detecção de ambiente                                                         #
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

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
    existing: dict[str, str] = {}
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
                if k not in values:          # mantém chaves não gerenciadas
                    existing[k] = v.strip()
                    extra_lines.append(line)

    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# RLM — gerado por `rlm setup`", ""]

    # Chaves gerenciadas pelo wizard
    section_order = [
        ("# --- LLM ---", ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "RLM_MODEL"]),
        ("# --- Servidor ---", ["RLM_API_HOST", "RLM_API_PORT", "RLM_WS_HOST", "RLM_WS_PORT"]),
        ("# --- Segurança ---", ["RLM_WS_TOKEN", "RLM_HOOK_TOKEN"]),
    ]

    for section_title, keys in section_order:
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


# --------------------------------------------------------------------------- #
# Wizard principal                                                             #
# --------------------------------------------------------------------------- #

def run_wizard() -> int:
    """Executa o wizard interativo. Retorna exit code."""

    env = _Env()
    project_root = Path.cwd()
    env_path = _resolve_env_path(project_root)
    existing = _load_existing_env(env_path)

    # ------------------------------------------------------------------ banner
    _print()
    if HAS_RICH:
        _console.print(Panel(
            textwrap.dedent("""\
                [bold cyan]RLM Setup Wizard[/]
                Configuração interativa do [bold]Recursive Language Model[/]

                Este wizard irá:
                  • Configurar chaves de API e modelo LLM
                  • Gerar tokens de segurança (WS + Webhook)
                  • Salvar o arquivo [bold].env[/]
                  • (opcional) Instalar daemon systemd/launchd
            """),
            title="[bold green]● RLM Installer[/]",
            border_style="green",
            expand=False,
        ))
    else:
        _print("=== RLM Setup Wizard ===")
    _print()

    os_label = f"{env.system}" + (" (WSL)" if env.is_wsl else "")
    _print(f"[dim]Sistema detectado:[/] [bold]{os_label}[/]  |  "
           f"[dim]Python:[/] [bold]{platform.python_version()}[/]  |  "
           f"[dim]uv:[/] [bold]{'✓ ' + str(env.uv_path) if env.uv_path else '✗ não encontrado'}[/]")
    _print()

    config: dict[str, str] = {}

    # ----------------------------------------------------------------- Step 1: LLM
    _rule("Passo 1 — Credenciais LLM")

    # OpenAI
    default_key = existing.get("OPENAI_API_KEY", "")
    masked = f"sk-...{default_key[-6:]}" if len(default_key) > 10 else ""
    openai_key = _ask(
        f"[bold]OPENAI_API_KEY[/] (Enter para manter {masked})" if masked
        else "[bold]OPENAI_API_KEY[/]",
        default=default_key,
        password=not masked,
    ).strip()
    if openai_key:
        config["OPENAI_API_KEY"] = openai_key

    # Modelo
    default_model = existing.get("RLM_MODEL", "gpt-4o-mini")
    model = _ask(
        "[bold]Modelo padrão[/] (gpt-4o-mini, gpt-4o, claude-3-5-haiku, ...)",
        default=default_model,
    ).strip() or default_model
    config["RLM_MODEL"] = model

    # Testar chave (opcional)
    if openai_key and openai_key.startswith("sk-"):
        if _confirm("[dim]Testar chave OpenAI agora?[/]", default=False):
            _print("[dim]Verificando...[/]")
            ok = _test_openai_key(openai_key)
            if ok:
                _print("[bold green]✓[/] Chave válida — conexão OK")
            else:
                _print("[bold yellow]⚠[/]  Não foi possível validar — verifique a chave depois")

    _print()

    # ----------------------------------------------------------------- Step 2: Servidor
    _rule("Passo 2 — Endereços do servidor")

    api_host = _ask(
        "[bold]RLM_API_HOST[/] (bind da API REST)",
        default=existing.get("RLM_API_HOST", "127.0.0.1"),
    ).strip() or "127.0.0.1"
    config["RLM_API_HOST"] = api_host

    api_port = _ask(
        "[bold]RLM_API_PORT[/]",
        default=existing.get("RLM_API_PORT", "5000"),
    ).strip() or "5000"
    config["RLM_API_PORT"] = api_port

    ws_host = _ask(
        "[bold]RLM_WS_HOST[/] (bind do WebSocket)",
        default=existing.get("RLM_WS_HOST", "127.0.0.1"),
    ).strip() or "127.0.0.1"
    config["RLM_WS_HOST"] = ws_host

    ws_port = _ask(
        "[bold]RLM_WS_PORT[/]",
        default=existing.get("RLM_WS_PORT", "8765"),
    ).strip() or "8765"
    config["RLM_WS_PORT"] = ws_port

    _print()

    # ----------------------------------------------------------------- Step 3: Tokens
    _rule("Passo 3 — Tokens de segurança")

    existing_ws = existing.get("RLM_WS_TOKEN", "")
    if existing_ws:
        regen_ws = _confirm(
            f"[dim]RLM_WS_TOKEN já existe (…{existing_ws[-6:]}). Regenerar?[/]",
            default=False,
        )
        config["RLM_WS_TOKEN"] = secrets.token_hex(32) if regen_ws else existing_ws
    else:
        config["RLM_WS_TOKEN"] = secrets.token_hex(32)
        _print(f"[green]✓[/] RLM_WS_TOKEN gerado: [dim]…{config['RLM_WS_TOKEN'][-8:]}[/]")

    existing_hook = existing.get("RLM_HOOK_TOKEN", "")
    if existing_hook:
        regen_hook = _confirm(
            f"[dim]RLM_HOOK_TOKEN já existe (…{existing_hook[-6:]}). Regenerar?[/]",
            default=False,
        )
        config["RLM_HOOK_TOKEN"] = secrets.token_hex(32) if regen_hook else existing_hook
    else:
        config["RLM_HOOK_TOKEN"] = secrets.token_hex(32)
        _print(f"[green]✓[/] RLM_HOOK_TOKEN gerado: [dim]…{config['RLM_HOOK_TOKEN'][-8:]}[/]")

    _print()

    # ----------------------------------------------------------------- Escrever .env
    _rule("Salvando configuração")

    _write_env(env_path, config)
    _print(f"[bold green]✓[/] .env salvo em: [underline]{env_path}[/]")
    _print()

    # ----------------------------------------------------------------- Step 4: Daemon
    if env.has_systemd:
        _rule("Passo 4 — Daemon systemd")
        if _confirm("Instalar serviço systemd (rlm.service) para iniciar no boot?", default=True):
            from rlm.cli.service import install_systemd_service
            rc = install_systemd_service(project_root=project_root, env_path=env_path)
            if rc != 0:
                _print("[yellow]⚠[/]  Daemon systemd não pôde ser instalado (use `rlm start` manualmente)")
            _print()

    elif env.has_launchd:
        _rule("Passo 4 — Daemon launchd (macOS)")
        if _confirm("Instalar serviço launchd (~/.rlm/com.rlm.plist) para iniciar no login?", default=True):
            from rlm.cli.service import install_launchd_service
            rc = install_launchd_service(project_root=project_root, env_path=env_path)
            if rc != 0:
                _print("[yellow]⚠[/]  Daemon launchd não pôde ser instalado (use `rlm start` manualmente)")
            _print()

    else:
        _rule("Passo 4 — Inicialização automática")
        _print("[dim]Sistema não suporta systemd/launchd detectado.[/]")
        _print("[dim]Use [bold]rlm start[/] para iniciar manualmente ou configure seu gerenciador de processos.[/]")
        _print()

    # ----------------------------------------------------------------- Resumo final
    _rule()
    if HAS_RICH:
        table = Table(title="Configuração RLM", show_header=True, header_style="bold cyan")
        table.add_column("Variável", style="bold")
        table.add_column("Valor")
        for k, v in config.items():
            if "KEY" in k or "TOKEN" in k:
                display = f"{'*' * (len(v) - 6)}…{v[-6:]}" if len(v) > 8 else "***"
            else:
                display = v
            table.add_row(k, display)
        _console.print(table)
    else:
        _print("=== Resumo ===")
        for k, v in config.items():
            if "KEY" in k or "TOKEN" in k:
                v = f"***...{v[-6:]}"
            _print(f"  {k}={v}")

    _print()
    _print("[bold green]✓ Setup completo![/]")
    _print()
    _print(f"  Para iniciar o servidor:  [bold cyan]rlm start[/]")
    _print(f"  Para ver status:          [bold cyan]rlm status[/]")
    _print(f"  Para rotacionar tokens:   [bold cyan]rlm token rotate[/]")
    _print()

    return 0
