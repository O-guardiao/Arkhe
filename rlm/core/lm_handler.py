"""
LMHandler - Routes LLM requests from the RLM process and environment subprocesses.

Uses a multi-threaded socket server. Protocol: 4-byte length prefix + JSON payload.

Architecture note:
    The Python ThreadingLMServer is the production handler. The Rust handler
    (rlm_rust/src/handler.rs) is reserved for future local-LLM / low-latency
    scenarios (see its module docstring for activation criteria).
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from socketserver import StreamRequestHandler, ThreadingTCPServer
from threading import Thread, Lock
from typing import cast

from rlm.clients.base_lm import BaseLM
from rlm.core.fast import LMRequest, LMResponse, socket_recv, socket_send
from rlm.core.types import RLMChatCompletion, UsageSummary
from rlm.core.control_flow import SyncLimiter
from rlm.logging import get_runtime_logger

logger = get_runtime_logger("lm_handler")


class LMRequestHandler(StreamRequestHandler):
    """Socket handler for LLM completion requests."""

    def handle(self):
        """Serve requests on this connection until the client disconnects.

        Supports persistent connections: the client may send multiple
        request-response pairs on the same socket without reconnecting.
        The 4-byte length-prefix framing already delimits each message.
        """
        server = cast("ThreadingLMServer", self.server)
        handler: LMHandler = server.lm_handler  # type: ignore[attr-defined]
        try:
            while True:
                request_data = socket_recv(self.connection)
                if request_data is None:
                    break  # Client closed connection cleanly (EOF)

                if not isinstance(request_data, dict):
                    try:
                        socket_send(
                            self.connection,
                            LMResponse.error_response("Request must be a JSON object").to_dict(),
                        )
                    except Exception:
                        break
                    continue

                try:
                    request = LMRequest.from_dict(request_data)
                    if request.is_batched:
                        response = self._handle_batched(request, handler)
                    elif request.prompt:
                        response = self._handle_single(request, handler)
                    else:
                        response = LMResponse.error_response(
                            "Missing 'prompt' or 'prompts' in request."
                        )
                    socket_send(self.connection, response.to_dict())
                    server.record_request()
                except Exception as e:
                    server.record_request(error=True)
                    try:
                        socket_send(
                            self.connection,
                            LMResponse.error_response(str(e)).to_dict(),
                        )
                    except Exception:
                        break
        except Exception:
            pass

    def _handle_single(self, request: LMRequest, handler: "LMHandler") -> LMResponse:
        """Handle a single prompt request."""
        assert request.prompt is not None  # garantido pelo caller (elif request.prompt)
        client = handler.get_client(request.model, request.depth)

        start_time = time.perf_counter()
        content = client.completion(request.prompt)
        end_time = time.perf_counter()

        model_usage = client.get_last_usage()
        root_model = request.model or client.model_name
        usage_summary = UsageSummary(model_usage_summaries={root_model: model_usage})
        return LMResponse.success_response(
            chat_completion=RLMChatCompletion(
                root_model=root_model,
                prompt=request.prompt,
                response=content,
                usage_summary=usage_summary,
                execution_time=end_time - start_time,
            )
        )

    def _handle_batched(self, request: LMRequest, handler: "LMHandler") -> LMResponse:
        """Handle a batched prompts request using async for concurrency."""
        assert request.prompts is not None  # garantido pelo caller (if request.is_batched)
        prompts = request.prompts
        client = handler.get_client(request.model, request.depth)

        start_time = time.perf_counter()

        async def run_all():
            tasks = [client.acompletion(prompt) for prompt in prompts]
            return await asyncio.gather(*tasks)

        server = cast("ThreadingLMServer", self.server)
        results = server.run_async(run_all())
        end_time = time.perf_counter()

        total_time = end_time - start_time
        model_usage = client.get_last_usage()
        root_model = request.model or client.model_name
        usage_summary = UsageSummary(model_usage_summaries={root_model: model_usage})

        chat_completions = [
            RLMChatCompletion(
                root_model=root_model,
                prompt=prompt,
                response=content,
                usage_summary=usage_summary,
                execution_time=total_time / len(prompts),  # approximate per-prompt time
            )
            for prompt, content in zip(prompts, results, strict=True)
        ]

        return LMResponse.batched_success_response(chat_completions=chat_completions)


class ThreadingLMServer(ThreadingTCPServer):
    """Production TCP server for LM requests.

    Improvements over stdlib ThreadingTCPServer:
    - Persistent asyncio event loop (eliminates ~2ms asyncio.run() per batch)
    - Persistent client connections (pool-friendly, no reconnect per request)
    - Connection tracking with configurable limit (prevents resource exhaustion)
    - Thread-pool executor for bounded concurrency
    - Request metrics (count, active, errors) for observability
    - Graceful shutdown draining active connections

    For local LLM / low-latency API scenarios (< 50ms response, 100+ envs),
    consider activating the Rust Tokio handler in rlm_rust/src/handler.rs.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, max_connections: int = 128, max_workers: int = 32, **kwargs):
        super().__init__(*args, **kwargs)

        # --- Persistent async loop for batched requests ---
        self._async_loop = asyncio.new_event_loop()
        self._async_thread = Thread(
            target=self._async_loop.run_forever,
            daemon=True,
            name="RLM-AsyncLoop",
        )
        self._async_thread.start()

        # --- Connection limiting ---
        self._max_connections = max_connections
        self._active_connections = 0
        self._conn_lock = Lock()

        # --- Thread pool (bounded, reusable threads) ---
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="RLM-Worker",
        )

        # --- Observability ---
        self._total_requests = 0
        self._total_errors = 0
        self._metrics_lock = Lock()

    # -- Async support -------------------------------------------------------

    def run_async(self, coro):
        """Submit a coroutine to the persistent loop from any handler thread."""
        future = asyncio.run_coroutine_threadsafe(coro, self._async_loop)
        return future.result()

    # -- Connection management -----------------------------------------------

    def process_request(self, request, client_address):
        """Override: use thread pool instead of spawning unlimited threads."""
        with self._conn_lock:
            if self._active_connections >= self._max_connections:
                logger.warn(
                    "Connection limit reached, rejecting client",
                    active_connections=self._active_connections,
                    max_connections=self._max_connections,
                    client_address=str(client_address),
                )
                self.close_request(request)
                return
            self._active_connections += 1

        self._thread_pool.submit(self._process_in_thread, request, client_address)

    def _process_in_thread(self, request, client_address):
        """Run the handler inside the thread pool, tracking lifecycle."""
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            with self._conn_lock:
                self._active_connections -= 1

    def process_request_thread(self, request, client_address):
        """Not used — overridden by process_request above.

        Kept to satisfy super()'s interface if called inadvertently.
        """
        self._process_in_thread(request, client_address)

    # -- Metrics -------------------------------------------------------------

    def record_request(self, *, error: bool = False):
        """Called by handlers to track request/error counts."""
        with self._metrics_lock:
            self._total_requests += 1
            if error:
                self._total_errors += 1

    @property
    def stats(self) -> dict:
        """Snapshot of server metrics."""
        with self._conn_lock:
            active = self._active_connections
        with self._metrics_lock:
            return {
                "active_connections": active,
                "max_connections": self._max_connections,
                "total_requests": self._total_requests,
                "total_errors": self._total_errors,
            }

    # -- Lifecycle -----------------------------------------------------------

    def server_close(self):
        """Graceful shutdown: stop async loop, drain thread pool, close server."""
        self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        self._async_thread.join(timeout=5)
        if not self._async_loop.is_closed():
            self._async_loop.close()

        self._thread_pool.shutdown(wait=True, cancel_futures=False)
        super().server_close()


