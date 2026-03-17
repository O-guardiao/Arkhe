from __future__ import annotations

import os
import signal
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from rlm.cli.context import CliContext


@dataclass(frozen=True, slots=True)
class ServiceRuntimeLayout:
    pid_dir: Path
    pid_api: Path
    pid_ws: Path
    log_dir: Path
    service_name: str

    @property
    def api_log(self) -> Path:
        return self.log_dir / "api.log"

    @property
    def ws_log(self) -> Path:
        return self.log_dir / "ws.log"


def runtime_mode(*, foreground: bool, api_only: bool, ws_only: bool) -> str:
    if foreground and ws_only:
        return "foreground-ws"
    if foreground and api_only:
        return "foreground-api"
    if foreground:
        return "foreground-combined"
    if ws_only:
        return "background-ws"
    if api_only:
        return "background-api"
    return "background-combined"


def port_accepting_connections(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def start_runtime(
    context: CliContext,
    layout: ServiceRuntimeLayout,
    *,
    foreground: bool,
    api_only: bool,
    ws_only: bool,
    write_pid: Callable[[Path, int], None],
    remove_pid: Callable[[Path], None],
    ok: Callable[[str], None],
    warn: Callable[[str], None],
    err: Callable[[str], None],
    info: Callable[[str], None],
) -> int:
    python = sys.executable
    env = os.environ.copy()
    env.update(context.env)

    if foreground:
        info("Iniciando em foreground (Ctrl+C para parar)...")
        previous_env = os.environ.copy()
        try:
            from rlm.server.api import start_server

            os.environ.update(env)
            if api_only:
                os.environ["RLM_WS_DISABLED"] = "true"
            else:
                os.environ.pop("RLM_WS_DISABLED", None)

            if ws_only:
                return subprocess.run([python, "-m", "rlm.server.ws_server"], env=env).returncode

            start_server(
                host=env.get("RLM_API_HOST", "127.0.0.1"),
                port=int(env.get("RLM_API_PORT", "5000")),
            )
        except KeyboardInterrupt:
            info("Encerrado pelo usuário.")
        finally:
            os.environ.clear()
            os.environ.update(previous_env)
        return 0

    layout.log_dir.mkdir(parents=True, exist_ok=True)
    layout.pid_dir.mkdir(parents=True, exist_ok=True)

    started: list[str] = []
    api_proc = None

    if not ws_only:
        info("Iniciando API FastAPI...")
        env.pop("RLM_WS_DISABLED", None)
        if api_only:
            env["RLM_WS_DISABLED"] = "true"
        api_handle = open(layout.api_log, "a")
        proc_api = subprocess.Popen(
            [python, "-m", "rlm.server.api"],
            env=env,
            stdout=api_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        api_handle.close()
        api_proc = proc_api
        write_pid(layout.pid_api, proc_api.pid)
        if api_only:
            remove_pid(layout.pid_ws)
        else:
            write_pid(layout.pid_ws, proc_api.pid)
        time.sleep(1.5)
        if proc_api.poll() is not None:
            remove_pid(layout.pid_api)
            remove_pid(layout.pid_ws)
            err(f"API falhou ao iniciar (exit={proc_api.returncode}). Últimas linhas de {layout.api_log}:")
            try:
                lines = layout.api_log.read_text().splitlines()
                for ln in lines[-15:]:
                    print(f"  {ln}")
            except Exception:
                pass
            return 1
        ok(f"API iniciada  pid={proc_api.pid}  log={layout.api_log}")
        started.append("API")
        if not api_only:
            started.append("WebSocket")

    if ws_only:
        info("Iniciando servidor WebSocket...")
        ws_handle = open(layout.ws_log, "a")
        proc_ws = subprocess.Popen(
            [python, "-m", "rlm.server.ws_server"],
            env=env,
            stdout=ws_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        ws_handle.close()
        write_pid(layout.pid_ws, proc_ws.pid)
        time.sleep(1.0)
        if proc_ws.poll() is not None:
            remove_pid(layout.pid_ws)
            err(f"WebSocket falhou ao iniciar (exit={proc_ws.returncode}). Últimas linhas de {layout.ws_log}:")
            try:
                lines = layout.ws_log.read_text().splitlines()
                for ln in lines[-10:]:
                    print(f"  {ln}")
            except Exception:
                pass
            if api_proc is not None and api_proc.poll() is None:
                os.kill(api_proc.pid, signal.SIGTERM)
                remove_pid(layout.pid_api)
            return 1
        ok(f"WS  iniciado  pid={proc_ws.pid}  log={layout.ws_log}")
        started.append("WebSocket")

    if started:
        ok(f"RLM em execução ({', '.join(started)})  —  use `rlm stop` para encerrar")
    else:
        warn("Nenhum serviço foi iniciado (--api-only e --ws-only ao mesmo tempo?)")

    return 0


def stop_runtime(
    layout: ServiceRuntimeLayout,
    *,
    read_pid: Callable[[Path], int | None],
    remove_pid: Callable[[Path], None],
    pid_alive: Callable[[int], bool],
    ok: Callable[[str], None],
    warn: Callable[[str], None],
    err: Callable[[str], None],
    info: Callable[[str], None],
) -> int:
    if shutil.which("systemctl"):
        result = subprocess.run(
            ["systemctl", "--user", "is-active", layout.service_name],
            capture_output=True,
        )
        if result.returncode == 0:
            subprocess.run(["systemctl", "--user", "stop", layout.service_name])
            ok("Daemon systemd parado")
            return 0

    stopped = False
    terminated_pids: set[int] = set()
    for label, pid_file in (("API", layout.pid_api), ("WebSocket", layout.pid_ws)):
        pid = read_pid(pid_file)
        if pid is None:
            continue
        if pid in terminated_pids:
            remove_pid(pid_file)
            continue
        if pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                ok(f"{label} encerrado (pid={pid})")
                stopped = True
                terminated_pids.add(pid)
            except Exception as exc:
                err(f"Não foi possível encerrar {label} pid={pid}: {exc}")
        else:
            info(f"{label} pid={pid} não estava rodando")
        remove_pid(pid_file)

    if not stopped:
        warn("Nenhum processo RLM encontrado.")

    return 0


def show_runtime_status(
    context: CliContext,
    layout: ServiceRuntimeLayout,
    *,
    read_pid: Callable[[Path], int | None],
    pid_alive: Callable[[int], bool],
    info: Callable[[str], None],
    warn: Callable[[str], None],
    has_rich: bool,
    table_cls: type[Any] | None,
) -> tuple[int, bool, bool]:
    api_pid = read_pid(layout.pid_api)
    ws_pid = read_pid(layout.pid_ws)
    api_running = bool(api_pid and pid_alive(api_pid))
    ws_running = bool(ws_pid and pid_alive(ws_pid))

    if has_rich and table_cls is not None:
        table = table_cls(title="Status RLM", show_header=True, header_style="bold cyan")
        table.add_column("Serviço", style="bold")
        table.add_column("PID")
        table.add_column("Status")
        table.add_column("Log")

        for label, pid, running, log_file in (
            ("API FastAPI", api_pid, api_running, layout.api_log),
            ("WebSocket Server", ws_pid, ws_running, layout.ws_log),
        ):
            if running:
                if label == "WebSocket Server" and pid == api_pid:
                    status = "[bold green]● ativo (embutido na API)[/]"
                else:
                    status = "[bold green]● ativo[/]"
            elif pid:
                status = "[red]✗ morto (pid inválido)[/]"
            else:
                status = "[dim]● parado[/]"
            table.add_row(label, str(pid or "—"), status, str(log_file))

        from rich.console import Console

        Console().print(table)
    else:
        for label, pid, running in (("API", api_pid, api_running), ("WS", ws_pid, ws_running)):
            print(f"  {label}: {'ativo' if running else 'parado'} (pid={pid or '—'})")

    info(f"API:  {context.api_base_url()}/")
    info(f"WS:   {context.ws_base_url()}/")
    info(f"Docs: {context.docs_url()}")
    info(f"Chat: {context.webchat_url()}")
    if port_accepting_connections(context.api_host(), context.api_port()) and not api_pid:
        warn("Porta da API responde, mas não há PID file. Pode haver processo externo ao gerenciador.")

    return 0, api_running, ws_running


def services_are_running(
    layout: ServiceRuntimeLayout,
    *,
    read_pid: Callable[[Path], int | None],
    pid_alive: Callable[[int], bool],
) -> bool:
    for pid_file in (layout.pid_api, layout.pid_ws):
        pid = read_pid(pid_file)
        if pid is not None and pid_alive(pid):
            return True
    return False