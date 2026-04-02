"""Facade for the optimized Python backend.

This module preserves the original public API while delegating responsibilities
to dedicated modules for parsing, wire protocol, and typed LM messages.
"""

from __future__ import annotations

from rlm.core.optimized.benchmark import benchmark
from rlm.core.optimized.parsing import compute_hash, find_code_blocks, find_final_answer, format_iteration_rs
from rlm.core.optimized.opt_types import LMRequest, LMResponse
from rlm.core.optimized.wire import (
    JSON_BACKEND,
    MAX_FRAME_SIZE,
    json_dumps,
    json_loads,
    send_lm_request,
    send_lm_request_batched,
    socket_recv,
    socket_request,
    socket_send,
)

__all__ = [
    "JSON_BACKEND",
    "MAX_FRAME_SIZE",
    "LMRequest",
    "LMResponse",
    "benchmark",
    "compute_hash",
    "find_code_blocks",
    "find_final_answer",
    "format_iteration_rs",
    "json_dumps",
    "json_loads",
    "send_lm_request",
    "send_lm_request_batched",
    "socket_recv",
    "socket_request",
    "socket_send",
]


if __name__ == "__main__":
    from rlm.core.optimized.benchmark import main as _main

    _main()
