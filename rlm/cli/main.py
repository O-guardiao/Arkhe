"""Arkhe CLI — ponto de entrada principal.

Uso:
    arkhe setup        Wizard interativo de instalação
    arkhe start        Inicia o servidor Arkhe
    arkhe stop         Para o daemon/servidor Arkhe
    arkhe status       Mostra status dos processos e configuração
    arkhe token rotate Regenera todos os tokens de segurança
    arkhe peer add     Adiciona peer WireGuard
    arkhe version      Exibe versão do Arkhe

Compatibilidade:
    rlm ...            Alias legado ainda suportado
"""

from __future__ import annotations

import sys
from rlm.cli.commands.version import cmd_version
from rlm.cli.command_specs import get_command_specs
from rlm.cli.context import CliContext
from rlm.cli.dispatch import build_dispatch_tables, dispatch_nested_command
from rlm.cli.parser import build_parser
from rlm.cli.context import print_error as _print_error

COMMAND_SPECS = get_command_specs()
DISPATCH, NESTED_DISPATCH = build_dispatch_tables(COMMAND_SPECS)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser(COMMAND_SPECS)
    args = parser.parse_args(argv)
    context = CliContext.from_environment()

    if getattr(args, "version", False):
        sys.exit(cmd_version(type("Args", (), {})(), context=context))

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    nested_result = dispatch_nested_command(
        args,
        parser=parser,
        context=context,
        table=NESTED_DISPATCH,
    )
    if nested_result is not None:
        sys.exit(nested_result)

    handler = DISPATCH.get(args.command)
    if handler is None:
        _print_error(f"Comando desconhecido: '{args.command}'")
        sys.exit(1)

    sys.exit(handler(args, context=context))


if __name__ == "__main__":
    main()