class LMHandler:
    """
    Handles all LM calls from the RLM main process and environment subprocesses.

    Uses a multi-threaded socket server for concurrent requests.
    Protocol: 4-byte big-endian length prefix + JSON payload.
    """

    def __init__(
        self,
        client: BaseLM,
        host: str = "127.0.0.1",
        port: int = 0,  # auto-assign available port
        other_backend_client: BaseLM | None = None,
        max_connections: int = 128,
        max_workers: int = 32,
    ):
        self.default_client = client
        self.other_backend_client = other_backend_client
        self.clients: dict[str, BaseLM] = {}
        self.host = host
        self._server: ThreadingLMServer | None = None
        self._thread: Thread | None = None
        self._port = port
        self._max_connections = max_connections
        self._max_workers = max_workers

        # Fase 10: Rate limiter — máx N chamadas LLM concorrentes
        import os as _os
        _max_llm = int(_os.environ.get("RLM_MAX_CONCURRENT_LLM", "5"))
        self._llm_limiter = SyncLimiter(max_concurrent=_max_llm)

        self.register_client(client.model_name, client)

    def register_client(self, model_name: str, client: BaseLM) -> None:
        """Register a client for a specific model name."""
        self.clients[model_name] = client

    def get_client(self, model: str | None = None, depth: int = 0) -> BaseLM:
        """Get client by model name or depth, or return default.

        Routing logic:
        - depth=0: use default_client (main backend)
        - depth=1: use other_backend_client if it exists, otherwise default_client
        - If model is specified and exists in clients, use that (overrides depth routing)
        """
        if model and model in self.clients:
            return self.clients[model]

        # Route based on depth
        if depth == 1 and self.other_backend_client is not None:
            return self.other_backend_client

        return self.default_client

    @property
    def port(self) -> int:
        """Get the actual port (useful when auto-assigned)."""
        if self._server:
            return self._server.server_address[1]
        return self._port

    @property
    def address(self) -> tuple[str, int]:
        """Get (host, port) tuple for connecting."""
        return (self.host, self.port)

    def start(self) -> tuple[str, int]:
        """Start the socket server in a background thread. Returns (host, port)."""
        if self._server is not None:
            return self.address

        self._server = ThreadingLMServer(
            (self.host, self._port),
            LMRequestHandler,
            max_connections=self._max_connections,
            max_workers=self._max_workers,
        )
        self._server.lm_handler = self  # type: ignore

        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        return self.address

    def stop(self):
        """Stop the socket server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._thread = None

    @property
    def server_stats(self) -> dict | None:
        """Server metrics snapshot, or None if not started."""
        if self._server:
            return self._server.stats
        return None

    def completion(self, prompt: str, model: str | None = None) -> str:
        """Direct completion call (for main process use). Fase 10: Rate-limited."""
        client = self.get_client(model)
        response = ""
        with self._llm_limiter:
            response = str(client.completion(prompt))
        return response

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    def get_usage_summary(self) -> UsageSummary:
        """Get the usage summary for all clients, merged into a single dict."""
        merged = {}
        # Include default client
        default_summary = self.default_client.get_usage_summary()
        merged.update(default_summary.model_usage_summaries)
        # Include other backend client if it exists
        if self.other_backend_client is not None:
            other_summary = self.other_backend_client.get_usage_summary()
            merged.update(other_summary.model_usage_summaries)
        # Include all registered clients
        for client in self.clients.values():
            client_summary = client.get_usage_summary()
            merged.update(client_summary.model_usage_summaries)
        return UsageSummary(model_usage_summaries=merged)
