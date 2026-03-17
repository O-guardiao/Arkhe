from __future__ import annotations

import argparse

from rlm.cli.command_specs import CLI_DESCRIPTION, CLI_EPILOG, CommandSpec, get_command_specs

def _apply_argument_spec(parser: argparse.ArgumentParser, command: CommandSpec) -> None:
    for argument in command.arguments:
        parser.add_argument(*argument.flags, **argument.kwargs)


def _add_command_spec(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], command: CommandSpec) -> None:
    parser = subparsers.add_parser(
        command.name,
        help=command.help,
        aliases=list(command.aliases),
        description=command.description or command.help,
        epilog=command.epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _apply_argument_spec(parser, command)
    if command.subcommands:
        nested_subparsers = parser.add_subparsers(dest=f"{command.name}_command", metavar=command.metavar or "<ação>")
        for subcommand in command.subcommands:
            _add_command_spec(nested_subparsers, subcommand)


def build_parser(commands: tuple[CommandSpec, ...] | None = None) -> argparse.ArgumentParser:
    prog_name = "arkhe"
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description=CLI_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLI_EPILOG,
    )
    parser.add_argument("--version", action="store_true", help="Exibe versão do Arkhe e encerra")

    sub = parser.add_subparsers(dest="command", metavar="<comando>")

    for command in commands or get_command_specs():
        _add_command_spec(sub, command)

    return parser