"""RuntimeWorkbench — TUI Rich interativo do Arkhe."""

from __future__ import annotations

import shlex
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from rlm.cli.context import CliContext
from rlm.cli.tui.runtime_factory import WorkbenchRuntime, build_local_workbench_runtime
from rlm.cli.tui.channel_console import (
    ChannelConsoleState,
    build_channel_panel,
    refresh_channel_state,
)
from rlm.core.observability.operator_surface import apply_operator_command, build_activity_payload, dispatch_operator_prompt


def _metadata_factory() -> dict[str, Any]:
    return {}


def _dict_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in cast(dict[Any, Any], value).items()}
    return {}


def _list_payload(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(cast(list[Any], value))
    return []


def _runtime_recursion_payload(runtime: dict[str, Any]) -> dict[str, Any]:
    recursion = _dict_payload(runtime.get("recursion"))
    if recursion:
        normalized = dict(recursion)
        normalized["controls"] = _dict_payload(recursion.get("controls"))
        normalized["summary"] = _dict_payload(recursion.get("summary"))
        normalized["branches"] = _list_payload(recursion.get("branches"))
        normalized["events"] = _list_payload(recursion.get("events"))
        normalized["latest_stats"] = _dict_payload(recursion.get("latest_stats"))
        return normalized

    coordination = _dict_payload(runtime.get("coordination"))
    return {
        "attached": bool(coordination.get("attached", False)),
        "active_branch_id": coordination.get("branch_id"),
        "controls": _dict_payload(runtime.get("controls")),
        "summary": _dict_payload(coordination.get("latest_parallel_summary")),
        "branches": _list_payload(coordination.get("branch_tasks")),
        "events": _list_payload(coordination.get("events")),
        "latest_stats": _dict_payload(coordination.get("latest_stats")),
    }


def _operation_summary(entry: dict[str, Any]) -> str:
    payload = _dict_payload(entry.get("payload"))
    summary = (
        payload.get("reason")
        or payload.get("note")
        or payload.get("text_preview")
        or payload.get("response_preview")
        or payload.get("checkpoint_path")
        or payload.get("error")
        or payload
        or "-"
    )
    return str(summary)


def _daemon_payload(runtime: dict[str, Any]) -> dict[str, Any]:
    daemon = _dict_payload(runtime.get("daemon"))
    daemon["stats"] = _dict_payload(daemon.get("stats"))
    daemon["warm_runtime"] = _dict_payload(daemon.get("warm_runtime"))
    daemon["attached_channels"] = _dict_payload(daemon.get("attached_channels"))
    daemon["outbox"] = _dict_payload(daemon.get("outbox"))
    daemon["channel_runtime"] = _dict_payload(daemon.get("channel_runtime"))
    daemon["memory_access"] = _dict_payload(daemon.get("memory_access"))
    daemon["memory_access"]["last_scope"] = _dict_payload(daemon["memory_access"].get("last_scope"))
    return daemon


def _memory_scope_summary(scope: dict[str, Any]) -> str:
    parts: list[str] = []
    channel = str(scope.get("channel") or "")
    actor = str(scope.get("actor") or "")
    workspace_scope = str(scope.get("workspace_scope") or "")
    agent_role = str(scope.get("agent_role") or "")
    parent_session_id = str(scope.get("parent_session_id") or "")

    if channel:
        parts.append(f"channel={channel}")
    if actor:
        parts.append(f"actor={actor}")

    active_channels = [str(item) for item in _list_payload(scope.get("active_channels")) if str(item).strip()]
    if active_channels:
        parts.append("active=" + ",".join(active_channels))

    if workspace_scope:
        parts.append(f"workspace={workspace_scope}")
    if scope.get("agent_depth") is not None:
        parts.append(f"depth={scope.get('agent_depth')}")
    if scope.get("branch_id") is not None:
        parts.append(f"branch={scope.get('branch_id')}")
    if agent_role:
        parts.append(f"role={agent_role}")
    if parent_session_id:
        parts.append(f"parent={parent_session_id}")
    if not parts:
        return "-"
    return "  ".join(parts)


def _is_local_live_target(context: CliContext) -> bool:
    configured = str(context.env.get("RLM_INTERNAL_HOST", context.api_base_url()) or "").strip()
    if not configured:
        return False
    parsed = urlparse(configured if "://" in configured else f"http://{configured}")
    hostname = str(parsed.hostname or "").strip().lower()
    return hostname in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _autostart_live_service_enabled(context: CliContext) -> bool:
    raw = str(context.env.get("RLM_TUI_AUTOSTART_SERVICE", "true") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _probe_live_api(live_api: Any, *, timeout: int = 3) -> bool:
    try:
        return bool(live_api.probe(timeout=timeout))
    except Exception:
        return False


def _ensure_live_service(context: CliContext, console: Console, live_api: Any | None) -> Any | None:
    if live_api is None:
        return None
    if not _is_local_live_target(context):
        return None
    if not _autostart_live_service_enabled(context):
        return None

    from rlm.cli.service import start_services

    console.print(
        "[yellow]Servidor vivo indisponivel — tentando iniciar backend persistente local...[/]"
    )
    if start_services(foreground=False, context=context) != 0:
        return None

    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if _probe_live_api(live_api, timeout=1):
            console.print(
                f"[bold green]Backend persistente local ativo em {live_api.base_url}[/]"
            )
            return live_api
        time.sleep(0.25)
    return None


@dataclass
class LiveSession:
    """Proxy de sessao para live mode — sem rlm_instance local."""
    session_id: str
    client_id: str
    status: str = "idle"
    state_dir: str = ""
    last_activity_at: str = ""
    metadata: dict[str, Any] = field(default_factory=_metadata_factory)
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
        self._channel_state = ChannelConsoleState()
        self.session: Any

        if self._live_api is not None:
            info = self._live_api.ensure_session(client_id)
            self.session = LiveSession(
                session_id=info.session_id,
                client_id=info.client_id,
                status=info.status,
                state_dir=info.state_dir,
                last_activity_at=str(info.metadata.get("last_activity_at", "") or ""),
                metadata=dict(info.metadata),
            )
        else:
            runtime = self.runtime
            if runtime is None:
                raise ValueError("runtime local e obrigatorio quando live_api nao for fornecido")
            session_manager = cast(Any, runtime.session_manager)
            self.session = session_manager.get_or_create(client_id)

    @property
    def _is_live(self) -> bool:
        return self._live_api is not None

    def _require_runtime(self) -> WorkbenchRuntime:
        runtime = self.runtime
        if runtime is None:
            raise RuntimeError("runtime local indisponivel neste modo")
        return runtime

    def _require_live_api(self) -> Any:
        live_api = self._live_api
        if live_api is None:
            raise RuntimeError("live_api indisponivel fora do modo live")
        return live_api

    def _session_metadata(self) -> dict[str, Any]:
        return _dict_payload(getattr(self.session, "metadata", {}))

    def _session_last_activity(self) -> str:
        return str(getattr(self.session, "last_activity_at", "") or getattr(self.session, "last_active", "") or "")

    def _set_session_last_activity(self, value: Any) -> None:
        normalized = str(value or "")
        setattr(self.session, "last_activity_at", normalized)
        if hasattr(self.session, "last_active"):
            setattr(self.session, "last_active", normalized)

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
            self._dispatch_live_prompt(text)
        else:
            runtime = self._require_runtime()
            dispatch_operator_prompt(
                runtime.session_manager,
                runtime.supervisor,
                self.session,
                text=text,
                origin="tui",
                runtime_services=runtime.dispatch_services,
                client_id=self.client_id,
            )
        self.last_notice = "Turno enviado. Observando sessao viva ate a conclusao."
        self.watch_until_idle()

    def _dispatch_live_prompt(self, text: str) -> None:
        live_api = self._require_live_api()
        self._run_live_with_reattach(
            lambda: live_api.dispatch_prompt(self.session.session_id, self.client_id, text),
            reason=f"prompt:{text[:80]}",
        )
        self.session.status = "running"

    def _apply_live_command(self, *, command_type: str, payload: dict[str, Any], branch_id: int | None) -> dict[str, Any]:
        live_api = self._require_live_api()
        return self._run_live_with_reattach(
            lambda: live_api.apply_command(
                self.session.session_id,
                client_id=self.client_id,
                command_type=command_type,
                payload=payload,
                branch_id=branch_id,
            ),
            reason=f"command:{command_type}",
        )

    def _run_live_with_reattach(self, callback: Callable[[], Any], *, reason: str) -> Any:
        try:
            return callback()
        except Exception as exc:
            if not self._reattach_live_session(reason=f"{reason}:{exc}"):
                raise
            return callback()

    def _activity_fallback_payload(self) -> dict[str, Any]:
        return {
            "session": {
                "session_id": self.session.session_id,
                "client_id": self.session.client_id,
                "status": self.session.status,
                "state_dir": self.session.state_dir,
                "last_activity_at": self._session_last_activity(),
                "metadata": self._session_metadata(),
            },
            "event_log": [],
            "operation_log": [],
            "runtime": None,
        }

    def _sync_live_session(self, activity: dict[str, Any]) -> None:
        session_data = _dict_payload(activity.get("session"))
        if not session_data:
            return
        self.session.session_id = str(session_data.get("session_id", self.session.session_id) or self.session.session_id)
        self.session.client_id = str(session_data.get("client_id", self.session.client_id) or self.session.client_id)
        self.session.status = str(session_data.get("status", self.session.status) or self.session.status)
        self.session.state_dir = str(session_data.get("state_dir", self.session.state_dir) or self.session.state_dir)
        self._set_session_last_activity(
            session_data.get("last_activity_at")
            or session_data.get("last_active")
            or self._session_last_activity()
            or ""
        )
        metadata = _dict_payload(session_data.get("metadata"))
        if not metadata:
            metadata = self._session_metadata()
        last_activity_at = self._session_last_activity()
        if last_activity_at:
            metadata["last_activity_at"] = last_activity_at
        self.session.metadata = metadata

    def _normalize_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        session_data = _dict_payload(activity.get("session"))
        metadata = self._session_metadata()
        metadata.update(_dict_payload(session_data.get("metadata")))
        last_activity_at = str(
            session_data.get("last_activity_at")
            or session_data.get("last_active")
            or self._session_last_activity()
            or ""
        )
        if self._is_live:
            session_data["session_id"] = self.session.session_id
            session_data["client_id"] = self.session.client_id
            session_data.setdefault("status", self.session.status)
            session_data.setdefault("state_dir", self.session.state_dir)
        if last_activity_at:
            session_data["last_activity_at"] = last_activity_at
            metadata["last_activity_at"] = last_activity_at
        session_data["metadata"] = metadata
        normalized: dict[str, Any] = {
            "session": session_data,
            "event_log": _list_payload(activity.get("event_log")),
            "operation_log": _list_payload(activity.get("operation_log")),
            "runtime": activity.get("runtime"),
        }
        self._sync_live_session(normalized)
        return normalized

    def _reattach_live_session(self, *, reason: str) -> bool:
        live_api = self._require_live_api()
        try:
            info = live_api.ensure_session(self.client_id)
        except Exception as exc:
            self.last_notice = f"Reconnect falhou: {exc}"
            return False

        reconnects = int(self._session_metadata().get("live_reconnects", 0) or 0) + 1
        metadata = dict(info.metadata or {})
        metadata["live_reconnects"] = reconnects
        metadata["live_reconnect_reason"] = reason
        self.session.session_id = info.session_id
        self.session.client_id = info.client_id
        self.session.status = info.status
        self.session.state_dir = info.state_dir
        self._set_session_last_activity(metadata.get("last_activity_at", self._session_last_activity()) or self._session_last_activity())
        self.session.metadata = metadata
        self.last_notice = f"Sessao live reconectada: {info.session_id}"
        return True

    def _fetch_activity(self) -> dict[str, Any]:
        """Busca activity payload — via HTTP (live) ou local direto."""
        if self._is_live:
            from rlm.cli.tui.live_api import is_live_session_missing
            live_api = self._require_live_api()

            try:
                activity = live_api.fetch_activity(self.session.session_id)
                return self._normalize_activity(activity)
            except Exception as exc:
                if is_live_session_missing(exc) and self._reattach_live_session(reason=str(exc)):
                    try:
                        activity = live_api.fetch_activity(self.session.session_id)
                        return self._normalize_activity(activity)
                    except Exception as retry_exc:
                        self.last_notice = f"Activity indisponivel: {retry_exc}"
                        return self._activity_fallback_payload()
                self.last_notice = f"Activity indisponivel: {exc}"
                return self._activity_fallback_payload()
        runtime = self._require_runtime()
        return build_activity_payload(runtime.session_manager, self.session)

    def watch_until_idle(self, *, duration_s: float | None = None) -> None:
        started = time.time()
        if self._is_live:
            while True:
                activity = self._fetch_activity()
                session_data = _dict_payload(activity.get("session"))
                status = str(session_data.get("status", "idle") or "idle")
                if status not in ("running",):
                    self.session.status = status
                    break
                self._render(clear=True)
                if duration_s is not None and (time.time() - started) >= duration_s:
                    self.last_notice = "Watch encerrado pelo limite solicitado."
                    return
                time.sleep(self.refresh_interval)
        else:
            runtime = self._require_runtime()
            while runtime.supervisor.is_running(self.session.session_id):
                self._render(clear=True)
                if duration_s is not None and (time.time() - started) >= duration_s:
                    self.last_notice = "Watch encerrado pelo limite solicitado."
                    return
                time.sleep(self.refresh_interval)
        self._render(clear=True)
        status = self._session_metadata().get("last_operator_status") or getattr(self.session, "status", "done")
        self.last_notice = f"Execucao encerrada com status: {status}"

    def _handle_operator_command(self, raw: str) -> bool:
        parts = shlex.split(raw)
        command = parts[0].lower()

        if command == "/help":
            self.last_notice = (
                "Comandos: /pause, /resume, /checkpoint, /focus, /winner, "
                "/priority, /note, /watch, /channels, /send, /probe, /quit"
            )
            return False
        if command in {"/quit", "/exit"}:
            return True
        if command == "/watch":
            duration = float(parts[1]) if len(parts) > 1 else None
            self.watch_until_idle(duration_s=duration)
            return False

        # ── Channel commands ──────────────────────────────────────
        if command == "/channels":
            refresh_channel_state(self._channel_state, live_api=self._live_api)
            total = len(self._channel_state.snapshots)
            running = sum(1 for s in self._channel_state.snapshots if s.running)
            self.last_notice = f"Canais atualizados: {running}/{total} ativos."
            return False

        if command == "/probe":
            if len(parts) < 2:
                self.last_notice = "Uso: /probe <channel_id>"
                return False
            channel_id = parts[1]
            try:
                if self._is_live:
                    live_api = self._require_live_api()
                    result = _dict_payload(live_api.probe_channel(channel_id))
                    self.last_notice = f"Probe {channel_id}: {result.get('status', 'ok')}"
                else:
                    try:
                        from rlm.core.comms.channel_status import get_channel_status_registry
                        csr = get_channel_status_registry()
                        csr.probe(channel_id)
                        self.last_notice = f"Probe {channel_id}: executado (local)"
                    except (ImportError, RuntimeError):
                        self.last_notice = "CSR indisponivel no modo local."
            except Exception as exc:
                self.last_notice = f"Probe falhou: {exc}"
            return False

        if command == "/send":
            if len(parts) < 3:
                self.last_notice = "Uso: /send <target_client_id> <mensagem>"
                return False
            target = parts[1]
            message = " ".join(parts[2:])
            try:
                if self._is_live:
                    live_api = self._require_live_api()
                    result = _dict_payload(live_api.cross_channel_send(target, message))
                    self.last_notice = f"Enviado para {target}: {result.get('status', 'ok')}"
                else:
                    try:
                        from rlm.plugins.channel_registry import ChannelRegistry
                        ChannelRegistry.reply(target, message)
                        self.last_notice = f"Enviado para {target} (local)."
                    except (ImportError, RuntimeError):
                        self.last_notice = "ChannelRegistry indisponivel no modo local."
            except Exception as exc:
                self.last_notice = f"Envio falhou: {exc}"
            self._channel_state.last_send_result = self.last_notice
            return False

        command_type, payload, branch_id = self._translate_operator_command(parts)
        if self._is_live:
            result = self._apply_live_command(command_type=command_type, payload=payload, branch_id=branch_id)
            cmd = _dict_payload(result.get("command"))
            self.last_notice = f"Comando aplicado: {cmd.get('command_type', command_type)}#{cmd.get('command_id', '?')}"
        else:
            runtime = self._require_runtime()
            entry, _runtime = apply_operator_command(
                runtime.session_manager,
                self.session,
                supervisor=runtime.supervisor,
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
        activity: dict[str, Any]
        try:
            activity = self._fetch_activity()
        except Exception:
            activity = {"session": {}, "event_log": [], "operation_log": [], "runtime": None}
        runtime = _dict_payload(activity.get("runtime"))

        # Atualiza canais a cada render (dados vem do cache do state)
        refresh_channel_state(self._channel_state, live_api=self._live_api)

        layout = Layout(name="root")
        layout.split_column(
            Layout(self._build_header(activity), name="header", size=9),
            Layout(name="body", ratio=1),
            Layout(self._build_footer(runtime), name="footer", size=7),
        )

        # Coluna direita: eventos (topo) + canais (base)
        right_column = Layout(name="right_col", size=48)
        right_column.split_column(
            Layout(self._build_events_panel(activity, runtime), name="events", ratio=2),
            Layout(build_channel_panel(self._channel_state), name="channels", ratio=1),
        )

        layout["body"].split_row(
            Layout(self._build_branches_panel(runtime), name="branches", size=38),
            Layout(self._build_messages_panel(runtime), name="messages", ratio=2),
            right_column,
        )
        return layout

    def _build_header(self, activity: dict[str, Any]) -> Panel:
        session_data = _dict_payload(activity.get("session"))
        session_metadata = _dict_payload(session_data.get("metadata"))
        channel_context = _dict_payload(session_metadata.get("_channel_context"))
        runtime = _dict_payload(activity.get("runtime"))
        recursion = _runtime_recursion_payload(runtime)
        daemon = _daemon_payload(runtime)
        daemon_stats = _dict_payload(daemon.get("stats"))
        memory_access = _dict_payload(daemon.get("memory_access"))
        controls = _dict_payload(recursion.get("controls"))
        summary = _dict_payload(recursion.get("summary"))
        rlm_core = getattr(getattr(self.session, "rlm_instance", None), "_rlm", None)
        backend_kwargs = _dict_payload(getattr(rlm_core, "backend_kwargs", None))
        model_name = backend_kwargs.get("model_name") or "unknown"
        mode_label = "live" if self._is_live else "local"
        text = Text()
        text.append("Arkhe TUI Workbench\n", style="bold cyan")
        text.append(f"Sessao: {session_data.get('session_id', self.session.session_id)}  ")
        text.append(f"Cliente: {session_data.get('client_id', self.client_id)}  ")
        text.append(f"Status: {session_data.get('status', self.session.status)}  ")
        text.append(f"Modelo: {model_name}  ")
        text.append(f"Modo: {mode_label}\n", style="bold green" if self._is_live else "bold yellow")
        last_activity_at = session_data.get("last_activity_at") or session_data.get("last_active") or self._session_last_activity() or "-"
        text.append(
            f"Transport: {channel_context.get('transport') or '-'}  "
            f"Source: {channel_context.get('source_name') or '-'}  "
            f"Actor: {channel_context.get('actor') or '-'}  "
            f"Origem: {channel_context.get('origin_session_id') or channel_context.get('requested_session_id') or '-'}\n"
        )
        text.append(
            f"OrigemSessao: {channel_context.get('session_origin') or '-'}  "
            f"Atividade: {last_activity_at}\n"
        )
        text.append(f"Paused: {controls.get('paused', False)}  ")
        text.append(f"Focus: {controls.get('focused_branch_id')}  ")
        text.append(f"Winner: {summary.get('winner_branch_id')}  ")
        text.append(f"Checkpoint: {controls.get('last_checkpoint_path') or '-'}")
        if daemon:
            daemon_state = "ready" if daemon.get("ready") else "cold"
            if daemon.get("draining"):
                daemon_state = "draining"
            text.append(
                f"\nDaemon: {daemon_state}  Inflight: {daemon.get('inflight_dispatches', 0)}  "
                f"LLM: {daemon_stats.get('llm_invoked', 0)}  Determ: {daemon_stats.get('deterministic_used', 0)}  "
                f"TaskAgents: {daemon_stats.get('task_agent_invoked', 0)}"
            )
            if memory_access:
                text.append(
                    f"\nMemory: recall={memory_access.get('recall_requests', 0)}  "
                    f"hits={memory_access.get('recall_hits', 0)}  "
                    f"session={memory_access.get('session_blocks', 0)}  "
                    f"workspace={memory_access.get('workspace_blocks', 0)}  "
                    f"kb={memory_access.get('kb_blocks', 0)}  "
                    f"post={memory_access.get('post_turn_requests', 0)}  "
                    f"episodic={memory_access.get('episodic_writes', 0)}"
                )
        ds = getattr(self.runtime, "dispatch_services", None) if self.runtime else None
        if ds and ds.eligible_skills:
            skills_dir = Path(ds.eligible_skills[0].source_path).parent.parent
            text.append(f"\nSkills: {len(ds.eligible_skills)} carregadas de {skills_dir}")
        return Panel(text, border_style="cyan")

    def _build_branches_panel(self, runtime: dict[str, Any]) -> Panel:
        recursion = _runtime_recursion_payload(runtime)
        controls = _dict_payload(recursion.get("controls"))
        summary = _dict_payload(recursion.get("summary"))
        priorities = _dict_payload(controls.get("branch_priorities"))
        tree = Tree("Branches", guide_style="cyan")
        for item in _list_payload(recursion.get("branches")):
            branch_item = _dict_payload(item)
            branch_id = branch_item.get("branch_id")
            metadata = _dict_payload(branch_item.get("metadata"))
            label = (
                f"branch {branch_id} | {branch_item.get('title', 'sem titulo')} | "
                f"{branch_item.get('mode', '-')} | {branch_item.get('status', '-')}"
            )
            if bool(branch_item.get("operator_fixed_winner")) or summary.get("winner_branch_id") == branch_id:
                label += " | winner"
            if bool(branch_item.get("operator_focused")) or controls.get("focused_branch_id") == branch_id:
                label += " | focus"
            operator_priority = branch_item.get("operator_priority")
            if operator_priority in (None, "") and str(branch_id) in priorities:
                operator_priority = priorities.get(str(branch_id))
            if operator_priority not in (None, ""):
                label += f" | prio={operator_priority}"
            branch = tree.add(label)
            if metadata:
                for key, value in sorted(metadata.items()):
                    branch.add(f"{key}: {value}")

        tasks = _dict_payload(runtime.get("tasks"))
        current = _dict_payload(tasks.get("current"))
        content: RenderableType = Group(
            tree,
            Text(),
            Text(f"Task atual: {current.get('title', '-')} [{current.get('status', '-')}]", style="bold"),
            Text(current.get("note") or "", style="dim"),
        )
        return Panel(content, title="Branches", border_style="green")

    def _build_messages_panel(self, runtime: dict[str, Any]) -> Panel:
        recursive_session = _dict_payload(runtime.get("recursive_session"))
        messages = _list_payload(recursive_session.get("messages"))[-12:]
        timeline = _list_payload(_dict_payload(runtime.get("timeline")).get("entries"))
        blocks: list[RenderableType] = []
        if messages:
            message_table = Table(box=None, expand=True, show_header=True)
            message_table.add_column("Role", style="cyan", width=10)
            message_table.add_column("Conteudo")
            for message in messages:
                message_payload = _dict_payload(message)
                message_table.add_row(str(message_payload.get("role", "?")), str(message_payload.get("content", "")))
            blocks.append(message_table)
        else:
            blocks.append(Text("Sem mensagens recursivas ainda.", style="dim"))

        blocks.append(Text())
        blocks.append(Text("Timeline", style="bold magenta"))
        timeline_table = Table(box=None, expand=True, show_header=True)
        timeline_table.add_column("Tipo", width=18)
        timeline_table.add_column("Resumo")
        for entry in timeline[-8:]:
            timeline_entry = _dict_payload(entry)
            summary = (
                timeline_entry.get("summary")
                or timeline_entry.get("message")
                or timeline_entry.get("title")
                or str(timeline_entry.get("payload") or "")
            )
            timeline_table.add_row(str(timeline_entry.get("event_type") or timeline_entry.get("kind") or "-"), str(summary))
        if timeline:
            blocks.append(timeline_table)
        else:
            blocks.append(Text("Sem timeline publicada.", style="dim"))
        return Panel(Group(*blocks), title="Mensagens E Timeline", border_style="blue")

    def _build_events_panel(self, activity: dict[str, Any], runtime: dict[str, Any]) -> Panel:
        event_log = _list_payload(activity.get("event_log"))
        operation_log = _list_payload(activity.get("operation_log"))
        recursion = _runtime_recursion_payload(runtime)
        recursive_events = _list_payload(_dict_payload(runtime.get("recursive_session")).get("events"))[-8:]
        coordination_events = _list_payload(recursion.get("events"))[-6:]
        latest_response = self._session_metadata().get("last_operator_response") or "-"
        latest_operation = _dict_payload(operation_log[-1] if operation_log else {})
        latest_operation_name = str(latest_operation.get("operation") or "-")
        latest_operation_status = str(latest_operation.get("status") or "")
        if latest_operation_status:
            latest_operation_name = f"{latest_operation_name}/{latest_operation_status}"

        event_table = Table(box=None, expand=True, show_header=True)
        event_table.add_column("Fonte", width=12)
        event_table.add_column("Evento", width=24)
        event_table.add_column("Resumo")
        for item in reversed(event_log[-8:]):
            event_item = _dict_payload(item)
            payload = _dict_payload(event_item.get("payload"))
            summary = (
                payload.get("text_preview")
                or payload.get("response_preview")
                or payload.get("error")
                or payload.get("command_type")
                or str(payload)
            )
            event_table.add_row("session", str(event_item.get("event_type", "-")), str(summary))
        for item in reversed(operation_log[-6:]):
            operation_item = _dict_payload(item)
            operation_name = str(operation_item.get("operation") or "-")
            operation_status = str(operation_item.get("status") or "")
            if operation_status:
                operation_name = f"{operation_name}/{operation_status}"
            event_table.add_row("op", operation_name, _operation_summary(operation_item))
        for item in reversed(recursive_events):
            event_item = _dict_payload(item)
            payload = _dict_payload(event_item.get("payload"))
            event_table.add_row("runtime", str(event_item.get("event_type", "-")), str(payload or event_item.get("source") or ""))
        for item in reversed(coordination_events):
            event_item = _dict_payload(item)
            event_table.add_row("coord", str(event_item.get("operation", "-")), str(event_item.get("payload_preview") or event_item.get("topic") or ""))

        help_text = Text()
        help_text.append("/pause [motivo]  /resume [motivo]\n")
        help_text.append("/focus <branch> [nota]  /winner <branch>\n")
        help_text.append("/priority <branch> <prio> [nota]\n")
        help_text.append("/checkpoint [nome]  /note <texto>\n")
        help_text.append("/channels  /probe <canal>  /send <id> <msg>\n")
        help_text.append("/watch [segundos]  /quit\n")
        help_text.append("Texto livre envia prompt ao runtime", style="dim")

        content = Group(
            Text(f"Resposta mais recente: {latest_response}", style="bold yellow"),
            Text("Ultima operacao", style="bold yellow"),
            Text(f"{latest_operation_name} :: {_operation_summary(latest_operation)}"),
            Text("Eventos", style="bold yellow"),
            event_table,
            Text("Controles", style="bold yellow"),
            help_text,
        )
        return Panel(content, title="Eventos E Comandos", border_style="yellow")

    def _build_footer(self, runtime: dict[str, Any]) -> Panel:
        controls = _dict_payload(_runtime_recursion_payload(runtime).get("controls"))
        daemon = _daemon_payload(runtime)
        warm_runtime = _dict_payload(daemon.get("warm_runtime"))
        outbox = _dict_payload(daemon.get("outbox"))
        channel_runtime = _dict_payload(daemon.get("channel_runtime"))
        memory_access = _dict_payload(daemon.get("memory_access"))
        memory_scope = _dict_payload(memory_access.get("last_scope"))
        text = Text()
        text.append(self.last_notice, style="bold")
        text.append("\n")
        text.append(f"Pause reason: {controls.get('pause_reason') or '-'}  ")
        text.append(f"Operator note: {controls.get('last_operator_note') or '-'}\n")
        if daemon:
            text.append(
                f"Warm runtime: req={warm_runtime.get('requests', 0)}  "
                f"warmed={warm_runtime.get('warmed', 0)}  already={warm_runtime.get('already_warm', 0)}  "
                f"failed={warm_runtime.get('failed', 0)}\n"
            )
            text.append(
                f"Flow: sessions={int(daemon.get('active_sessions', 0) or 0)}  "
                f"channels={channel_runtime.get('running', 0)}/{channel_runtime.get('total', 0)}  "
                f"healthy={channel_runtime.get('healthy', 0)}  "
                f"backlog={outbox.get('backlog', 0)}  dlq={outbox.get('dlq', 0)}\n"
            )
            if memory_access:
                text.append(f"Memory scope: {_memory_scope_summary(memory_scope)}\n")
        if self._use_polled_input:
            text.append(f"Input: {self._input_buffer or ' '}\n", style="cyan")
        text.append(f"Refresh: {self.refresh_interval:.2f}s  ")
        text.append(f"Estado salvo em: {Path(getattr(self.session, 'state_dir', '.')).as_posix()}")
        return Panel(text, border_style="magenta")


def run_workbench(context: CliContext, *, client_id: str | None, refresh_interval: float, once: bool) -> int:
    resolved_client_id = client_id or "tui:default"
    console = Console()
    live_api: Any | None = None

    # --- Tenta modo live (servidor vivo) ---
    try:
        from rlm.cli.tui.live_api import LiveWorkbenchAPI

        live_api = LiveWorkbenchAPI(context)
        if _probe_live_api(live_api):
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
        live_api = None

    live_api = _ensure_live_service(context, console, live_api)
    if live_api is not None:
        workbench = RuntimeWorkbench(
            None,
            client_id=resolved_client_id,
            refresh_interval=refresh_interval,
            console=console,
            live_api=live_api,
        )
        return workbench.run(once=once)

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
