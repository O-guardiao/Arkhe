"""Shim de compatibilidade — implementação movida para rlm.core.engine.comms_utils.

Mantido para não quebrar imports existentes. Use o caminho canônico:
    from rlm.core.engine.comms_utils import socket_send, socket_recv, LMRequest, LMResponse
"""
from rlm.core.engine.comms_utils import (  # noqa: F401
    LMRequest,
    LMResponse,
    socket_send,
    socket_recv,
    socket_request,
    send_lm_request,
    send_lm_request_batched,
)

__all__ = [
    "LMRequest",
    "LMResponse",
    "socket_send",
    "socket_recv",
    "socket_request",
    "send_lm_request",
    "send_lm_request_batched",
]
