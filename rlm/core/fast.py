"""
RLM Fast - Drop-in replacement for RLM core modules
====================================================

Este módulo tenta usar o backend mais rápido disponível:
1. rlm_rust (Rust via PyO3) - 50-100x faster
2. rlm.core.optimized (Python otimizado) - 2-5x faster
3. Fallback para implementação original

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
from typing import Any, Optional

# Optional Rust extensions — set to None until binary is rebuilt with new exports
format_iteration_rs = None
compute_hash = None

# Try Rust backend first (fastest)
try:
    from rlm_rust import (
        socket_send as _rust_socket_send,
        socket_recv as _rust_socket_recv,
        socket_request as _rust_socket_request,
        find_code_blocks as _rust_find_code_blocks,
        find_final_answer as _rust_find_final_answer,
        RustLMHandler,
    )
    
    def socket_send(sock, data):
        """Wrapper for Rust socket_send that handles socket objects."""
        if hasattr(sock, "fileno"):
            _rust_socket_send(sock.fileno(), data)
        else:
            _rust_socket_send(sock, data)
            
    def socket_recv(sock):
        """Wrapper for Rust socket_recv that handles socket objects."""
        if hasattr(sock, "fileno"):
            return _rust_socket_recv(sock.fileno())
        return _rust_socket_recv(sock)

    def socket_request(address, data, timeout=300):
        """Wrapper for Rust socket_request to accept optional timeout kwarg."""
        try:
            return _rust_socket_request(address, data, timeout)
        except TypeError:
            # Rust version may not accept timeout
            return _rust_socket_request(address, data)

    def find_code_blocks(text, *args, **kwargs):
        """Wrapper for Rust find_code_blocks to ignore extra args."""
        return _rust_find_code_blocks(text)

    def find_final_answer(text, *args, **kwargs):
        """Wrapper: uses Rust regex for speed, but delegates FINAL_VAR resolution
        to the Python environment when one is provided (Rust can't access it).
        """
        environment = kwargs.get("environment") or (args[0] if args else None)
        rust_result = _rust_find_final_answer(text)

        # If Rust found a FINAL_VAR match, it returns the variable NAME, not the value.
        # We need to resolve it through the environment.
        if rust_result is not None and environment is not None:
            import re
            _fv = re.search(r"^\s*FINAL_VAR\((.*?)\)", text, re.MULTILINE | re.DOTALL)
            if _fv:
                var_name = _fv.group(1).strip().strip('"').strip("'")
                if hasattr(environment, "execute_code"):
                    exec_result = environment.execute_code(f"print(FINAL_VAR({var_name!r}))")
                    resolved = exec_result.stdout.strip()
                    if resolved and not resolved.startswith("Error:"):
                        return resolved
                # FINAL_VAR resolution failed — return None so the RLM loop
                # continues and the model can correct itself (matches original behavior).
                return None

        return rust_result
        
    BACKEND = "rust"

    # Optional parsing/hashing extensions (available after maturin rebuild)
    try:
        from rlm_rust import (
            format_iteration_rs as _rust_format_iteration,
            compute_hash as _rust_compute_hash,
        )
        format_iteration_rs = _rust_format_iteration
        compute_hash = _rust_compute_hash
    except ImportError:
        pass  # Leave as None; Python fallbacks in loop_detector and parsing

except ImportError:
    # Fall back to optimized Python
    try:
        from rlm.core.optimized import (
            socket_send,
            socket_recv,
            socket_request,
            find_code_blocks,
            find_final_answer,
        )
        BACKEND = "optimized"
        RustLMHandler = None
        
    except ImportError:
        # Fall back to original implementation
        from rlm.core.comms_utils import socket_send, socket_recv
        from rlm.utils.parsing import find_code_blocks, find_final_answer
        
        def socket_request(address, data, timeout=300):
            """Wrapper for original implementation."""
            import socket as sock_module
            with sock_module.socket(sock_module.AF_INET, sock_module.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(address)
                socket_send(s, data)
                return socket_recv(s)
        
        BACKEND = "original"
        RustLMHandler = None
        warnings.warn(
            "Using original RLM implementation. "
            "For 2-5x speedup, install orjson: pip install orjson",
            RuntimeWarning
        )


def get_backend() -> str:
    """Return the current backend being used."""
    return BACKEND


def print_backend_info():
    """Print information about the current backend."""
    speedup = {
        "rust": "50-100x",
        "optimized": "2-5x", 
        "original": "1x (baseline)"
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
    "RustLMHandler",
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
