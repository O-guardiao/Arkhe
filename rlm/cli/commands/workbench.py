"""RuntimeWorkbench — TUI Rich interativo do Arkhe."""

from __future__ import annotations

import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from rlm.cli.context import CliContext
from rlm.cli.tui.runtime_factory import WorkbenchRuntime, build_local_workbench_runtime
from rlm.core.observability.operator_surface import apply_operator_command, build_activity_payload, dispatch_operator_prompt


@dataclass
class LiveSession:
    """Proxy de sessao para live mode — sem rlm_instance local."""
    session_id: str
    client_id: str
    status: str = "idle"
    state_dir: str = ""
    metadata: dict = field(default_factory=dict)
    rlm_instance: None = None


class RuntimeWorkbench:
    def __init__(
        self,
        runtime: WorkbenchRuntime | None,
        *,
        client_id: str,
        refresh_interval: float = 0.75,
        console: Console | None = None,
        live_api: Any | None = None,
    ) -> None:
        self.runtime = runtime
        self._live_api = live_api
        self.client_id = client_id
        self.refresh_interval = max(refresh_interval, 0.2)
        self.console = console or Console()
        self.last_notice = "Use /help para ver os comandos do operador."
        self._input_buffer = ""
        self._use_polled_input = sys.platform == "win32"

        if self._live_api is not None:
            info = self._live_api.ensure_session(client_id)
            self.session = LiveSession(
                session_id=info.session_id,
                client_id=info.client_id,
                status=info.status,
                state_dir=info.state_dir,
                metadata=dict(info.metadata),
            )
        else:
            self.session = self.runtime.session_manager.get_or_create(client_id)

    @property
    def _is_live(self) -> bool:
        return self._live_api is not None

    def run(self, *, once: bool = False) -> int:
        if once:
            self._render(clear=False)
            return 0

        self.last_notice = f"Sessao viva: {self.session.session_id}"
        if self._use_polled_input:
            return self._run_polled_loop()

        return self._run_blocking_loop()

    def _run_blocking_loop(self) -> int:
        while True:
            self._render(clear=True)
            try:
                raw = self.console.input("[bold cyan]arkhe>[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                return 0

            if not raw:
                continue
            if raw in {"/quit", "/exit"}:
                return 0

            try:
                if raw.startswith("/"):
                    should_exit = self._handle_operator_command(raw)
                    if should_exit:
                        return 0
                else:
                    self._dispatch_prompt(raw)
            except Exception as exc:
                self.last_notice = f"Erro: {exc}"

    def _run_polled_loop(self) -> int:
        while True:
            self._render(clear=True)
            try:
                raw = self._poll_input_line()
            except KeyboardInterrupt:
                self.console.print()
                return 0

            if raw is None:
                time.sleep(self.refresh_interval)
                continue
            raw = raw.strip()
            if not raw:
                continue
            if raw in {"/quit", "/exit"}:
                return 0

            try:
                if raw.startswith("/"):
                    should_exit = self._handle_operator_command(raw)
                    if should_exit:
                        return 0
                else:
                    self._dispatch_prompt(raw)
            except Exception as exc:
                self.last_notice = f"Erro: {exc}"

    def _dispatch_prompt(self, text: str) -> None:
        """Envia prompt ao runtime — via HTTP (live) ou local direto."""
        if self._is_live:
            self._live_api.dispatch_prompt(
                self.session.session_id,
                self.client_id,
                text,
            )
        else:
            dispatch_operator_prompt(
                self.runtime.session_manager,
                self.runtime.supervisor,
                self.session,
                text=text,
                origin="tui",
                runtime_services=self.runtime.dispatch_services,
                client_id=self.client_id,
            )
        self.last_notice = "Turno enviado. Observando sessao viva ate a conclusao."
        self.watch_until_idle()

    def _fetch_activity(self) -> dict[str, Any]:
        """Busca activity payload — via HTTP (live) ou local direto."""
        if self._is_live:
            try:
                return self._live_api.fetch_activity(self.session.session_id)
            except Exception:
                return {"session": {}, "event_log": [], "runtime": None}
        return build_activity_payload(self.runtime.session_manager, self.session)

    def watch_until_idle(self, *, duration_s: float | None = None) -> None:
        started = time.time()
        if self._is_live:
            while True:
                activity = self._fetch_activity()
                session_data = activity.get("session") or {}
                status = session_data.get("status", "idle")
                if status not in ("running",):
                    self.session.status = status
                    break
                self._render(clear=True)
                if duration_s is not None and (time.time() - started) >= duration_s:
                    self.last_notice = "Watch encerrado pelo limite solicitado."
                    return
                time.sleep(self.refresh_interval)
        else:
            while self.runtime.supervisor.is_running(self.session.session_id):
                self._render(clear=True)
                if duration_s is not None and (time.time() - started) >= duration_s:
                    self.last_notice = "Watch encerrado pelo limite solicitado."
                    return
                time.sleep(self.refresh_interval)
        self._render(clear=True)
        status = (self.session.metadata or {}).get("last_operator_status") or getattr(self.session, "status", "done")
        self.last_notice = f"Execucao encerrada com status: {status}"

    def _handle_operator_command(self, raw: str) -> bool:
        parts = shlex.split(raw)
        command = parts[0].lower()

        if command == "/help":
            self.last_notice = "Comandos: /pause, /resume, /checkpoint, /focus, /winner, /priority, /note, /watch, /quit"
            return False
        if command in {"/quit", "/exit"}:
            return True
        if command == "/watch":
            duration = float(parts[1]) if len(parts) > 1 else None
            self.watch_until_idle(duration_s=duration)
            return False

        command_type, payload, branch_id = self._translate_operator_command(parts)
        if self._is_live:
            result = self._live_api.apply_command(
                self.session.session_id,
                client_id=self.client_id,
                command_type=command_type,
                payload=payload,
                branch_id=branch_id,
            )
            cmd = result.get("command") or {}
            self.last_notice = f"Comando aplicado: {cmd.get('command_type', command_type)}#{cmd.get('command_id', '?')}"
        else:
            entry, _runtime = apply_operator_command(
                self.runtime.session_manager,
                self.session,
                supervisor=self.runtime.supervisor,
                command_type=command_type,
                payload=payload,
                branch_id=branch_id,
                origin="tui",
            )
            self.last_notice = f"Comando aplicado: {entry.get('command_type')}#{entry.get('command_id')}"
        return False

    def _poll_input_line(self) -> str | None:
        import msvcrt

        line: str | None = None
        while msvcrt.kbhit():
            char = msvcrt.getwch()
            if char in {"\r", "\n"}:
                line = self._input_buffer
                self._input_buffer = ""
                break
            if char == "\x03":
                raise KeyboardInterrupt()
            if char in {"\b", "\x7f"}:
                self._input_buffer = self._input_buffer[:-1]
                continue
            if char in {"\x00", "\xe0"}:
                if msvcrt.kbhit():
                    msvcrt.getwch()
                continue
            if char.isprintable():
                self._input_buffer += char
        return line

    def _translate_operator_command(self, parts: list[str]) -> tuple[str, dict[str, Any], int | None]:
        command = parts[0].lower()
        if command == "/pause":
            return "pause_runtime", {"reason": " ".join(parts[1:]).strip()}, None
        if command == "/resume":
            return "resume_runtime", {"reason": " ".join(parts[1:]).strip()}, None
        if command == "/checkpoint":
            name = parts[1] if len(parts) > 1 else f"tui-{int(time.time())}"
            return "create_checkpoint", {"checkpoint_name": name}, None
        if command == "/focus":
            if len(parts) < 2:
                raise ValueError("/focus exige branch_id")
            return "focus_branch", {"note": " ".join(parts[2:]).strip()}, int(parts[1])
        if command == "/winner":
            if len(parts) < 2:
                raise ValueError("/winner exige branch_id")
            return "fix_winner_branch", {"note": " ".join(parts[2:]).strip()}, int(parts[1])
        if command == "/priority":
            if len(parts) < 3:
                raise ValueError("/priority exige branch_id e prioridade")
            branch_id = int(parts[1])
            priority = int(parts[2])
            note = " ".join(parts[3:]).strip()
            return "reprioritize_branch", {"priority": priority, "reason": note}, branch_id
        if command == "/note":
            note = " ".join(parts[1:]).strip()
            if not note:
                raise ValueError("/note exige texto")
            return "operator_note", {"note": note}, None
        raise ValueError(f"Comando desconhecido: {parts[0]}")

    def _render(self, *, clear: bool) -> None:
        if clear:
            self.console.clear()
        self.console.print(self.build_layout())

    def build_layout(self) -> Layout:
        try:
            activity = self._fetch_activity()
        except Exception:
            activity = {"session": {}, "event_log": [], "runtime": None}
        runtime = activity.get("runtime") or {}
        layout = Layout(name="root")
        layout.split_column(
            Layout(self._build_header(activity), name="header", size=5),
            Layout(name="body", ratio=1),
            Layout(self._build_footer(runtime), name="footer", size=6),
        )
        layout["body"].split_row(
            Layout(self._build_branches_panel(runtime), name="branches", size=38),
            Layout(self._build_messages_panel(runtime), name="messages", ratio=2),
            Layout(self._build_events_panel(activity, runtime), name="events", size=48),
        )
        return layout

    def _build_header(self, activity: dict[str, Any]) -> Panel:
        session_data = activity.get("session") or {}
        runtime = activity.get("runtime") or {}
        controls = runtime.get("controls") or {}
        summary = (runtime.get("coordination") or {}).get("latest_parallel_summary") or {}
        rlm_core = getattr(getattr(self.session, "rlm_instance", None), "_rlm", None)
        backend_kwargs = getattr(rlm_core, "backend_kwargs", None) or {}
        model_name = backend_kwargs.get("model_name") or "unknown"
        mode_label = "live" if self._is_live else "local"
        text = Text()
        text.append("Arkhe TUI Workbench\n", style="bold cyan")
        text.append(f"Sessao: {session_data.get('session_id', self.session.session_id)}  ")
        text.append(f"Cliente: {session_data.get('client_id', self.client_id)}  ")
        text.append(f"Status: {session_data.get('status', self.session.status)}  ")
        text.append(f"Modelo: {model_name}  ")
        text.append(f"Modo: {mode_label}\n", style="bold green" if self._is_live else "bold yellow")
        text.append(f"Paused: {controls.get('paused', False)}  ")
        text.append(f"Focus: {controls.get('focused_branch_id')}  ")
        text.append(f"Winner: {summary.get('winner_branch_id')}  ")
        text.append(f"Checkpoint: {controls.get('last_checkpoint_path') or '-'}")
        ds = getattr(self.runtime, "dispatch_services", None) if self.runtime else None
        if ds and ds.eligible_skills:
            skills_dir = Path(ds.eligible_skills[0].source_path).parent.parent
            text.append(f"\nSkills: {len(ds.eligible_skills)} carregadas de {skills_dir}")
        return Panel(text, border_style="cyan")

    def _build_branches_panel(self, runtime: dict[str, Any]) -> Panel:
        coordination = runtime.get("coordination") or {}
        controls = runtime.get("controls") or {}
        summary = coordination.get("latest_parallel_summary") or {}
        tree = Tree("Branches", guide_style="cyan")
        for item in coordination.get("branch_tasks") or []:
            branch_id = item.get("branch_id")
            metadata = item.get("metadata") or {}
            label = f"branch {branch_id} | {item.get('title', 'sem titulo')} | {item.get('mode', '-') } | {item.get('status', '-') }"
            if summary.get("winner_branch_id") == branch_id:
                label += " | winner"
            if controls.get("focused_branch_id") == branch_id:
                label += " | focus"
            if str(branch_id) in (controls.get("branch_priorities") or {}):
                label += f" | prio={(controls.get('branch_priorities') or {}).get(str(branch_id))}"
            branch = tree.add(label)
            if metadata:
                for key, value in sorted(metadata.items()):
                    branch.add(f"{key}: {value}")

        tasks = runtime.get("tasks") or {}
        current = tasks.get("current") or {}
        content: RenderableType = Group(
            tree,
            Text(),
            Text(f"Task atual: {current.get('title', '-')} [{current.get('status', '-')}]", style="bold"),
            Text(current.get("note") or "", style="dim"),
        )
        return Panel(content, title="Branches", border_style="green")

    def _build_messages_panel(self, runtime: dict[str, Any]) -> Panel:
        messages = ((runtime.get("recursive_session") or {}).get("messages") or [])[-12:]
        timeline = (runtime.get("timeline") or {}).get("entries") or []
        blocks: list[RenderableType] = []
        if messages:
            message_table = Table(box=None, expand=True, show_header=True)
            message_table.add_column("Role", style="cyan", width=10)
            message_table.add_column("Conteudo")
            for message in messages:
                message_table.add_row(str(message.get("role", "?")), str(message.get("content", "")))
            blocks.append(message_table)
        else:
            blocks.append(Text("Sem mensagens recursivas ainda.", style="dim"))

        blocks.append(Text())
        blocks.append(Text("Timeline", style="bold magenta"))
        timeline_table = Table(box=None, expand=True, show_header=True)
        timeline_table.add_column("Tipo", width=18)
        timeline_table.add_column("Resumo")
        for entry in timeline[-8:]:
            summary = entry.get("summary") or entry.get("message") or entry.get("title") or str(entry.get("payload") or "")
            timeline_table.add_row(str(entry.get("event_type") or entry.get("kind") or "-"), str(summary))
        if timeline:
            blocks.append(timeline_table)
        else:
            blocks.append(Text("Sem timeline publicada.", style="dim"))
        return Panel(Group(*blocks), title="Mensagens E Timeline", border_style="blue")

    def _build_events_panel(self, activity: dict[str, Any], runtime: dict[str, Any]) -> Panel:
        event_log = activity.get("event_log") or []
        recursive_events = ((runtime.get("recursive_session") or {}).get("events") or [])[-8:]
        coordination_events = ((runtime.get("coordination") or {}).get("events") or [])[-6:]
        latest_response = (self.session.metadata or {}).get("last_operator_response") or "-"

        event_table = Table(box=None, expand=True, show_header=True)
        event_table.add_column("Fonte", width=12)
        event_table.add_column("Evento", width=24)
        event_table.add_column("Resumo")
        for item in event_log[-8:]:
            payload = item.get("payload") or {}
            summary = payload.get("text_preview") or payload.get("response_preview") or payload.get("error") or payload.get("command_type") or str(payload)
            event_table.add_row("session", str(item.get("event_type", "-")), str(summary))
        for item in recursive_events:
            payload = item.get("payload") or {}
            event_table.add_row("runtime", str(item.get("event_type", "-")), str(payload or item.get("source") or ""))
        for item in coordination_events:
            event_table.add_row("coord", str(item.get("operation", "-")), str(item.get("payload_preview") or item.get("topic") or ""))

        help_text = Text()
        help_text.append("/pause [motivo]  /resume [motivo]\n")
        help_text.append("/focus <branch> [nota]  /winner <branch>\n")
        help_text.append("/priority <branch> <prio> [nota]\n")
        help_text.append("/checkpoint [nome]  /note <texto>\n")
        help_text.append("/watch [segundos]  /quit\n")
        help_text.append("Texto livre envia prompt ao runtime", style="dim")

        content = Group(
            Text("Resposta mais recente", style="bold yellow"),
            Text(str(latest_response)),
            Text(),
            Text("Eventos", style="bold yellow"),
            event_table,
            Text(),
            Text("Controles", style="bold yellow"),
            help_text,
        )
        return Panel(content, title="Eventos E Comandos", border_style="yellow")

    def _build_footer(self, runtime: dict[str, Any]) -> Panel:
        controls = runtime.get("controls") or {}
        text = Text()
        text.append(self.last_notice, style="bold")
        text.append("\n")
        text.append(f"Pause reason: {controls.get('pause_reason') or '-'}  ")
        text.append(f"Operator note: {controls.get('last_operator_note') or '-'}\n")
        if self._use_polled_input:
            text.append(f"Input: {self._input_buffer or ' '}\n", style="cyan")
        text.append(f"Refresh: {self.refresh_interval:.2f}s  ")
        text.append(f"Estado salvo em: {Path(getattr(self.session, 'state_dir', '.')).as_posix()}")
        return Panel(text, border_style="magenta")


def run_workbench(context: CliContext, *, client_id: str | None, refresh_interval: float, once: bool) -> int:
    resolved_client_id = client_id or "tui:default"
    console = Console()

    # --- Tenta modo live (servidor vivo) ---
    try:
        from rlm.cli.tui.live_api import LiveWorkbenchAPI

        live_api = LiveWorkbenchAPI(context)
        if live_api.probe():
            console.print(
                f"[bold green]Conectado ao servidor vivo em {live_api.base_url}[/]"
            )
            workbench = RuntimeWorkbench(
                None,
                client_id=resolved_client_id,
                refresh_interval=refresh_interval,
                console=console,
                live_api=live_api,
            )
            return workbench.run(once=once)
    except Exception:
        pass

    # --- Fallback: modo local (sem multichannel) ---
    console.print(
        "[yellow]Servidor indisponivel — usando runtime local "
        "(cross-channel indisponivel).[/]"
    )
    runtime = build_local_workbench_runtime()
    workbench = RuntimeWorkbench(
        runtime,
        client_id=resolved_client_id,
        refresh_interval=refresh_interval,
        console=console,
    )
    try:
        return workbench.run(once=once)
    finally:
        runtime.close()
