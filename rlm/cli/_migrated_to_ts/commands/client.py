"""
rlm client add/list/revoke/status — gerenciamento de dispositivos/clientes.

Camada 3 da arquitetura multidevice: cada dispositivo possui token próprio,
perfil, context_hint e permissions.  O token é exibido uma única vez no
momento do ``add`` e nunca mais.

Referência: docs/arquitetura-config-multidevice.md §7.
"""
from __future__ import annotations

import argparse
import json

from rlm.cli.context import CliContext, print_error, print_success


def _get_db_path(context: CliContext | None) -> str:
    """Resolve o caminho do SQLite de sessões."""
    if context is not None:
        db = context.paths.data_dir / "rlm_sessions.db"
        if db.exists():
            return str(db)
    # Fallback: CWD
    return "rlm_sessions.db"


def cmd_client_add(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Registra novo dispositivo/cliente e imprime o token."""
    from rlm.core.auth import register_client

    current = context if context is not None else CliContext.from_environment()
    db_path = _get_db_path(current)
    client_id = args.client_id
    profile = getattr(args, "profile", "default") or "default"
    description = getattr(args, "description", "") or ""
    context_hint = getattr(args, "context", "") or ""

    # Parse optional metadata JSON
    meta_raw = getattr(args, "metadata", None)
    metadata: dict = {}
    if meta_raw:
        try:
            metadata = json.loads(meta_raw)
        except json.JSONDecodeError:
            print_error(f"--metadata inválido (não é JSON): {meta_raw}")
            return 1

    try:
        raw_token = register_client(
            db_path=db_path,
            client_id=client_id,
            profile=profile,
            description=description,
            context_hint=context_hint,
            metadata=metadata,
        )
    except ValueError as e:
        print_error(str(e))
        return 1

    print_success(f"Cliente '{client_id}' criado (profile={profile})")
    print(f"  Token: {raw_token}")
    print("  ⚠  Copie agora — não será exibido novamente.")
    return 0


def cmd_client_list(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Lista clientes registrados."""
    from rlm.core.auth import list_clients

    current = context if context is not None else CliContext.from_environment()
    db_path = _get_db_path(current)
    show_all = getattr(args, "all", False)

    clients = list_clients(db_path, active_only=not show_all)
    if not clients:
        print("Nenhum cliente registrado.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title="Clientes RLM")
        table.add_column("ID", style="cyan")
        table.add_column("Profile", style="green")
        table.add_column("Ativo")
        table.add_column("Último Acesso")
        table.add_column("Descrição")

        for c in clients:
            active = "✓" if c["active"] else "✗"
            last_seen = c.get("last_seen") or "—"
            table.add_row(c["id"], c["profile"], active, last_seen, c.get("description", ""))

        Console().print(table)
    except ImportError:
        # Fallback plain text
        fmt = "{:<20} {:<12} {:<6} {:<22} {}"
        print(fmt.format("ID", "PROFILE", "ATIVO", "ÚLTIMO ACESSO", "DESCRIÇÃO"))
        print("-" * 80)
        for c in clients:
            active = "sim" if c["active"] else "não"
            last_seen = c.get("last_seen") or "—"
            print(fmt.format(c["id"], c["profile"], active, last_seen, c.get("description", "")))

    return 0


def cmd_client_revoke(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Revoga um cliente (sem deletar)."""
    from rlm.core.auth import revoke_client

    current = context if context is not None else CliContext.from_environment()
    db_path = _get_db_path(current)
    client_id = args.client_id

    if revoke_client(db_path, client_id):
        print_success(f"Cliente '{client_id}' revogado.")
        return 0
    else:
        print_error(f"Cliente '{client_id}' não encontrado ou já revogado.")
        return 1


def cmd_client_status(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Mostra status detalhado de um cliente."""
    from rlm.core.auth import get_client_status

    current = context if context is not None else CliContext.from_environment()
    db_path = _get_db_path(current)
    client_id = args.client_id

    info = get_client_status(db_path, client_id)
    if not info:
        print_error(f"Cliente '{client_id}' não encontrado.")
        return 1

    active = "ativo" if info["active"] else "REVOGADO"
    print(f"  ID:           {info['id']}")
    print(f"  Status:       {active}")
    print(f"  Profile:      {info['profile']}")
    print(f"  Descrição:    {info.get('description', '')}")
    print(f"  Context Hint: {info.get('context_hint', '')}")
    print(f"  Permissões:   {info.get('permissions', '[]')}")
    print(f"  Criado:       {info['created_at']}")
    print(f"  Último acesso:{info.get('last_seen') or '—'}")
    print(f"  Metadata:     {info.get('metadata', '{}')}")
    return 0
