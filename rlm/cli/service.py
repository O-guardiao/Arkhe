"""RLM Service Manager — start, stop, status, systemd/launchd, WireGuard.

Gerencia o ciclo de vida dos processos RLM (API FastAPI e WebSocket)
e a instalação de daemons no sistema operacional.

Funções públicas usadas por main.py:
  start_services(foreground, api_only, ws_only)
  stop_services()
  show_status()
  install_systemd_service(project_root, env_path)
  install_launchd_service(project_root, env_path)
  add_wireguard_peer(name, pubkey, ip)
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
import textwrap
from pathlib import Path


# --------------------------------------------------------------------------- #
# Configuração                                                                 #
# --------------------------------------------------------------------------- #

_PID_DIR = Path.home() / ".rlm" / "run"
_PID_API = _PID_DIR / "api.pid"
_PID_WS  = _PID_DIR / "ws.pid"
_LOG_DIR = Path.home() / ".rlm" / "logs"
_SERVICE_NAME = "rlm"


# --------------------------------------------------------------------------- #
# Helpers de console (rich disponível após `uv pip install -e .`)             #
# --------------------------------------------------------------------------- #

try:
    from rich.console import Console
    from rich.table import Table

    _c = Console()
    _e = Console(stderr=True)

    def _ok(msg: str) -> None:   _c.print(f"[bold green]✓[/] {msg}")
    def _warn(msg: str) -> None: _c.print(f"[yellow]⚠[/]  {msg}")
    def _err(msg: str) -> None:  _e.print(f"[bold red]✗[/] {msg}")
    def _info(msg: str) -> None: _c.print(f"[dim]→[/] {msg}")

    HAS_RICH = True

except ImportError:
    HAS_RICH = False

    def _ok(msg: str) -> None:   print(f"✓ {msg}")
    def _warn(msg: str) -> None: print(f"⚠  {msg}", file=sys.stderr)
    def _err(msg: str) -> None:  print(f"✗ {msg}", file=sys.stderr)
    def _info(msg: str) -> None: print(f"→ {msg}")


# --------------------------------------------------------------------------- #
# PID helpers                                                                  #
# --------------------------------------------------------------------------- #

def _read_pid(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text().strip())
    except Exception:
        return None


def _write_pid(pid_file: Path, pid: int) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid))


def _pid_alive(pid: int) -> bool:
    """Retorna True se o processo com `pid` está em execução.

    Usa abordagem compatível com Windows (tasklist) e POSIX (os.kill sig=0).
    No Windows, os.kill(pid, 0) equivale a CTRL_C_EVENT — NÃO usar para check.
    """
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/fi", f"PID eq {pid}", "/nh", "/fo", "csv"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return f'"{pid}"' in result.stdout or str(pid) in result.stdout
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


# --------------------------------------------------------------------------- #
# start_services                                                               #
# --------------------------------------------------------------------------- #

def start_services(
    foreground: bool = False,
    api_only: bool = False,
    ws_only: bool = False,
) -> int:
    """Inicia os servidores RLM (API e/ou WebSocket)."""
    python = sys.executable
    project_root = Path.cwd()
    env_file = project_root / ".env"
    if not env_file.exists():
        env_file = Path.home() / ".rlm" / ".env"

    # Env vars para os subprocessos
    env = os.environ.copy()
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, v = stripped.partition("=")
                env.setdefault(k.strip(), v.strip())

    if foreground:
        _info("Iniciando em foreground (Ctrl+C para parar)...")
        # Roda ambos em foreground via módulo principal
        try:
            from rlm.server.api import start_server
            from rlm.server.ws_server import start_ws_server
            import threading
            if not api_only:
                t_ws = threading.Thread(
                    target=start_ws_server,
                    kwargs={
                        "host": env.get("RLM_WS_HOST", "127.0.0.1"),
                        "port": int(env.get("RLM_WS_PORT", "8765")),
                        "ws_token": env.get("RLM_WS_TOKEN"),
                    },
                    daemon=True,
                )
                t_ws.start()
            if not ws_only:
                start_server(
                    host=env.get("RLM_API_HOST", "127.0.0.1"),
                    port=int(env.get("RLM_API_PORT", "5000")),
                )
        except KeyboardInterrupt:
            _info("Encerrado pelo usuário.")
        return 0

    # Background
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _PID_DIR.mkdir(parents=True, exist_ok=True)

    started: list[str] = []

    if not ws_only:
        _info("Iniciando API FastAPI...")
        log_api = _LOG_DIR / "api.log"
        proc_api = subprocess.Popen(
            [python, "-m", "rlm.server.api"],
            env=env,
            stdout=open(log_api, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _write_pid(_PID_API, proc_api.pid)
        _ok(f"API iniciada  pid={proc_api.pid}  log={log_api}")
        started.append("API")

    if not api_only:
        _info("Iniciando servidor WebSocket...")
        log_ws = _LOG_DIR / "ws.log"
        proc_ws = subprocess.Popen(
            [python, "-m", "rlm.server.ws_server"],
            env=env,
            stdout=open(log_ws, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _write_pid(_PID_WS, proc_ws.pid)
        _ok(f"WS  iniciado  pid={proc_ws.pid}  log={log_ws}")
        started.append("WebSocket")

    if started:
        _ok(f"RLM em execução ({', '.join(started)})  —  use `rlm stop` para encerrar")
    else:
        _warn("Nenhum serviço foi iniciado (--api-only e --ws-only ao mesmo tempo?)")

    return 0


# --------------------------------------------------------------------------- #
# stop_services                                                                #
# --------------------------------------------------------------------------- #

def stop_services() -> int:
    """Para os processos RLM via PID files ou systemctl."""

    # Tenta systemctl primeiro
    if shutil.which("systemctl"):
        result = subprocess.run(
            ["systemctl", "--user", "is-active", _SERVICE_NAME],
            capture_output=True,
        )
        if result.returncode == 0:
            subprocess.run(["systemctl", "--user", "stop", _SERVICE_NAME])
            _ok("Daemon systemd parado")
            return 0

    stopped = False
    for label, pid_file in (("API", _PID_API), ("WebSocket", _PID_WS)):
        pid = _read_pid(pid_file)
        if pid is None:
            continue
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                _ok(f"{label} encerrado (pid={pid})")
                stopped = True
            except Exception as exc:
                _err(f"Não foi possível encerrar {label} pid={pid}: {exc}")
        else:
            _info(f"{label} pid={pid} não estava rodando")
        pid_file.unlink(missing_ok=True)

    if not stopped:
        _warn("Nenhum processo RLM encontrado.")

    return 0


# --------------------------------------------------------------------------- #
# show_status                                                                  #
# --------------------------------------------------------------------------- #

def show_status() -> int:
    """Mostra status dos processos e configuração de endpoints."""
    if HAS_RICH:
        table = Table(title="Status RLM", show_header=True, header_style="bold cyan")
        table.add_column("Serviço", style="bold")
        table.add_column("PID")
        table.add_column("Status")
        table.add_column("Log")

        for label, pid_file, log_file in (
            ("API FastAPI",      _PID_API, _LOG_DIR / "api.log"),
            ("WebSocket Server", _PID_WS,  _LOG_DIR / "ws.log"),
        ):
            pid = _read_pid(pid_file)
            if pid and _pid_alive(pid):
                status = "[bold green]● ativo[/]"
            elif pid:
                status = "[red]✗ morto (pid inválido)[/]"
            else:
                status = "[dim]● parado[/]"
            table.add_row(label, str(pid or "—"), status, str(log_file))

        from rich.console import Console
        Console().print(table)
    else:
        for label, pid_file in (("API", _PID_API), ("WS", _PID_WS)):
            pid = _read_pid(pid_file)
            alive = pid and _pid_alive(pid)
            print(f"  {label}: {'ativo' if alive else 'parado'} (pid={pid or '—'})")

    # Mostrar endpoints a partir do .env
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        env_path = Path.home() / ".rlm" / ".env"

    if env_path.exists():
        cfg: dict[str, str] = {}
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()

        api_host = cfg.get("RLM_API_HOST", "127.0.0.1")
        api_port = cfg.get("RLM_API_PORT", "5000")
        ws_host  = cfg.get("RLM_WS_HOST",  "127.0.0.1")
        ws_port  = cfg.get("RLM_WS_PORT",  "8765")

        _info(f"API:  http://{api_host}:{api_port}/")
        _info(f"WS:   ws://{ws_host}:{ws_port}/")
        _info(f"Docs: http://{api_host}:{api_port}/docs")

    return 0


# --------------------------------------------------------------------------- #
# update_installation                                                          #
# --------------------------------------------------------------------------- #

def _services_are_running() -> bool:
    """Retorna True se API ou WebSocket do RLM estiverem ativos."""
    for pid_file in (_PID_API, _PID_WS):
        pid = _read_pid(pid_file)
        if pid is not None and _pid_alive(pid):
            return True
    return False


def update_installation(check_only: bool = False, restart: bool = True) -> int:
    """Atualiza um checkout git do RLM e sincroniza dependências com uv."""
    project_root = Path.cwd()

    if not (project_root / ".git").exists():
        _err("`rlm update` requer um checkout git do projeto na pasta atual.")
        return 1

    if shutil.which("git") is None:
        _err("`git` não encontrado no PATH.")
        return 1

    if shutil.which("uv") is None:
        _err("`uv` não encontrado no PATH.")
        return 1

    _info("Validando worktree local...")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        _err((status.stderr or status.stdout or "Falha ao ler estado do git.").strip())
        return 1
    if status.stdout.strip():
        _err("Há mudanças locais não commitadas. Faça commit/stash antes de atualizar.")
        return 1

    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if branch_result.returncode != 0:
        _err((branch_result.stderr or branch_result.stdout or "Falha ao detectar branch atual.").strip())
        return 1
    branch = branch_result.stdout.strip()
    if not branch or branch == "HEAD":
        _err("Branch atual inválida para update automático.")
        return 1

    _info(f"Buscando updates remotos para '{branch}'...")
    fetch = subprocess.run(
        ["git", "fetch", "origin", branch, "--quiet"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        _err((fetch.stderr or fetch.stdout or "Falha no git fetch.").strip())
        return 1

    rev_list = subprocess.run(
        ["git", "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if rev_list.returncode != 0:
        _err((rev_list.stderr or rev_list.stdout or "Falha ao comparar commits.").strip())
        return 1

    counts = rev_list.stdout.strip().split()
    if len(counts) != 2:
        _err("Saída inesperada do git rev-list ao comparar atualizações.")
        return 1

    ahead_count = int(counts[0])
    behind_count = int(counts[1])

    if check_only:
        if behind_count == 0 and ahead_count == 0:
            _ok("Checkout já está sincronizado com origin.")
        elif behind_count == 0:
            _warn("Checkout local está à frente do remoto; nada para baixar.")
        else:
            _warn(f"Há {behind_count} commit(s) pendente(s) em origin/{branch}.")
        return 0

    if behind_count == 0:
        _ok("Nenhuma atualização remota disponível.")
        return 0

    _info("Aplicando git pull --ff-only...")
    pull = subprocess.run(
        ["git", "pull", "--ff-only", "origin", branch],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if pull.returncode != 0:
        _err((pull.stderr or pull.stdout or "Falha no git pull.").strip())
        return 1
    _ok(f"Código atualizado: {behind_count} commit(s) aplicados.")

    _info("Reinstalando dependências com uv sync...")
    sync = subprocess.run(
        ["uv", "sync"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if sync.returncode != 0:
        _err((sync.stderr or sync.stdout or "Falha no uv sync.").strip())
        return 1
    _ok("Dependências sincronizadas.")

    if restart and _services_are_running():
        _info("Reiniciando serviços do RLM...")
        stop_services()
        start_services(foreground=False)
        _ok("Serviços reiniciados.")
    elif restart:
        _info("Serviços não estavam ativos; nenhum restart necessário.")

    return 0


# --------------------------------------------------------------------------- #
# systemd                                                                      #
# --------------------------------------------------------------------------- #

_SYSTEMD_UNIT = """\
[Unit]
Description=RLM — Recursive Language Model Server
After=network.target

