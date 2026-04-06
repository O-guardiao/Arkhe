"""
Camada de ferramentas do RLM.

Expõe as três classes principais e o singleton de registro.
"""

from __future__ import annotations

from rlm.core.tools.specs import PermissionMode, ToolLayer, ToolSpec
from rlm.core.tools.registry import ToolRegistry, get_registry, reset_registry_for_testing
from rlm.core.tools.dispatcher import DispatchResult, ToolDispatcher

__all__ = [
    # specs
    "PermissionMode",
    "ToolLayer",
    "ToolSpec",
    # registry
    "ToolRegistry",
    "get_registry",
    "reset_registry_for_testing",
    # dispatcher
    "DispatchResult",
    "ToolDispatcher",
]
