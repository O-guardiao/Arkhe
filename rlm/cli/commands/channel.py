from __future__ import annotations

import argparse
import json
from urllib import error as urllib_error
from urllib import request as urllib_request

from rlm.cli.context import CliContext, print_error, print_success


def _api_get(ctx: CliContext, path: str) -> dict | None:
    """GET ao servidor local com auth. Retorna JSON ou None se offline."""
    url = f"{ctx.api_base_url()}{path}"
    headers: dict[str, str] = {}
    for name in ("RLM_ADMIN_TOKEN", "RLM_API_TOKEN", "RLM_WS_TOKEN"):
        tok = ctx.env.get(name, "").strip()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
            break
    req = urllib_request.Request(url, headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


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


def cmd_channel_status(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Mostra status runtime dos canais via API /api/channels/status."""
    ctx = context if context is not None else CliContext.from_environment()
    data = _api_get(ctx, "/api/channels/status")

    if data is None:
        print_error(f"Servidor offline ou inacessível em {ctx.api_base_url()}")
        print("  Dica: rode 'arkhe start' e tente novamente.")
        return 1

    channels = data.get("channels", {})
    if not channels:
        print("Nenhum canal registrado no ChannelStatusRegistry.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
        table.add_column("Canal", style="bold")
        table.add_column("Bot")
        table.add_column("Running")
        table.add_column("Healthy")
        table.add_column("Probe (ms)")
        table.add_column("Erro")

        for ch_id, accounts in sorted(channels.items()):
            for acc in accounts:
                identity = acc.get("identity", {})
                bot_label = f"@{identity['username']}" if identity and identity.get("username") else "-"
                running = "[green]✓[/]" if acc.get("running") else "[red]✗[/]"
                healthy = "[green]✓[/]" if acc.get("healthy") else "[yellow]✗[/]"
                probe_ms = str(acc.get("last_probe_ms", "-"))
                error = acc.get("last_error") or ""
                if len(error) > 40:
                    error = error[:37] + "..."
                table.add_row(ch_id, bot_label, running, healthy, probe_ms, error)

        console.print(table)
        console.print()
        console.print(
            f"[dim]Total: {data.get('total', '?')} canais, "
            f"{data.get('running', '?')} running, "
            f"{data.get('healthy', '?')} healthy[/]"
        )
    except ImportError:
        print(f"{'Canal':<12} {'Bot':<20} {'Run':>4} {'OK':>4} {'ms':>8} Erro")
        print("-" * 70)
        for ch_id, accounts in sorted(channels.items()):
            for acc in accounts:
                identity = acc.get("identity", {})
                bot = f"@{identity['username']}" if identity and identity.get("username") else "-"
                run = "Y" if acc.get("running") else "N"
                ok = "Y" if acc.get("healthy") else "N"
                ms = str(acc.get("last_probe_ms", "-"))
                err = (acc.get("last_error") or "")[:30]
                print(f"{ch_id:<12} {bot:<20} {run:>4} {ok:>4} {ms:>8} {err}")

    return 0


def cmd_channel_probe(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Executa probe sob demanda para um canal específico."""
    ctx = context if context is not None else CliContext.from_environment()
    channel_id = args.channel_id

    # POST /api/channels/{channel_id}/probe
    url = f"{ctx.api_base_url()}/api/channels/{channel_id}/probe"
    headers: dict[str, str] = {"Content-Length": "0"}
    for name in ("RLM_ADMIN_TOKEN", "RLM_API_TOKEN", "RLM_WS_TOKEN"):
        tok = ctx.env.get(name, "").strip()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
            break

    req = urllib_request.Request(url, method="POST", headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib_error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print_error(f"Probe falhou: HTTP {e.code} — {body}")
        return 1
    except Exception as e:
        print_error(f"Servidor offline ou inacessível: {e}")
        return 1

    probe = data.get("probe", {})
    snapshot = data.get("snapshot", {})
    identity = probe.get("identity")

    if probe.get("ok"):
        print_success(f"Probe OK para '{channel_id}' ({probe.get('elapsed_ms', '?')}ms)")
        if identity:
            print(f"  Bot ID:   {identity.get('bot_id', '-')}")
            print(f"  Username: @{identity.get('username', '-')}")
            print(f"  Nome:     {identity.get('display_name', '-')}")
    else:
        print_error(f"Probe falhou para '{channel_id}': {probe.get('error', '?')}")

    # Show snapshot summary
    s_identity = snapshot.get("identity")
    if s_identity:
        print(f"\n  Identidade cached: @{s_identity.get('username', '-')} (id={s_identity.get('bot_id', '-')})")
    print(f"  Running: {snapshot.get('running', '-')}  Healthy: {snapshot.get('healthy', '-')}")

    return 0 if probe.get("ok") else 1