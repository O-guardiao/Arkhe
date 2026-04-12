"""Shim backward compat -- canonico em `rlm.server.operator_bridge`.

.. deprecated:: Movido para `rlm.server.operator_bridge`.
"""
from rlm.server.operator_bridge import (  # noqa: layer  # noqa: F401
    TuiAdapter,
    router,
    OperatorSessionRequest,
    OperatorMessageRequest,
    OperatorCommandRequest,
    operator_attach_session,
    operator_session_activity,
    operator_session_message,
    operator_session_command,
)

__all__ = [
    "TuiAdapter",
    "router",
    "OperatorSessionRequest",
    "OperatorMessageRequest",
    "OperatorCommandRequest",
]