[Service]
Type=simple
WorkingDirectory={work_dir}
EnvironmentFile={env_file}
ExecStart={python} -m rlm.server.api
ExecStartPost=/bin/sleep 1
ExecStartPost=/bin/sh -c '{python} -m rlm.server.ws_server &'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_systemd_service(project_root: Path, env_path: Path) -> int:
    """Instala rlm.service como serviço systemd do usuário."""
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)

    unit_file = systemd_dir / f"{_SERVICE_NAME}.service"
    content = _SYSTEMD_UNIT.format(
        work_dir=project_root,
        env_file=env_path,
        python=sys.executable,
    )
    unit_file.write_text(content)
    _ok(f"Unit file criado: {unit_file}")

    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", _SERVICE_NAME],
        ["systemctl", "--user", "start",  _SERVICE_NAME],
    ):
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            _err(f"Falha em `{' '.join(cmd)}`: {result.stderr.decode()}")
            return 1
        _ok(f"$ {' '.join(cmd)}")

    _ok("Serviço systemd instalado e iniciado")
    _info("Use `systemctl --user status rlm` para verificar")
    return 0


# --------------------------------------------------------------------------- #
# launchd (macOS)                                                              #
# --------------------------------------------------------------------------- #

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>        <string>com.rlm.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>rlm.server.api</string>
  </array>
  <key>WorkingDirectory</key>  <string>{work_dir}</string>
  <key>EnvironmentVariables</key>
  <dict>
{env_dict}
  </dict>
  <key>RunAtLoad</key>     <true/>
  <key>KeepAlive</key>     <true/>
  <key>StandardOutPath</key>  <string>{log_dir}/api.log</string>
  <key>StandardErrorPath</key> <string>{log_dir}/api.log</string>
