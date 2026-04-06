"""Orquestração principal do onboarding — run_wizard + fluxo de 8 etapas."""

from __future__ import annotations

import platform
import textwrap
from pathlib import Path
from typing import Any

from rlm.cli.wizard.env_utils import (
    _Env,
    _load_existing_env,
    _probe_server,
    _resolve_env_path,
    _summarize_existing_config,
    _write_env,
)
from rlm.cli.wizard.prompter import WizardCancelledError, WizardPrompter
from rlm.cli.wizard.rich_prompter import RichPrompter
from rlm.cli.wizard.steps import (
    _step_channels,
    _step_llm_credentials,
    _step_security_tokens,
    _step_server_config,
)


# ═══════════════════════════════════════════════════════════════════════════ #
# run_wizard — ponto de entrada público                                      #
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


# ═══════════════════════════════════════════════════════════════════════════ #
# _run_onboarding — orquestrador central de 8 etapas                        #
# ═══════════════════════════════════════════════════════════════════════════ #


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
# _collect_config — coleta ramificada por flow                               #
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

    # ─────────────────────────────────── Canais de comunicação (bot tokens)
    config.update(_step_channels(p, existing, flow))

    # ─────────────────────────────────── Tokens de segurança
    config.update(_step_security_tokens(p, existing, flow))

    return config


# ═══════════════════════════════════════════════════════════════════════════ #
# _setup_daemon                                                              #
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
# _show_summary                                                              #
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
