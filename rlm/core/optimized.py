"""
RLM Optimized - High-performance Python implementation
======================================================

Esta é uma versão Python otimizada dos componentes críticos do RLM.
Usa técnicas como:
- Regex compilado (1 vez, reutilizado)
- Buffer pre-alocado
- Struct nativo para parsing binário
- JSON acceleration via orjson (se disponível)

Para performance máxima, instale: pip install orjson

Ganho esperado vs Python padrão: 2-5x
(Rust seria 50-100x, mas há problemas de build no Windows)
"""

import re
import struct
import socket
from typing import Optional, Any
from dataclasses import dataclass

from rlm.core.types import RLMChatCompletion

# Try to use orjson for 3-5x faster JSON
try:
    import orjson
    
    def json_dumps(obj: Any) -> bytes:
        return orjson.dumps(obj)
    
    def json_loads(data: bytes) -> Any:
        return orjson.loads(data)
    
    JSON_BACKEND = "orjson"
except ImportError:
    import json
    
    def json_dumps(obj: Any) -> bytes:
        return json.dumps(obj).encode("utf-8")
    
    def json_loads(data: bytes) -> Any:
        return json.loads(data.decode("utf-8"))
    
    JSON_BACKEND = "stdlib"

# =============================================================================
# Compiled Regexes (compiled once, reused)
# =============================================================================

# Pattern for ```repl code blocks (handles both \n and \r\n)
_CODE_BLOCK_PATTERN = re.compile(r"```repl\s*\r?\n(.*?)\r?\n```", re.DOTALL)

# Pattern for FINAL_VAR(...)
_FINAL_VAR_PATTERN = re.compile(r"^\s*FINAL_VAR\((.*?)\)", re.MULTILINE | re.DOTALL)

# Pattern for FINAL(...)
_FINAL_PATTERN = re.compile(r"^\s*FINAL\((.*)\)\s*$", re.MULTILINE | re.DOTALL)


def find_code_blocks(text: str) -> list[str]:
    """
    Find REPL code blocks in text wrapped in triple backticks.
    
    Optimized: Uses pre-compiled regex pattern.
    
    Returns:
        List of code content strings (without markers)
    """
    return [match.group(1).strip() for match in _CODE_BLOCK_PATTERN.finditer(text)]


def find_final_answer(text: str, environment: Optional[object] = None) -> str | None:
    """
    Find FINAL(...) or FINAL_VAR(...) statement in response and return the final answer string.
    
    Optimized: Uses pre-compiled regex patterns.
    
    Args:
        text: The response text to parse
        environment: Optional environment to execute code for FINAL_VAR retrieval
        
    Returns:
        The final answer string, or None if no final answer pattern is found
    """
    # Check FINAL_VAR first (higher priority)
    match = _FINAL_VAR_PATTERN.search(text)
    if match:
        variable_name = match.group(1).strip().strip('"').strip("'")
        # If environment is provided, resolve the variable
        if environment is not None and hasattr(environment, 'execute_code'):
            result = environment.execute_code(f"print(FINAL_VAR({variable_name!r}))")
            final_answer = result.stdout.strip()
            # Guard: if resolution returned error or empty, return None
            # so the RLM loop continues and the model can correct itself.
            if final_answer.startswith("Error:"):
                return None
            if final_answer == "":
                return None
            return final_answer
        # If no environment, return None (matching original implementation)
        return None
    
    # Check FINAL pattern
    match = _FINAL_PATTERN.search(text)
    if match:
        raw = match.group(1).strip()
        # If argument looks like a variable name (no quotes, valid identifier),
        # try to resolve it via environment — same as FINAL_VAR.
        # This prevents the common mistake of FINAL(var) returning the literal name.
        stripped = raw.strip('"').strip("'")
        if stripped.isidentifier() and environment is not None and hasattr(environment, 'execute_code'):
            result = environment.execute_code(f"print(FINAL_VAR({stripped!r}))")
            resolved = result.stdout.strip()
            if resolved and not resolved.startswith("Error:"):
                return resolved
        return raw
    
    return None


# =============================================================================
# Socket Communication (optimized)
# =============================================================================

# Pre-allocated struct for length prefix
_LENGTH_STRUCT = struct.Struct(">I")


def socket_send(sock: socket.socket, data: dict) -> None:
    """
    Send a length-prefixed JSON message over socket.
    
    Protocol: 4-byte big-endian length prefix + UTF-8 JSON payload.
    
    Optimized:
    - Uses orjson for faster serialization
    - Pre-packed struct for length prefix
    - Single sendall call
    """
    payload = json_dumps(data)
    length_prefix = _LENGTH_STRUCT.pack(len(payload))
    sock.sendall(length_prefix + payload)


def socket_recv(sock: socket.socket) -> dict:
    """
    Receive a length-prefixed JSON message from socket.
    
    Protocol: 4-byte big-endian length prefix + UTF-8 JSON payload.
    
    Optimized:
    - Uses memoryview to avoid copies
    - Pre-allocated buffer
    - orjson for parsing
    """
    # Read 4-byte length prefix
    raw_len = sock.recv(4)
    if not raw_len:
        return {}
    
    length = _LENGTH_STRUCT.unpack(raw_len)[0]
    
    # Pre-allocate buffer and read in chunks
    buffer = bytearray(length)
    view = memoryview(buffer)
    bytes_read = 0
    
    while bytes_read < length:
        chunk_size = sock.recv_into(view[bytes_read:])
        if not chunk_size:
            raise ConnectionError("Connection closed before message complete")
        bytes_read += chunk_size
    
    return json_loads(bytes(buffer))