</dict>
</plist>
"""


def install_launchd_service(project_root: Path, env_path: Path) -> int:
    """Instala com.rlm.server.plist como LaunchAgent no macOS."""
    launch_dir = Path.home() / "Library" / "LaunchAgents"
    launch_dir.mkdir(parents=True, exist_ok=True)
    plist_path = launch_dir / "com.rlm.server.plist"

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Monta bloco de env vars do .env
    env_items: list[str] = []
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, v = stripped.partition("=")
                env_items.append(
                    f"    <key>{k.strip()}</key><string>{v.strip()}</string>"
                )

    content = _PLIST_TEMPLATE.format(
        python=sys.executable,
        work_dir=project_root,
        log_dir=_LOG_DIR,
        env_dict="\n".join(env_items),
    )
    plist_path.write_text(content)
    _ok(f"Plist criada: {plist_path}")

    for cmd in (
        ["launchctl", "bootout",  "gui/{os.getuid()}", str(plist_path)],
        ["launchctl", "bootstrap","gui/{os.getuid()}", str(plist_path)],
    ):
        real_cmd = [c.format(**{"os.getuid()": os.getuid()}) for c in cmd]
        subprocess.run(real_cmd, capture_output=True)

    _ok("LaunchAgent instalado")
    _info("Use `launchctl list com.rlm.server` para verificar")
    return 0


# --------------------------------------------------------------------------- #
# WireGuard peer add                                                           #
# --------------------------------------------------------------------------- #

def add_wireguard_peer(name: str, pubkey: str, ip: str) -> int:
    """Adiciona peer ao wg0.conf. Requer root/sudo."""
    wg_conf = Path("/etc/wireguard/wg0.conf")
    if not wg_conf.exists():
        _err("wg0.conf não encontrado em /etc/wireguard/wg0.conf")
        _info("Verifique a documentação em docs/seguranca-rede-e-multiconexoes.md")
        return 1

    block = textwrap.dedent(f"""
        # peer: {name}
        [Peer]
        PublicKey = {pubkey}
        AllowedIPs = {ip}/32
    """)

    # Verifica se pubkey já existe
    existing = wg_conf.read_text()
    if pubkey in existing:
        _warn(f"Peer com pubkey ...{pubkey[-12:]} já existe no wg0.conf")
        return 0

    # Appenda com sudo se necessário
    if os.geteuid() != 0:
        try:
            proc = subprocess.run(
                ["sudo", "tee", "-a", str(wg_conf)],
                input=block.encode(),
                capture_output=True,
            )
            if proc.returncode != 0:
                _err(f"Falha ao escrever wg0.conf: {proc.stderr.decode()}")
                return 1
        except FileNotFoundError:
            _err("sudo não encontrado — execute como root")
            return 1
    else:
        with open(wg_conf, "a") as f:
            f.write(block)

    # Recarrega WireGuard
    if shutil.which("wg"):
        subprocess.run(["sudo", "wg", "addconf", "wg0", "/dev/stdin"],
                       input=block.encode(), capture_output=True)
    if shutil.which("systemctl"):
        subprocess.run(["sudo", "systemctl", "reload", "wg-quick@wg0"], capture_output=True)

    _ok(f"Peer '{name}' ({ip}) adicionado ao wg0.conf")
    return 0
