from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import importlib
import json
import os
import threading
from typing import Any, Dict, List, Optional


class BaseSyncMCPClient:
    """
    Common lifecycle for sync wrappers over async MCP SDK transports.

    Concrete transports implement `_open_transport()` and this base class owns
    the event loop, background thread and session lifecycle.
    """

    transport_name = "unknown"

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None
        self._ready_event = threading.Event()
        self._error: BaseException | None = None
        self._stop_future: asyncio.Future[Any] | None = None
        self._lifecycle_lock = threading.RLock()

        self._start_worker()

    def _start_worker(self) -> None:
        self._ready_event = threading.Event()
        self._error = None
        self._stop_future = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        if self._loop is None:
            return
        asyncio.set_event_loop(self._loop)
        self._stop_future = self._loop.create_future()
        try:
            self._loop.run_until_complete(self._connect_and_serve())
        finally:
            self._loop.close()

    async def _open_transport(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        raise NotImplementedError

    async def _connect_and_serve(self) -> None:
        stack: AsyncExitStack | None = None
        try:
            stack = AsyncExitStack()
            read, write = await self._open_transport(stack)

            session_mod = importlib.import_module("mcp.client.session")
            session_cls = getattr(session_mod, "ClientSession")
            self._session = await stack.enter_async_context(session_cls(read, write))
            await self._session.initialize()
            self._ready_event.set()

            if self._stop_future is not None:
                await self._stop_future
            else:
                await asyncio.Future()
        except Exception as exc:
            self._error = exc
            self._ready_event.set()
        finally:
            if stack is not None:
                await stack.aclose()
            self._session = None

    def _run_coro_sync(self, coro: Any) -> Any:
        if self._loop is None:
            raise RuntimeError("MCP event loop is not available")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def _thread_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _needs_reconnect(self) -> bool:
        return self._session is None or not self._thread_alive() or self._error is not None

    def _should_retry_after_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        recoverable_names = {
            "brokenpipeerror",
            "connectionreseterror",
            "runtimeerror",
            "cancellederror",
        }
        return (
            exc.__class__.__name__.lower() in recoverable_names
            or "broken pipe" in message
            or "closed" in message
            or "event loop" in message
            or not self.is_connected()
        )

    def reconnect(self, timeout: float = 15.0) -> None:
        with self._lifecycle_lock:
            self.close()
            self._start_worker()
        self.connect(timeout=timeout)

    def _with_retry(self, operation: Any) -> Any:
        self.connect()
        try:
            return operation()
        except Exception as exc:
            if not self._should_retry_after_error(exc):
                raise
            self.reconnect()
            return operation()

    def connect(self, timeout: float = 15.0) -> None:
        with self._lifecycle_lock:
            if self._needs_reconnect():
                if self._thread_alive() and self._session is not None and self._error is None:
                    return
                if not self._thread_alive() or self._loop is None:
                    self._start_worker()

        self._ready_event.wait(timeout)
        if self._error:
            raise RuntimeError(
                f"Failed to connect MCP transport {self.transport_name}: {self._error}"
            )
        if self._session is None:
            raise TimeoutError(f"Timeout connecting MCP transport {self.transport_name}")

    def list_tools(self) -> List[Dict[str, Any]]:
        def _op() -> List[Dict[str, Any]]:
            if self._session is None:
                return []

            result = self._run_coro_sync(self._session.list_tools())
            tools_list = []
            if result and hasattr(result, "tools"):
                for tool in result.tools:
                    tools_list.append(
                        {
                            "name": tool.name,
                            "description": getattr(tool, "description", ""),
                            "inputSchema": getattr(tool, "inputSchema", {}),
                        }
                    )
            return tools_list

        return self._with_retry(_op)

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        args_payload = arguments or {}

        def _op() -> str:
            if self._session is None:
                return f"Unexpected error calling tool {name}: session not connected"

            result = self._run_coro_sync(self._session.call_tool(name, args_payload))

            out = []
            if result and hasattr(result, "content"):
                for content in result.content:
                    if hasattr(content, "type") and content.type == "text":
                        out.append(content.text)
                    elif hasattr(content, "text"):
                        out.append(str(content.text))
                    else:
                        out.append(str(content))
            return "\n".join(out)

        tools_list = []
        try:
            return self._with_retry(_op)
        except Exception as exc:
            if exc.__class__.__name__ == "McpError":
                return f"Error MCP calling tool {name}: {str(exc)}"
            return f"Unexpected error calling tool {name}: {str(exc)}"

    def health_check(self) -> dict[str, Any]:
        try:
            tools = self.list_tools()
            return {
                "ok": True,
                "transport": self.transport_name,
                "tool_count": len(tools),
                "connected": self.is_connected(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "transport": self.transport_name,
                "connected": self.is_connected(),
                "error": str(exc),
            }

    def is_connected(self) -> bool:
        return self._session is not None and self._thread_alive()

    def close(self) -> None:
        loop = self._loop
        thread = self._thread
        stop_future = self._stop_future
        if loop is not None and loop.is_running() and stop_future and not stop_future.done():
            loop.call_soon_threadsafe(stop_future.set_result, True)
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._session = None
        self._loop = None
        self._thread = None
        self._stop_future = None


class SyncMCPClient(BaseSyncMCPClient):
    """Synchronous wrapper for stdio-based MCP servers."""

    transport_name = "stdio"

    def __init__(self, command: str, args: List[str], env: Optional[Dict[str, str]] = None):
        self.command = command
        self.args = args
        self.env = env or os.environ.copy()
        super().__init__()

    async def _open_transport(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        stdio_mod = importlib.import_module("mcp.client.stdio")
        stdio_client = getattr(stdio_mod, "stdio_client")
        server_params_cls = getattr(stdio_mod, "StdioServerParameters")
        server_params = server_params_cls(command=self.command, args=self.args, env=self.env)
        return await stack.enter_async_context(stdio_client(server_params))


class SyncMCPHttpClient(BaseSyncMCPClient):
    """
    Optional HTTP/SSE MCP client.

    The MCP SDK changed HTTP transport names over time, so the import is
    resolved dynamically with a fallback chain.
    """

    transport_name = "http"

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None):
        self.url = url
        self.headers = headers or {}
        super().__init__()

    async def _open_transport(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        factories = [
            ("mcp.client.sse", "sse_client"),
            ("mcp.client.streamable_http", "streamablehttp_client"),
            ("mcp.client.streamable_http", "streamable_http_client"),
        ]
        errors: list[str] = []
        for module_name, factory_name in factories:
            try:
                module = importlib.import_module(module_name)
                factory = getattr(module, factory_name)
                try:
                    return await stack.enter_async_context(factory(self.url, headers=self.headers))
                except TypeError:
                    return await stack.enter_async_context(factory(self.url))
            except Exception as exc:
                errors.append(f"{module_name}.{factory_name}: {exc}")

        raise RuntimeError(
            "HTTP/SSE MCP transport unavailable in installed SDK. "
            f"Attempted: {'; '.join(errors)}"
        )