def socket_request(
    address: tuple[str, int],
    data: dict,
    timeout: int = 300
) -> dict:
    """
    Send a request and receive a response over a new socket connection.
    
    Args:
        address: (host, port) tuple
        data: Dictionary to send as JSON
        timeout: Socket timeout in seconds
    
    Returns:
        Response dictionary
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(address)
        socket_send(sock, data)
        return socket_recv(sock)


# =============================================================================
# LM Handler Classes
# =============================================================================

@dataclass
class LMRequest:
    """Request message sent to the LM Handler."""
    prompt: str | dict[str, Any] | None = None
    prompts: list[str | dict[str, Any]] | None = None
    model: str | None = None
    depth: int = 0
    
    @property
    def is_batched(self) -> bool:
        return self.prompts is not None and len(self.prompts) > 0
    
    def to_dict(self) -> dict:
        d = {"depth": self.depth}
        if self.prompt is not None:
            d["prompt"] = self.prompt
        if self.prompts is not None:
            d["prompts"] = self.prompts
        if self.model is not None:
            d["model"] = self.model
        return d
    
    @classmethod
    def from_dict(cls, data: dict) -> "LMRequest":
        return cls(
            prompt=data.get("prompt"),
            prompts=data.get("prompts"),
            model=data.get("model"),
            depth=data.get("depth", 0),
        )


@dataclass
class LMResponse:
    """Response message from the LM Handler."""
    error: str | None = None
    chat_completion: RLMChatCompletion | None = None
    chat_completions: list[RLMChatCompletion] | None = None
    
    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def is_batched(self) -> bool:
        return self.chat_completions is not None

    def to_dict(self) -> dict:
        if self.error is not None:
            return {"error": self.error, "chat_completion": None, "chat_completions": None}
        if self.chat_completions is not None:
            return {
                "chat_completions": [
                    c.to_dict() if hasattr(c, "to_dict") else c
                    for c in self.chat_completions
                ],
                "chat_completion": None,
                "error": None,
            }
        if self.chat_completion is not None:
            cc = self.chat_completion
            return {
                "chat_completion": cc.to_dict() if hasattr(cc, "to_dict") else cc,
                "chat_completions": None,
                "error": None,
            }
        return {"error": "No response", "chat_completion": None, "chat_completions": None}
    
    @classmethod
    def from_dict(cls, data: dict) -> "LMResponse":
        chat_completion = None
        if data.get("chat_completion"):
            raw = data["chat_completion"]
            chat_completion = RLMChatCompletion.from_dict(raw) if isinstance(raw, dict) else raw

        chat_completions = None
        if data.get("chat_completions"):
            chat_completions = [
                RLMChatCompletion.from_dict(c) if isinstance(c, dict) else c
                for c in data["chat_completions"]
            ]

        return cls(
            error=data.get("error"),
            chat_completion=chat_completion,
            chat_completions=chat_completions,
        )

    @classmethod
    def success_response(cls, chat_completion: RLMChatCompletion) -> "LMResponse":
        return cls(chat_completion=chat_completion)

    @classmethod
    def batched_success_response(cls, chat_completions: list[RLMChatCompletion]) -> "LMResponse":
        return cls(chat_completions=chat_completions)

    @classmethod
    def error_response(cls, error: str) -> "LMResponse":
        return cls(error=error)


def send_lm_request(
    address: tuple[str, int],
    request: LMRequest,
    timeout: int = 300,
    depth: int | None = None,
) -> LMResponse:
    """Send an LM request and return typed response."""
    try:
        if depth is not None:
            request.depth = depth
        response_data = socket_request(address, request.to_dict(), timeout)
        return LMResponse.from_dict(response_data)
    except Exception as e:
        return LMResponse(error=f"Request failed: {e}")


# =============================================================================
# Benchmark / Self-test
# =============================================================================

def benchmark():
    """Run a quick benchmark to show performance."""
    import time
    
    # Test data
    test_text = """
Let me solve this problem step by step:

```repl
x = 2 + 2
y = x * 10
print(f"Result: {y}")
```

Based on the calculation:

```repl
final_answer = y + 100
```

FINAL(The answer is 140)
"""
    
    iterations = 10000
    
    # Benchmark find_code_blocks
    start = time.perf_counter()
    for _ in range(iterations):
        find_code_blocks(test_text)
    elapsed = time.perf_counter() - start
    print(f"find_code_blocks: {iterations} iterations in {elapsed:.3f}s ({iterations/elapsed:.0f} ops/s)")
    
    # Benchmark find_final_answer
    start = time.perf_counter()
    for _ in range(iterations):
        find_final_answer(test_text)
    elapsed = time.perf_counter() - start
    print(f"find_final_answer: {iterations} iterations in {elapsed:.3f}s ({iterations/elapsed:.0f} ops/s)")
    
    # Benchmark JSON
    test_dict = {"prompt": "Hello world", "model": "gpt-4", "depth": 0, "data": list(range(100))}
    start = time.perf_counter()
    for _ in range(iterations):
        payload = json_dumps(test_dict)
        json_loads(payload)
    elapsed = time.perf_counter() - start
    print(f"json roundtrip ({JSON_BACKEND}): {iterations} iterations in {elapsed:.3f}s ({iterations/elapsed:.0f} ops/s)")


if __name__ == "__main__":
    print(f"RLM Optimized - JSON backend: {JSON_BACKEND}")
    print("-" * 50)
    benchmark()
    print("-" * 50)
    print("✅ All benchmarks complete!")
