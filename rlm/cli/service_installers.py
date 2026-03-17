from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable


SYSTEMD_UNIT = """\
[Unit]
Description=RLM — Recursive Language Model Server
After=network.target

[Service]
Type=simple
WorkingDirectory={work_dir}
EnvironmentFile={env_file}
ExecStart={python} -m rlm.server.api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


PLIST_TEMPLATE = """\
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


def install_systemd_service_impl(
    project_root: Path,
    env_path: Path,
    *,
    service_name: str,
    ok: Callable[[str], None],
    err: Callable[[str], None],
    info: Callable[[str], None],
) -> tuple[int, Path]:
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)

    unit_file = systemd_dir / f"{service_name}.service"
    content = SYSTEMD_UNIT.format(
        work_dir=project_root,
        env_file=env_path,
        python=sys.executable,
    )
    unit_file.write_text(content)
    ok(f"Unit file criado: {unit_file}")

    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", service_name],
        ["systemctl", "--user", "start", service_name],
    ):
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            err(f"Falha em `{' '.join(cmd)}`: {result.stderr.decode()}")
            return 1, unit_file
        ok(f"$ {' '.join(cmd)}")

    ok("Serviço systemd instalado e iniciado")
    info(f"Use `systemctl --user status {service_name}` para verificar")
    return 0, unit_file


def install_launchd_service_impl(
    project_root: Path,
    env_path: Path,
    log_dir: Path,
    *,
    os_getuid: Callable[[], int],
    ok: Callable[[str], None],
    info: Callable[[str], None],
) -> tuple[int, Path]:
    launch_dir = Path.home() / "Library" / "LaunchAgents"
    launch_dir.mkdir(parents=True, exist_ok=True)
    plist_path = launch_dir / "com.rlm.server.plist"

    log_dir.mkdir(parents=True, exist_ok=True)

    env_items: list[str] = []
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, _, value = stripped.partition("=")
                env_items.append(f"    <key>{key.strip()}</key><string>{value.strip()}</string>")

    content = PLIST_TEMPLATE.format(
        python=sys.executable,
        work_dir=project_root,
        log_dir=log_dir,
        env_dict="\n".join(env_items),
    )
    plist_path.write_text(content)
    ok(f"Plist criada: {plist_path}")

    for cmd in (
        ["launchctl", "bootout", "gui/{uid}", str(plist_path)],
        ["launchctl", "bootstrap", "gui/{uid}", str(plist_path)],
    ):
        real_cmd = [part.format(uid=os_getuid()) for part in cmd]
        subprocess.run(real_cmd, capture_output=True)

    ok("LaunchAgent instalado")
    info("Use `launchctl list com.rlm.server` para verificar")
    return 0, plist_path