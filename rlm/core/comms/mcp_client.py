"""Shim de compatibilidade — implementação movida para rlm.core.integrations.mcp_client.

Mantido para não quebrar imports existentes. Use o caminho canônico:
    from rlm.core.integrations.mcp_client import BaseSyncMCPClient, SyncMCPClient, SyncMCPHttpClient
"""
from rlm.core.integrations.mcp_client import (  # noqa: F401
    BaseSyncMCPClient,
    SyncMCPClient,
    SyncMCPHttpClient,
)

__all__ = [
    "BaseSyncMCPClient",
    "SyncMCPClient",
    "SyncMCPHttpClient",
]
