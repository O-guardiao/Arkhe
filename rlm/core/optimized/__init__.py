"""Superficie publica do subsistema otimizado.

Este pacote agrega o fast-path Python do runtime:
- parsing acelerado usado pelo loop recursivo;
- tipos de request/response do LM handler;
- wire protocol JSON/socket para chamadas ao handler;
- benchmark legado para verificacao manual.

Como ``rlm.core`` importa ``optimized`` no pacote raiz, os simbolos sao
resolvidos sob demanda para evitar eager imports de socket/json backends quando
o core apenas precisa registrar o subpacote.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from rlm.core.optimized.opt_types import LMRequest, LMResponse
	from rlm.core.optimized.parsing import (
		compute_hash,
		find_code_blocks,
		find_final_answer,
		format_iteration_rs,
	)
	from rlm.core.optimized.wire import (
		JSON_BACKEND,
		MAX_FRAME_SIZE,
		json_dumps,
		json_loads,
		send_lm_request,
		send_lm_request_batched,
		socket_recv,
		socket_request,
		socket_send,
	)

_LAZY_MODULES: dict[str, str] = {
	"benchmark": "rlm.core.optimized.benchmark",
	"fast": "rlm.core.optimized.fast",
	"opt_types": "rlm.core.optimized.opt_types",
	"parsing": "rlm.core.optimized.parsing",
	"wire": "rlm.core.optimized.wire",
}

_LAZY_ATTRS: dict[str, str] = {
	"JSON_BACKEND": "rlm.core.optimized.wire",
	"MAX_FRAME_SIZE": "rlm.core.optimized.wire",
	"LMRequest": "rlm.core.optimized.opt_types",
	"LMResponse": "rlm.core.optimized.opt_types",
	"compute_hash": "rlm.core.optimized.parsing",
	"find_code_blocks": "rlm.core.optimized.parsing",
	"find_final_answer": "rlm.core.optimized.parsing",
	"format_iteration_rs": "rlm.core.optimized.parsing",
	"json_dumps": "rlm.core.optimized.wire",
	"json_loads": "rlm.core.optimized.wire",
	"send_lm_request": "rlm.core.optimized.wire",
	"send_lm_request_batched": "rlm.core.optimized.wire",
	"socket_recv": "rlm.core.optimized.wire",
	"socket_request": "rlm.core.optimized.wire",
	"socket_send": "rlm.core.optimized.wire",
}

__all__ = [
	"JSON_BACKEND",
	"MAX_FRAME_SIZE",
	"LMRequest",
	"LMResponse",
	"compute_hash",
	"find_code_blocks",
	"find_final_answer",
	"format_iteration_rs",
	"json_dumps",
	"json_loads",
	"send_lm_request",
	"send_lm_request_batched",
	"socket_recv",
	"socket_request",
	"socket_send",
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
