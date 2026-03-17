from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping

from rlm.cli.command_specs import CommandSpec, get_command_specs
from rlm.cli.context import CliContext

CommandHandler = Callable[..., int]
NestedDispatch = Mapping[str, Mapping[str, CommandHandler]]


def build_dispatch_tables(commands: tuple[CommandSpec, ...] | None = None) -> tuple[dict[str, CommandHandler], dict[str, dict[str, CommandHandler]]]:
    root_dispatch: dict[str, CommandHandler] = {}
    nested_dispatch: dict[str, dict[str, CommandHandler]] = {}

    for command in commands or get_command_specs():
        if command.subcommands:
            nested_dispatch[command.name] = {}
            for subcommand in command.subcommands:
                if subcommand.handler is not None:
                    nested_dispatch[command.name][subcommand.name] = subcommand.handler
                    for alias in subcommand.aliases:
                        nested_dispatch[command.name][alias] = subcommand.handler
        elif command.handler is not None:
            root_dispatch[command.name] = command.handler
            for alias in command.aliases:
                root_dispatch[alias] = command.handler

    return root_dispatch, nested_dispatch


def dispatch_nested_command(
    args: argparse.Namespace,
    *,
    parser: argparse.ArgumentParser,
    context: CliContext,
    table: NestedDispatch,
) -> int | None:
    command_name = getattr(args, "command", None)
    if command_name not in table:
        return None

    nested_name = getattr(args, f"{command_name}_command", None)
    if not nested_name:
        parser.parse_args([command_name, "--help"])
        return 0

    handler = table[command_name].get(nested_name)
    if handler is None:
        parser.parse_args([command_name, "--help"])
        return 0

    return handler(args, context=context)