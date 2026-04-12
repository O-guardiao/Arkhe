"""Shim de compatibilidade — implementação movida para rlm.core.orchestration.sibling_bus.

.. deprecated::
    Use o caminho canônico:
        from rlm.core.orchestration.sibling_bus import SiblingBus, SiblingBusError
    Este shim será removido na próxima versão minor.
"""
import warnings as _warnings

_warnings.warn(
    "rlm.core.comms.sibling_bus é um shim deprecated. "
    "Importe de rlm.core.orchestration.sibling_bus.",
    DeprecationWarning,
    stacklevel=2,
)

from rlm.core.orchestration.sibling_bus import (  # noqa: F401
    SiblingBus,
    SiblingBusError,
    SiblingMessage,
    ControlChannel,
    VALID_SIGNAL_TYPES,
    SIGNAL_TOPIC_MAP,
    _MAX_CHANNELS,
    _CHANNEL_MAXSIZE,
    _MAX_PAYLOAD_BYTES,
)

__all__ = [
    "SiblingBus",
    "SiblingBusError",
    "SiblingMessage",
    "ControlChannel",
    "VALID_SIGNAL_TYPES",
    "SIGNAL_TOPIC_MAP",
    "_MAX_CHANNELS",
    "_CHANNEL_MAXSIZE",
    "_MAX_PAYLOAD_BYTES",
]
