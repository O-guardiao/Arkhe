"""Channel Console — painel Rich de awareness multichannel para o TUI.

Exibe status de todos os canais registrados no ChannelStatusRegistry,
seja via HTTP (live mode) ou acesso direto ao CSR (local mode).
Permite envio cross-channel e probe sob demanda sem depender do REPL.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ── Status icons ──────────────────────────────────────────────────────────

_ICON_RUNNING = "●"
_ICON_CONFIGURED = "◑"
_ICON_OFF = "○"
_ICON_ERROR = "✖"


def _channel_icon(snap: dict[str, Any]) -> tuple[str, str]:
    """Retorna (icon, style) para um snapshot de canal."""
    if snap.get("running") and snap.get("healthy"):
        return _ICON_RUNNING, "bold green"
    if snap.get("running") and not snap.get("healthy"):
        return _ICON_ERROR, "bold red"
    if snap.get("configured"):
        return _ICON_CONFIGURED, "yellow"
    return _ICON_OFF, "dim"


# ── Data ──────────────────────────────────────────────────────────────────

@dataclass
class ChannelSnapshot:
    """Representação simplificada de um canal para renderização."""
    channel_id: str
    account_id: str = "default"
    configured: bool = False
    running: bool = False
    healthy: bool = False
    identity_name: str = ""
    last_error: str | None = None
    reconnect_attempts: int = 0
    last_probe_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChannelSnapshot:
        ident = d.get("identity") or {}
        name = ident.get("display_name") or ident.get("username") or ""
        return cls(
            channel_id=d.get("channel_id", "?"),
            account_id=d.get("account_id", "default"),
            configured=bool(d.get("configured")),
            running=bool(d.get("running")),
            healthy=bool(d.get("healthy")),
            identity_name=str(name),
            last_error=d.get("last_error"),
            reconnect_attempts=int(d.get("reconnect_attempts", 0)),
            last_probe_ms=float(d.get("last_probe_ms", 0)),
            meta=dict(d.get("meta") or {}),
        )


# ── Panel builder ─────────────────────────────────────────────────────────

@dataclass
class ChannelConsoleState:
    """Estado mantido entre renders do painel de canais."""
    snapshots: list[ChannelSnapshot] = field(default_factory=list)
    last_fetch_at: float = 0.0
    last_send_result: str = ""
    fetch_error: str = ""


def build_channel_panel(state: ChannelConsoleState) -> Panel:
    """Constrói o painel Rich de canais a partir do estado atual."""
    blocks: list[RenderableType] = []

    if not state.snapshots and not state.fetch_error:
        blocks.append(Text("Nenhum canal registrado.", style="dim"))
    elif state.fetch_error:
        blocks.append(Text(f"Erro: {state.fetch_error}", style="bold red"))
    else:
        table = Table(box=None, expand=True, show_header=True, padding=(0, 1))
        table.add_column("", width=2)  # icon
        table.add_column("Canal", width=11)
        table.add_column("Bot", ratio=1)
        table.add_column("Latência", width=8, justify="right")
        table.add_column("Erros", width=5, justify="right")

        for snap in state.snapshots:
            icon, style = _channel_icon(
                {"running": snap.running, "healthy": snap.healthy, "configured": snap.configured}
            )
            latency = f"{snap.last_probe_ms:.0f}ms" if snap.last_probe_ms > 0 else "-"
            errors = str(snap.reconnect_attempts) if snap.reconnect_attempts else "-"
            name = snap.identity_name or "-"
            if snap.last_error and not snap.healthy:
                name = Text(snap.last_error[:30], style="red")

            table.add_row(
                Text(icon, style=style),
                snap.channel_id,
                name if isinstance(name, Text) else str(name),
                latency,
                errors,
            )
        blocks.append(table)

    # Status line
    summary_parts: list[str] = []
    total = len(state.snapshots)
    running = sum(1 for s in state.snapshots if s.running)
    if total:
        summary_parts.append(f"{running}/{total} ativos")
    if state.last_fetch_at:
        age = time.time() - state.last_fetch_at
        summary_parts.append(f"atualizado há {age:.0f}s")
    if summary_parts:
        blocks.append(Text())
        blocks.append(Text(" · ".join(summary_parts), style="dim"))

    if state.last_send_result:
        blocks.append(Text(state.last_send_result, style="bold"))

    # Help
    blocks.append(Text())
    help_text = Text()
    help_text.append("/channels", style="bold cyan")
    help_text.append("  atualizar status\n")
    help_text.append("/send <canal> <texto>", style="bold cyan")
    help_text.append("  enviar cross-channel\n")
    help_text.append("/probe <canal>", style="bold cyan")
    help_text.append("  testar conectividade")
    blocks.append(help_text)

    return Panel(
        Group(*blocks),
        title="Canais",
        border_style="magenta",
    )


# ── Data fetchers ─────────────────────────────────────────────────────────

def fetch_channel_snapshots_live(live_api: Any) -> list[ChannelSnapshot]:
    """Busca snapshots via HTTP /api/channels/status (live mode)."""
    data = live_api.fetch_channels_status()
    channels = data.get("channels") or {}
    result: list[ChannelSnapshot] = []
    for _channel_id, accounts in channels.items():
        if isinstance(accounts, list):
            for acc in accounts:
                result.append(ChannelSnapshot.from_dict(acc))
        elif isinstance(accounts, dict):
            result.append(ChannelSnapshot.from_dict(accounts))
    return result


def fetch_channel_snapshots_local() -> list[ChannelSnapshot]:
    """Busca snapshots diretamente do CSR (local mode — se inicializado)."""
    try:
        from rlm.core.comms.channel_status import get_channel_status_registry
        csr = get_channel_status_registry()
    except (ImportError, RuntimeError):
        return []
    summary = csr.summary()
    channels = summary.get("channels") or {}
    result: list[ChannelSnapshot] = []
    for _channel_id, accounts in channels.items():
        if isinstance(accounts, list):
            for acc in accounts:
                result.append(ChannelSnapshot.from_dict(acc))
        elif isinstance(accounts, dict):
            result.append(ChannelSnapshot.from_dict(accounts))
    return result


def refresh_channel_state(
    state: ChannelConsoleState,
    *,
    live_api: Any | None,
) -> None:
    """Atualiza o state in-place — nunca levanta exceção."""
    try:
        if live_api is not None:
            state.snapshots = fetch_channel_snapshots_live(live_api)
        else:
            state.snapshots = fetch_channel_snapshots_local()
        state.last_fetch_at = time.time()
        state.fetch_error = ""
    except Exception as exc:
        state.fetch_error = str(exc)[:120]
