"""Subsistema canônico de gateways Python.

Centraliza adapters de canal, protocolos de transporte e montagem de rotas
antes espalhados em ``rlm.server``.

Os símbolos públicos são resolvidos sob demanda para evitar que imports leves,
como ``rlm.gateway.telegram_gateway``, fiquem bloqueados por dependências não
necessárias do pacote raiz como validação de schema.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rlm.gateway.auth_helpers import build_internal_auth_headers
    from rlm.gateway.backoff import GATEWAY_RECONNECT, compute_backoff, sleep_sync
    from rlm.gateway.envelope import Envelope

_LAZY_ATTRS: dict[str, str] = {
    "Envelope": "rlm.gateway.envelope",
    "build_internal_auth_headers": "rlm.gateway.auth_helpers",
    "compute_backoff": "rlm.gateway.backoff",
    "sleep_sync": "rlm.gateway.backoff",
    "GATEWAY_RECONNECT": "rlm.gateway.backoff",
}

__all__ = [
    "Envelope",
    "build_internal_auth_headers",
    "compute_backoff",
    "sleep_sync",
    "GATEWAY_RECONNECT",
]


def __getattr__(name: str):
    if name in _LAZY_ATTRS:
        module = importlib.import_module(_LAZY_ATTRS[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
