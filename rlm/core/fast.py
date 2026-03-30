"""
RLM Fast - Drop-in replacement for RLM core modules
====================================================

Este módulo tenta usar o backend Python mais rápido disponível:
1. rlm.core.optimized (Python otimizado) - 2-5x faster
2. Fallback para implementação original

Uso:
    from rlm.core.fast import (
        socket_send,
        socket_recv,
        socket_request,
        find_code_blocks,
        find_final_answer,
    )
"""

import threading as _threading
import warnings
from typing import Any

try:
    from rlm.core.optimized import (
        compute_hash,
        find_code_blocks,
        find_final_answer,
        format_iteration_rs,
        socket_recv,
        socket_request,
        socket_send,
    )
    BACKEND = "optimized"

except ImportError:
    from rlm.core.comms_utils import socket_send, socket_recv
    from rlm.utils.parsing import find_code_blocks, find_final_answer

    format_iteration_rs = None
    compute_hash = None

    def socket_request(address, data, timeout=300):
        """Wrapper for original implementation."""
        import socket as sock_module

        with sock_module.socket(sock_module.AF_INET, sock_module.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(address)
            socket_send(s, data)
            return socket_recv(s)

    BACKEND = "original"
    warnings.warn(
        "Using original RLM implementation. "
        "For 2-5x speedup, install orjson: pip install orjson",
        RuntimeWarning,
    )


def get_backend() -> str:
    """Return the current backend being used."""
    return BACKEND


def print_backend_info():
    """Print information about the current backend."""
    speedup = {
        "optimized": "2-5x",
        "original": "1x (baseline)",
    }
    print(f"RLM Backend: {BACKEND} ({speedup[BACKEND]} speedup)")


# Re-export for convenience
__all__ = [
    "socket_send",
    "socket_recv",
    "socket_request",
    "find_code_blocks",
    "find_final_answer",
    "format_iteration_rs",
    "compute_hash",
    "get_backend",
    "print_backend_info",
    "BACKEND",
    "LMRequest",
    "LMResponse",
    "send_lm_request",
    "send_lm_request_batched",
]

# Always provide high-level wrappers from optimized implementation
from rlm.core.optimized import (
    LMRequest,
    LMResponse,
)


class _ConnectionPool:
    """Thread-safe TCP connection pool for LMHandler server communication.

    The LMHandler server now supports persistent connections (serves multiple
    request-response pairs per socket), so this pool eliminates the per-call
    TCP handshake overhead (~0.5ms per call on loopback).
    Each thread gets exclusive access to a socket while using it.
    """

    def __init__(self, max_idle: int = 8):
        self._pools: dict[tuple, list] = {}
        self._lock = _threading.Lock()
        self._max_idle = max_idle

    def get(self, address: tuple, timeout: int):
        import socket as _socket
        key = (address[0], address[1], timeout)
        with self._lock:
            pool = self._pools.get(key, [])
            while pool:
                sock = pool.pop()
                try:
                    sock.getpeername()  # raises OSError if connection is dead
                    sock.settimeout(timeout)
                    return sock
                except OSError:
                    pass  # stale socket — discard and try next
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(address)
        return sock

    def release(self, address: tuple, timeout: int, sock) -> None:
        key = (address[0], address[1], timeout)
        with self._lock:
            pool = self._pools.setdefault(key, [])
            if len(pool) < self._max_idle:
                pool.append(sock)
                return
        sock.close()  # pool full


_lm_pool = _ConnectionPool()


def _pool_request(address: tuple, data_dict: dict, timeout: int) -> dict:
    """Send one request via pooled connection, retrying once on stale socket."""
    last_err: Exception | None = None
    for _ in range(2):
        sock = _lm_pool.get(address, timeout)
        ok = False
        try:
            socket_send(sock, data_dict)
            resp = socket_recv(sock)
            if resp is None:
                # Server closed connection — force fresh socket on next attempt
                sock.close()
                continue
            _lm_pool.release(address, timeout, sock)
            ok = True
            return resp
        except Exception as exc:
            last_err = exc
        finally:
            if not ok:
                try:
                    sock.close()
                except OSError:
                    pass
    raise ConnectionError(f"Request failed: {last_err}")


def send_lm_request(
    address: tuple[str, int],
    request: LMRequest,
    timeout: int = 300,
    depth: int | None = None,
) -> LMResponse:
    """Send an LM request using a pooled connection for reduced TCP overhead."""
    try:
        if depth is not None:
            request.depth = depth
        response_data = _pool_request(address, request.to_dict(), timeout)
        return LMResponse.from_dict(response_data)
    except Exception as e:
        return LMResponse(error=f"Request failed: {e}")


def send_lm_request_batched(
    address: tuple[str, int],
    prompts: list[str | dict[str, Any]],
    model: str | None = None,
    timeout: int = 300,
    depth: int = 0,
) -> list[LMResponse]:
    """Send a batched LM request using a pooled connection."""
    try:
        request = LMRequest(prompts=prompts, model=model, depth=depth)
        response_data = _pool_request(address, request.to_dict(), timeout)
        response = LMResponse.from_dict(response_data)

        if not response.success:
            error_msg = response.error or "Unknown error"
            return [LMResponse(error=error_msg)] * len(prompts)

        if response.chat_completions is None:
            return [LMResponse(error="No completions returned")] * len(prompts)

        return [
            LMResponse(chat_completion=chat_completion)
            for chat_completion in response.chat_completions
        ]
    except Exception as e:
        return [LMResponse(error=f"Request failed: {e}")] * len(prompts)
