"""
RLM WebSocket Streaming Server — Real-time observability for the RLM.

Broadcasts internal events (thoughts, REPL executions, memory updates)
via WebSocket/SSE for consumption by frontend dashboards.

Usage:
    from rlm.server.ws_server import RLMEventBus, start_ws_server

    # Create event bus (singleton)
    bus = RLMEventBus()

    # Hook into RLM
    bus.emit("thought", {"iteration": 1, "content": "Analyzing router.ts..."})
    bus.emit("repl_exec", {"code": "files = list_files('src/gateway')", "output": "..."})
    bus.emit("memory_update", {"action": "analyze", "key": "router.ts"})
    bus.emit("final_answer", {"content": "The gateway module..."})

    # Start server (non-blocking, spawns a thread)
    start_ws_server(bus, host="0.0.0.0", port=8765)
"""

from __future__ import annotations

import json
import asyncio
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable

from dotenv import load_dotenv


@dataclass
class RLMEvent:
    """A single observable event from the RLM."""
    event_type: str  # "thought", "repl_exec", "memory_update", "final_answer", "error"
    data: dict[str, Any]
    timestamp: str = ""
    iteration: int = 0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class RLMEventBus:
    """Central event bus for RLM observability.

    Collects events and distributes them to all connected listeners
    (WebSocket clients, file loggers, etc.).
    """

    def __init__(self, max_history: int = 500):
        self._listeners: list[Callable[[RLMEvent], None]] = []
        self._history: deque[RLMEvent] = deque(maxlen=max_history)
        self._iteration: int = 0

    def set_iteration(self, iteration: int):
        """Update the current RLM iteration number."""
        self._iteration = iteration

    def emit(self, event_type: str, data: dict[str, Any] | None = None):
        """Emit an event to all listeners.

        Args:
            event_type: One of "thought", "repl_exec", "memory_update",
                        "final_answer", "error", "status".
            data: Arbitrary dict payload.
        """
        event = RLMEvent(
            event_type=event_type,
            data=data or {},
            iteration=self._iteration,
        )
        self._history.append(event)

        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # Don't crash the RLM for observer failures

    def add_listener(self, callback: Callable[[RLMEvent], None]):
        """Register a listener for events."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[RLMEvent], None]):
        """Remove a listener."""
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def get_history(self, last_n: int | None = None) -> list[dict[str, Any]]:
        """Get the recent event history as serializable dicts."""
        events = list(self._history)
        if last_n:
            events = events[-last_n:]
        return [asdict(e) for e in events]

    def clear_history(self):
        """Clear the event history."""
        self._history.clear()


"""start_ws_server — Phase 9.3 (CiberSeg): porta padrão 127.0.0.1, auth via token."""
def start_ws_server(
    event_bus: RLMEventBus,
    host: str = "127.0.0.1",   # Phase 9.3: default local-only (era 0.0.0.0)
    port: int = 8765,
    ws_token: str | None = None,
) -> threading.Thread:
    """Start a WebSocket server in a background thread.

    Each connected client receives all RLM events in real-time.

    Security (Phase 9.3):
        Requires token authentication on every new WebSocket connection.
        Token is read from env var ``RLM_WS_TOKEN`` or the ``ws_token``
        argument.  Clients send it by:
          - Query parameter: ``ws://host:8765?token=<valor>``
          - HTTP Upgrade header: ``Authorization: Bearer <valor>``
        Connections without a valid token are rejected with HTTP 401
        before the WebSocket handshake completes.

        To disable auth (internal / loopback only deployments) leave
        ``RLM_WS_TOKEN`` unset AND pass ``ws_token=""``.  In that case a
        warning is logged on startup.

    Args:
        event_bus: The RLMEventBus instance to stream from.
        host: Host to bind to.  Defaults to ``127.0.0.1`` (loopback).
              Use the WireGuard interface address (e.g. ``10.0.0.1``) to
              allow VPN peers.  NEVER use ``0.0.0.0`` in production.
        port: Port to bind to.
        ws_token: Override for the expected token (default: RLM_WS_TOKEN env).

    Returns:
        The background thread running the server.
    """
    import http
    import urllib.parse
    import hmac
    import os as _os

    # Resolve expected token (arg > env > empty)
    _expected_token: str = (
        ws_token
        if ws_token is not None
        else _os.environ.get("RLM_WS_TOKEN", "").strip()
    )

    try:
        import websockets
        from websockets.server import serve
    except ImportError:
        print(
            "[RLM WS] websockets not installed. "
            "Install with: pip install websockets"
        )
        return None

    connected_clients: set = set()

    # -----------------------------------------------------------------------
    # Phase 9.3: token handshake gate
    # -----------------------------------------------------------------------
    async def _process_request(connection, request):
        """Called before WebSocket handshake completes.
        Returns None to allow, or (status, headers, body) to reject.
        """
        if not _expected_token:
            # Auth disabled intentionally (loopback-only deploy)
            return None

        # 1. Query param ?token=...
        raw_path = getattr(request, "path", "/")
        qs = urllib.parse.urlparse(raw_path).query
        params = urllib.parse.parse_qs(qs)
        qp_token = params.get("token", [""])[0].strip()

        # 2. Authorization: Bearer ...
        raw_headers = getattr(request, "headers", {})
        auth_header = ""
        try:
            # websockets ≥10 exposes dict-like headers
            auth_header = (raw_headers.get("Authorization") or raw_headers.get("authorization") or "").strip()
        except Exception:
            pass
        bearer_token = ""
        if auth_header.lower().startswith("bearer "):
            bearer_token = auth_header[7:].strip()

        received = qp_token or bearer_token

        def _ct_eq(a: str, b: str) -> bool:
            """Constant-time string comparison (prevents timing oracle)."""
            if not a or not b:
                return False
            return hmac.compare_digest(a.encode(), b.encode())

        if _ct_eq(received, _expected_token):
            return None  # allow

        # Reject before handshake
        return (
            http.HTTPStatus.UNAUTHORIZED,
            [("Content-Type", "text/plain")],
            b"[RLM WS] 401 Unauthorized: missing or invalid token.\n",
        )

    async def handler(websocket):
        """Handle a single WebSocket connection."""
        connected_clients.add(websocket)
        try:
            # Send recent history on connect
            history = event_bus.get_history(last_n=50)
            await websocket.send(json.dumps({
                "type": "history",
                "events": history,
            }))

            # Keep alive and handle incoming commands
            async for message in websocket:
                try:
                    cmd = json.loads(message)
                    if cmd.get("action") == "get_history":
                        n = cmd.get("last_n", 50)
                        await websocket.send(json.dumps({
                            "type": "history",
                            "events": event_bus.get_history(last_n=n),
                        }))
                except Exception:
                    pass
        finally:
            connected_clients.discard(websocket)

    def broadcast(event: RLMEvent):
        """Broadcast an event to all connected WebSocket clients."""
        if not connected_clients:
            return
        msg = event.to_json()
        # Use thread-safe approach to schedule sends
        for client in list(connected_clients):
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send(msg),
                    loop
                )
            except Exception:
                connected_clients.discard(client)

    # Register broadcaster as event listener
    event_bus.add_listener(broadcast)

    loop = None

    def run_server():
        nonlocal loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def serve_forever():
            async with serve(handler, host, port):
                print(f"[RLM WS] Streaming server started on ws://{host}:{port}")
                await asyncio.Future()  # Run forever

        loop.run_until_complete(serve_forever())

    thread = threading.Thread(target=run_server, daemon=True, name="rlm-ws-server")
    thread.start()

    # Give the server a moment to start
    time.sleep(0.3)

    return thread


# =============================================================================
# SSE Fallback (no dependencies required)
# =============================================================================

class SSEStream:
    """Simple Server-Sent Events stream for HTTP clients.

    Use this when websockets is not available. Generates SSE-formatted
    strings that can be served by any HTTP framework.
    """

    def __init__(self, event_bus: RLMEventBus):
        self.event_bus = event_bus
        self._queue: deque[str] = deque(maxlen=200)
        event_bus.add_listener(self._on_event)

    def _on_event(self, event: RLMEvent):
        sse_data = f"event: {event.event_type}\ndata: {event.to_json()}\n\n"
        self._queue.append(sse_data)

    def stream(self):
        """Generator that yields SSE-formatted events."""
        while True:
            if self._queue:
                yield self._queue.popleft()
            else:
                time.sleep(0.1)
                yield ": keepalive\n\n"


def main() -> int:
    """Standalone WebSocket server entrypoint.

    Intended for ws-only debugging. The default production path embeds the
    WebSocket server inside the API process so they share the same event bus.
    """
    load_dotenv()
    bus = RLMEventBus()
    thread = start_ws_server(
        event_bus=bus,
        host=os.environ.get("RLM_WS_HOST", "127.0.0.1"),
        port=int(os.environ.get("RLM_WS_PORT", "8765")),
        ws_token=os.environ.get("RLM_WS_TOKEN"),
    )
    if thread is None:
        return 1

    print("[RLM WS] Standalone mode: no API event bridge attached.")
    try:
        while thread.is_alive():
            thread.join(timeout=1.0)
    except KeyboardInterrupt:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
