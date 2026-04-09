"""Shim de compatibilidade — implementação movida para rlm.core.orchestration.sibling_bus.

Mantido para não quebrar imports existentes. Use o caminho canônico:
    from rlm.core.orchestration.sibling_bus import SiblingBus, SiblingBusError
"""
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
