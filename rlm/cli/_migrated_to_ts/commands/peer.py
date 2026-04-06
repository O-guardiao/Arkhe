from __future__ import annotations

import argparse

from rlm.cli.context import CliContext


def cmd_peer_add(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Adiciona peer WireGuard ao wg0.conf."""
    _ = context if context is not None else CliContext.from_environment()
    from rlm.cli.service import add_wireguard_peer

    return add_wireguard_peer(name=args.name, pubkey=args.pubkey, ip=args.ip)