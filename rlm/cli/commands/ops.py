from __future__ import annotations

import argparse

from rlm.cli.context import CliContext, require_supported_runtime


def _context_or_default(context: CliContext | None) -> CliContext:
    return context if context is not None else CliContext.from_environment()


def cmd_start(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Inicia o servidor Arkhe (API + WebSocket)."""
    current_context = _context_or_default(context)
    if not require_supported_runtime("arkhe start"):
        return 1
    from rlm.cli.service import start_services

    current_context.paths.log_dir.mkdir(parents=True, exist_ok=True)
    current_context.paths.runtime_dir.mkdir(parents=True, exist_ok=True)

    return start_services(
        foreground=args.foreground,
        api_only=args.api_only,
        ws_only=args.ws_only,
        context=current_context,
    )


def cmd_stop(args: argparse.Namespace, *, context: CliContext | None = None) -> int:  # noqa: ARG001
    """Para o daemon Arkhe."""
    current_context = _context_or_default(context)
    from rlm.cli.service import stop_services

    return stop_services(context=current_context)


def cmd_status(args: argparse.Namespace, *, context: CliContext | None = None) -> int:  # noqa: ARG001
    """Exibe status atual dos processos e configuração."""
    current_context = _context_or_default(context)
    from rlm.cli.service import show_status

    return show_status(context=current_context, json_output=getattr(args, "json", False))


def cmd_update(args: argparse.Namespace, *, context: CliContext | None = None) -> int:
    """Atualiza o checkout git do Arkhe e sincroniza dependências."""
    current_context = _context_or_default(context)
    from rlm.cli.service import update_installation

    return update_installation(
        check_only=args.check,
        restart=not args.no_restart,
        target_path=getattr(args, "path", None),
        context=current_context,
    )