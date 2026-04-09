"""Superfície pública do motor recursivo.

Este pacote agrega os blocos centrais do runtime recursivo:
- orquestração principal via ``RLM``;
- transporte LM/socket via ``LMHandler`` e ``comms_utils``;
- hooks, políticas de permissão e diário de sessão;
- identidade/runtime workbench e factories de ``sub_rlm``.

Os símbolos são resolvidos sob demanda para evitar importar toda a árvore do
motor quando ``rlm.core`` é carregado apenas para descoberta, patching ou
inspeção de tipos.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from rlm.core.engine.comms_utils import (
		LMRequest,
		LMResponse,
		send_lm_request,
		send_lm_request_batched,
	)
	from rlm.core.engine.enums import PermissionMode
	from rlm.core.engine.hooks import HookEvent, HookSystem
	from rlm.core.engine.lm_handler import LMHandler
	from rlm.core.engine.permission_policy import PermissionPolicy, PolicyAction, PolicyRule
	from rlm.core.engine.rlm import RLM
	from rlm.core.engine.runtime_workbench import AgentContext, TaskEntry, TaskLedger
	from rlm.core.engine.session_journal import JournalEntry, Role, SessionJournal
	from rlm.core.engine.sub_rlm import (
		AsyncHandle,
		SubRLMArtifactResult,
		SubRLMCallable,
		SubRLMDepthError,
		SubRLMError,
		SubRLMParallelCallable,
		SubRLMParallelDetailedResults,
		SubRLMParallelTaskResult,
		SubRLMResult,
		SubRLMTimeoutError,
		make_sub_rlm_async_fn,
		make_sub_rlm_fn,
		make_sub_rlm_parallel_fn,
	)

	socket_recv: Callable[..., dict[str, object]]
	socket_request: Callable[..., dict[str, object]]
	socket_send: Callable[..., None]

_LAZY_MODULES: dict[str, str] = {
	"comms_utils": "rlm.core.engine.comms_utils",
	"compaction": "rlm.core.engine.compaction",
	"control_flow": "rlm.core.engine.control_flow",
	"enums": "rlm.core.engine.enums",
	"hooks": "rlm.core.engine.hooks",
	"lm_handler": "rlm.core.engine.lm_handler",
	"loop_detector": "rlm.core.engine.loop_detector",
	"permission_policy": "rlm.core.engine.permission_policy",
	"rlm": "rlm.core.engine.rlm",
	"rlm_context_mixin": "rlm.core.engine.rlm_context_mixin",
	"rlm_loop_mixin": "rlm.core.engine.rlm_loop_mixin",
	"rlm_mcts_mixin": "rlm.core.engine.rlm_mcts_mixin",
	"rlm_persistence_mixin": "rlm.core.engine.rlm_persistence_mixin",
	"runtime_workbench": "rlm.core.engine.runtime_workbench",
	"session_journal": "rlm.core.engine.session_journal",
	"sub_rlm": "rlm.core.engine.sub_rlm",
}

_LAZY_ATTRS: dict[str, str] = {
	"AgentContext": "rlm.core.engine.runtime_workbench",
	"AsyncHandle": "rlm.core.engine.sub_rlm",
	"HookEvent": "rlm.core.engine.hooks",
	"HookSystem": "rlm.core.engine.hooks",
	"JournalEntry": "rlm.core.engine.session_journal",
	"LMHandler": "rlm.core.engine.lm_handler",
	"LMRequest": "rlm.core.engine.comms_utils",
	"LMResponse": "rlm.core.engine.comms_utils",
	"PermissionMode": "rlm.core.engine.enums",
	"PermissionPolicy": "rlm.core.engine.permission_policy",
	"PolicyAction": "rlm.core.engine.permission_policy",
	"PolicyRule": "rlm.core.engine.permission_policy",
	"RLM": "rlm.core.engine.rlm",
	"Role": "rlm.core.engine.session_journal",
	"SessionJournal": "rlm.core.engine.session_journal",
	"SubRLMArtifactResult": "rlm.core.engine.sub_rlm",
	"SubRLMCallable": "rlm.core.engine.sub_rlm",
	"SubRLMDepthError": "rlm.core.engine.sub_rlm",
	"SubRLMError": "rlm.core.engine.sub_rlm",
	"SubRLMParallelCallable": "rlm.core.engine.sub_rlm",
	"SubRLMParallelDetailedResults": "rlm.core.engine.sub_rlm",
	"SubRLMParallelTaskResult": "rlm.core.engine.sub_rlm",
	"SubRLMResult": "rlm.core.engine.sub_rlm",
	"SubRLMTimeoutError": "rlm.core.engine.sub_rlm",
	"TaskEntry": "rlm.core.engine.runtime_workbench",
	"TaskLedger": "rlm.core.engine.runtime_workbench",
	"make_sub_rlm_async_fn": "rlm.core.engine.sub_rlm",
	"make_sub_rlm_fn": "rlm.core.engine.sub_rlm",
	"make_sub_rlm_parallel_fn": "rlm.core.engine.sub_rlm",
	"send_lm_request": "rlm.core.engine.comms_utils",
	"send_lm_request_batched": "rlm.core.engine.comms_utils",
	"socket_recv": "rlm.core.engine.comms_utils",
	"socket_request": "rlm.core.engine.comms_utils",
	"socket_send": "rlm.core.engine.comms_utils",
}

__all__ = [
	"RLM",
	"LMHandler",
	"LMRequest",
	"LMResponse",
	"send_lm_request",
	"send_lm_request_batched",
	"socket_recv",
	"socket_request",
	"socket_send",
	"HookEvent",
	"HookSystem",
	"PermissionMode",
	"PermissionPolicy",
	"PolicyAction",
	"PolicyRule",
	"SessionJournal",
	"JournalEntry",
	"Role",
	"AgentContext",
	"TaskEntry",
	"TaskLedger",
	"AsyncHandle",
	"SubRLMArtifactResult",
	"SubRLMCallable",
	"SubRLMDepthError",
	"SubRLMError",
	"SubRLMParallelCallable",
	"SubRLMParallelDetailedResults",
	"SubRLMParallelTaskResult",
	"SubRLMResult",
	"SubRLMTimeoutError",
	"make_sub_rlm_async_fn",
	"make_sub_rlm_fn",
	"make_sub_rlm_parallel_fn",
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
	return sorted(set(globals()) | set(__all__) | set(_LAZY_MODULES))
