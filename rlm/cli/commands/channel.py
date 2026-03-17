from __future__ import annotations

import argparse

from rlm.cli.context import CliContext


def cmd_channel_list(args: argparse.Namespace, *, context: CliContext | None = None) -> int:  # noqa: ARG001
    """Mostra todos os canais disponíveis e seu estado de configuração."""
    current_context = context if context is not None else CliContext.from_environment()

    channels = [
        {
            "name": "Telegram",
            "prefix": "telegram",
            "direction": "in + out",
            "vars": {"required": ["TELEGRAM_BOT_TOKEN"], "optional": []},
            "docs": "Polling de mensagens. Sem webhook necessário.",
        },
        {
            "name": "Discord",
            "prefix": "discord",
            "direction": "in + out",
            "vars": {
                "required": ["DISCORD_APP_PUBLIC_KEY", "DISCORD_APP_ID"],
                "optional": ["DISCORD_WEBHOOK_URL", "DISCORD_BOT_TOKEN"],
            },
            "docs": "Interactions Endpoint (slash commands). Out via webhook URL.",
        },
        {
            "name": "WhatsApp",
            "prefix": "whatsapp",
            "direction": "in + out",
            "vars": {
                "required": ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_VERIFY_TOKEN"],
                "optional": [],
            },
            "docs": "Meta Cloud API. Requer conta WhatsApp Business.",
        },
        {
            "name": "Slack",
            "prefix": "slack",
            "direction": "in + out",
            "vars": {
                "required": ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"],
                "optional": ["SLACK_WEBHOOK_URL"],
            },
            "docs": "Events API. Requer app instalado no workspace.",
        },
        {
            "name": "WebChat",
            "prefix": "webchat",
            "direction": "in + out",
            "vars": {"required": [], "optional": []},
            "docs": f"Sempre ativo. Acesse {current_context.webchat_url()}",
        },
    ]

    console = None
    table = None
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        table.add_column("Canal", style="bold")
        table.add_column("Prefixo")
        table.add_column("Status")
        table.add_column("Nota")
        rich_enabled = True
    except ImportError:
        rich_enabled = False
        print(f"{'Canal':<12} {'Prefixo':<12} {'Status':<15} Nota")
        print("-" * 70)

    for channel in channels:
        required = channel["vars"]["required"]
        if not required:
            status = "✓ sempre ativo"
            status_color = "green"
        else:
            missing = [var_name for var_name in required if not current_context.env.get(var_name)]
            if not missing:
                status = "✓ configurado"
                status_color = "green"
            else:
                status = f"· faltam: {', '.join(missing)}"
                status_color = "dim"

        if rich_enabled and table is not None:
            table.add_row(
                channel["name"],
                channel["prefix"],
                f"[{status_color}]{status}[/]",
                channel["docs"],
            )
        else:
            print(f"{channel['name']:<12} {channel['prefix']:<12} {status:<30} {channel['docs']}")

    if rich_enabled and console is not None and table is not None:
        console.print(table)
        console.print()
        console.print("[dim]Para adicionar um canal: edite .env com as variáveis listadas e reinicie.[/]")
    else:
        print("\nEdite .env com as variáveis necessárias e reinicie o servidor.")

    return 0