"""Superfície pública lazy para comandos do CLI Arkhe.

Responsabilidades deste pacote:
- expor handlers ``cmd_*`` usados por testes e integrações;
- reexportar a API pública do workbench TUI;
- manter os módulos reais carregados sob demanda, sem importar toda a árvore
  de comandos no import do pacote.

O pacote não executa lógica de negócio diretamente. A implementação continua
nos submódulos (``ops``, ``doctor``, ``client``, ``workbench`` etc.).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	import rlm.cli.commands.channel as channel
	import rlm.cli.commands.client as client
	import rlm.cli.commands.doctor as doctor
	import rlm.cli.commands.ops as ops
	import rlm.cli.commands.ops as service
	import rlm.cli.commands.peer as peer
	import rlm.cli.commands.setup as setup
	import rlm.cli.commands.skill as skill
	import rlm.cli.commands.token as token
	import rlm.cli.commands.tui as tui
	import rlm.cli.commands.version as version
	import rlm.cli.commands.workbench as workbench
	from rlm.cli.commands.channel import cmd_channel_list, cmd_channel_probe, cmd_channel_status
	from rlm.cli.commands.client import (
		cmd_client_add,
		cmd_client_list,
		cmd_client_revoke,
		cmd_client_status,
	)
	from rlm.cli.commands.doctor import cmd_doctor
	from rlm.cli.commands.ops import cmd_start, cmd_status, cmd_stop, cmd_update
	from rlm.cli.commands.peer import cmd_peer_add
	from rlm.cli.commands.setup import cmd_setup
	from rlm.cli.commands.skill import cmd_skill_install, cmd_skill_list
	from rlm.cli.commands.token import cmd_token_rotate
	from rlm.cli.commands.tui import cmd_tui
	from rlm.cli.commands.version import cmd_version
	from rlm.cli.commands.workbench import LiveSession, RuntimeWorkbench, run_workbench

_LAZY_MODULES: dict[str, str] = {
	"channel": "rlm.cli.commands.channel",
	"client": "rlm.cli.commands.client",
	"doctor": "rlm.cli.commands.doctor",
	"ops": "rlm.cli.commands.ops",
	"peer": "rlm.cli.commands.peer",
	"service": "rlm.cli.commands.ops",
	"setup": "rlm.cli.commands.setup",
	"skill": "rlm.cli.commands.skill",
	"token": "rlm.cli.commands.token",
	"tui": "rlm.cli.commands.tui",
	"version": "rlm.cli.commands.version",
	"workbench": "rlm.cli.commands.workbench",
}

_LAZY_ATTRS: dict[str, str] = {
	"cmd_channel_list": "rlm.cli.commands.channel",
	"cmd_channel_probe": "rlm.cli.commands.channel",
	"cmd_channel_status": "rlm.cli.commands.channel",
	"cmd_client_add": "rlm.cli.commands.client",
	"cmd_client_list": "rlm.cli.commands.client",
	"cmd_client_revoke": "rlm.cli.commands.client",
	"cmd_client_status": "rlm.cli.commands.client",
	"cmd_doctor": "rlm.cli.commands.doctor",
	"cmd_peer_add": "rlm.cli.commands.peer",
	"cmd_setup": "rlm.cli.commands.setup",
	"cmd_skill_install": "rlm.cli.commands.skill",
	"cmd_skill_list": "rlm.cli.commands.skill",
	"cmd_start": "rlm.cli.commands.ops",
	"cmd_status": "rlm.cli.commands.ops",
	"cmd_stop": "rlm.cli.commands.ops",
	"cmd_token_rotate": "rlm.cli.commands.token",
	"cmd_tui": "rlm.cli.commands.tui",
	"cmd_update": "rlm.cli.commands.ops",
	"cmd_version": "rlm.cli.commands.version",
	"LiveSession": "rlm.cli.commands.workbench",
	"RuntimeWorkbench": "rlm.cli.commands.workbench",
	"run_workbench": "rlm.cli.commands.workbench",
}

__all__ = [
	"channel",
	"client",
	"doctor",
	"ops",
	"peer",
	"service",
	"setup",
	"skill",
	"token",
	"tui",
	"version",
	"workbench",
	"cmd_channel_list",
	"cmd_channel_probe",
	"cmd_channel_status",
	"cmd_client_add",
	"cmd_client_list",
	"cmd_client_revoke",
	"cmd_client_status",
	"cmd_doctor",
	"cmd_peer_add",
	"cmd_setup",
	"cmd_skill_install",
	"cmd_skill_list",
	"cmd_start",
	"cmd_status",
	"cmd_stop",
	"cmd_token_rotate",
	"cmd_tui",
	"cmd_update",
	"cmd_version",
	"LiveSession",
	"RuntimeWorkbench",
	"run_workbench",
]


def __getattr__(name: str):
	if name in _LAZY_MODULES:
		module = importlib.import_module(_LAZY_MODULES[name])
		globals()[name] = module
		return module
	if name in _LAZY_ATTRS:
		module = importlib.import_module(_LAZY_ATTRS[name])
		value = getattr(module, name)
		globals()[name] = value
		return value
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
	return sorted(set(globals()) | set(__all__))