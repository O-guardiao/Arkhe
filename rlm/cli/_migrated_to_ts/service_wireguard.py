from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Callable


def add_wireguard_peer_impl(
    name: str,
    pubkey: str,
    ip: str,
    *,
    wg_conf: Path,
    os_geteuid: Callable[[], int],
    ok: Callable[[str], None],
    warn: Callable[[str], None],
    err: Callable[[str], None],
    info: Callable[[str], None],
) -> int:
    if not wg_conf.exists():
        err("wg0.conf não encontrado em /etc/wireguard/wg0.conf")
        info("Verifique a documentação em docs/seguranca-rede-e-multiconexoes.md")
        return 1

    block = textwrap.dedent(f"""
        # peer: {name}
        [Peer]
        PublicKey = {pubkey}
        AllowedIPs = {ip}/32
    """)

    existing = wg_conf.read_text()
    if pubkey in existing:
        warn(f"Peer com pubkey ...{pubkey[-12:]} já existe no wg0.conf")
        return 0

    if os_geteuid() != 0:
        try:
            proc = subprocess.run(
                ["sudo", "tee", "-a", str(wg_conf)],
                input=block.encode(),
                capture_output=True,
            )
            if proc.returncode != 0:
                err(f"Falha ao escrever wg0.conf: {proc.stderr.decode()}")
                return 1
        except FileNotFoundError:
            err("sudo não encontrado — execute como root")
            return 1
    else:
        with open(wg_conf, "a") as file_handle:
            file_handle.write(block)

    if shutil.which("wg"):
        subprocess.run(["sudo", "wg", "addconf", "wg0", "/dev/stdin"], input=block.encode(), capture_output=True)
    if shutil.which("systemctl"):
        subprocess.run(["sudo", "systemctl", "reload", "wg-quick@wg0"], capture_output=True)

    ok(f"Peer '{name}' ({ip}) adicionado ao wg0.conf")
    return 0