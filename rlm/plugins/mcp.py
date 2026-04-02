from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from rlm.core.comms.mcp_client import BaseSyncMCPClient

# Maintain global registry of active clients
_active_clients: dict[str, "MCPServerNamespace"] = {}

class MCPToolWrapper:
    """Wraps an MCP tool into a standard Python callable."""
    def __init__(self, client: "BaseSyncMCPClient", tool_name: str, tool_desc: str):
        self._client = client
        self._tool_name = tool_name
        self.__name__ = tool_name.replace("-", "_")
        self.__doc__ = tool_desc

    def __call__(self, **kwargs):
        return self._client.call_tool(self._tool_name, kwargs)
        
    def __repr__(self):
        return f"<MCP Tool: {self._tool_name}>"

class MCPServerNamespace:
    """
    A namespace object that dynamically exposes MCP tools as methods.
    """
    def __init__(self, client: "BaseSyncMCPClient", name: str, cache_key: str = ""):
        self._client = client
        self._name = name
        self._cache_key = cache_key or name
        self._setup_tools()
        
    def _setup_tools(self):
        tools = self._client.list_tools()
        for t in tools:
            name = t["name"]
            safe_name = name.replace("-", "_")
            desc = t.get("description", "")
            wrapper = MCPToolWrapper(self._client, name, desc)
            setattr(self, safe_name, wrapper)

    def list_tools(self):
        """Returns the raw list of dictionaries defining the tools."""
        return self._client.list_tools()

    def close(self):
        self._client.close()

    @property
    def cache_key(self) -> str:
        return self._cache_key
        
    def __repr__(self):
        tools = [t for t in dir(self) if not t.startswith("_") and t not in ("close", "list_tools")]
        return f"<MCPServerNamespace '{self._name}' containing tools: {', '.join(tools)}>"


def _infer_scope_key(scope_key: str = "") -> str:
    if scope_key:
        return scope_key
    try:
        from rlm.core.skillkit.skill_telemetry import get_skill_telemetry

        ctx = get_skill_telemetry().current_context()
        return ctx.get("session_id") or ctx.get("client_id") or ""
    except Exception:
        return ""


def _build_cache_key(server_name: str, scope_key: str = "") -> str:
    effective_scope = _infer_scope_key(scope_key)
    return f"{server_name}::{effective_scope}" if effective_scope else server_name


def load_server(
    server_name: str,
    command: str,
    args: List[str],
    *,
    env: Optional[Dict[str, str]] = None,
    scope_key: str = "",
    headers: Optional[Dict[str, str]] = None,
    transport: str = "auto",
) -> MCPServerNamespace:
    """
    Spawns an MCP server and returns an object whose methods map to the server's tools.
    
    Example:
        sqlite = load_server("sqlite", "npx", ["-y", "@modelcontextprotocol/server-sqlite", "--db", "test.db"])
        results = sqlite.query_db(query="SELECT * FROM users")
    """
    cache_key = _build_cache_key(server_name, scope_key)
    existing = _active_clients.get(cache_key)
    if existing is not None:
        return existing

    from rlm.core.comms.mcp_client import SyncMCPClient, SyncMCPHttpClient

    resolved_transport = transport
    if resolved_transport == "auto":
        resolved_transport = "http" if command.startswith(("http://", "https://")) else "stdio"

    if resolved_transport == "http":
        client = SyncMCPHttpClient(command, headers=headers)
    else:
        client = SyncMCPClient(command, args, env=env)
    
    # Wait for connection to ensure it's healthy
    try:
        client.connect()
    except Exception as e:
        client.close()
        raise RuntimeError(f"Could not connect to MCP server '{server_name}': {e}")
    
    namespace = MCPServerNamespace(client, server_name, cache_key=cache_key)
    _active_clients[cache_key] = namespace
    return namespace


def close_cache_key(cache_key: str) -> bool:
    namespace = _active_clients.pop(cache_key, None)
    if namespace is None:
        return False
    namespace.close()
    return True


def close_scope(scope_key: str) -> int:
    effective_scope = _infer_scope_key(scope_key)
    if not effective_scope:
        return 0

    suffix = f"::{effective_scope}"
    closed = 0
    for cache_key in list(_active_clients.keys()):
        if cache_key.endswith(suffix):
            close_cache_key(cache_key)
            closed += 1
    return closed


def close_all_servers() -> None:
    for key, namespace in list(_active_clients.items()):
        try:
            namespace.close()
        finally:
            _active_clients.pop(key, None)
