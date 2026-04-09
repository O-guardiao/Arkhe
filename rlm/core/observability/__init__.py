"""Superfície pública do subsistema de observabilidade.

Este pacote agrega dois contratos do runtime:
- telemetria por turno para auditoria/custo em JSONL append-only;
- surface operacional para snapshot de runtime e comandos do operador.

Os símbolos são resolvidos sob demanda para evitar eager imports do core em
componentes de UI, gateway e telemetria quando apenas ``rlm.core`` é carregado.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from rlm.core.observability.operator_surface import (
		apply_operator_command,
		build_activity_payload,
		build_runtime_snapshot,
		dispatch_operator_prompt,
	)
	from rlm.core.observability.turn_telemetry import TurnTelemetry, TurnTelemetryStore

_LAZY_MODULES: dict[str, str] = {
	"operator_surface": "rlm.core.observability.operator_surface",
	"turn_telemetry": "rlm.core.observability.turn_telemetry",
}

_LAZY_ATTRS: dict[str, str] = {
	"TurnTelemetry": "rlm.core.observability.turn_telemetry",
	"TurnTelemetryStore": "rlm.core.observability.turn_telemetry",
	"build_runtime_snapshot": "rlm.core.observability.operator_surface",
	"build_activity_payload": "rlm.core.observability.operator_surface",
	"apply_operator_command": "rlm.core.observability.operator_surface",
	"dispatch_operator_prompt": "rlm.core.observability.operator_surface",
}

__all__ = [
	"TurnTelemetry",
	"TurnTelemetryStore",
	"build_runtime_snapshot",
	"build_activity_payload",
	"apply_operator_command",
	"dispatch_operator_prompt",
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
