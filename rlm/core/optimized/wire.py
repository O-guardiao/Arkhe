from __future__ import annotations

import json
import os
import socket
import struct
from typing import Any

from rlm.core.optimized.opt_types import LMRequest, LMResponse

try:
    import orjson  # type: ignore[import-not-found]
except ImportError:
    orjson = None

try:
    import arkhe_wire as _wire_rs
    _RUST_WIRE = True
except ImportError:
    _wire_rs = None  # type: ignore[assignment]  # noqa: N816
    _RUST_WIRE = False

# NOTE: _wire_rs is always non-None when _RUST_WIRE is True.
# Pylance cannot narrow through a separate boolean guard.


_DEFAULT_MAX_FRAME_SIZE = 64 * 1024 * 1024
_LENGTH_STRUCT = struct.Struct(">I")


def _read_max_frame_size() -> int:
    raw = os.environ.get("RLM_MAX_SOCKET_FRAME_BYTES", "").strip()
    if not raw:
        return _DEFAULT_MAX_FRAME_SIZE
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_FRAME_SIZE
    return value if value > 0 else _DEFAULT_MAX_FRAME_SIZE


MAX_FRAME_SIZE = _read_max_frame_size()

if _RUST_WIRE:
    JSON_BACKEND = "rust"
elif orjson is not None:
    JSON_BACKEND = "orjson"
else:
    JSON_BACKEND = "stdlib"


def _json_default(value: Any) -> str:
    return str(value)


def _sanitize_surrogates(text: str) -> str:
    if not text:
        return text
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return "".join("�" if 0xD800 <= ord(char) <= 0xDFFF else char for char in text)


def _normalize_json_key(key: Any) -> Any:
    if isinstance(key, str):
        return _sanitize_surrogates(key)
    if isinstance(key, (int, float, bool)) or key is None:
        return key
    return str(key)


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_surrogates(value)
    if isinstance(value, dict):
        return {_normalize_json_key(key): _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, set):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def json_dumps(obj: Any) -> bytes:
    if _RUST_WIRE:
        return _wire_rs.wire_json_dumps(obj)  # type: ignore[union-attr]
    normalized = _normalize_json_value(obj)
    if orjson is not None:
        return orjson.dumps(normalized, default=_json_default)
    return json.dumps(normalized, default=str).encode("utf-8")


def json_loads(data: bytes) -> Any:
    if orjson is not None:
        return orjson.loads(data)
    return json.loads(data.decode("utf-8"))


def _recv_exactly(sock: Any, size: int, *, allow_eof: bool = False) -> bytes:
    if size == 0:
        return b""

    buffer = bytearray(size)
    view = memoryview(buffer)
    received = 0
    recv_into = getattr(sock, "recv_into", None)

    while received < size:
        if callable(recv_into):
            chunk_size = recv_into(view[received:])
        else:
            chunk = sock.recv(size - received)
            chunk_size = len(chunk)
            if chunk_size:
                view[received:received + chunk_size] = chunk

        if not chunk_size:
            if allow_eof and received == 0:
                return b""
            raise ConnectionError("Connection closed before message complete")

        received += chunk_size

    return bytes(buffer)


def socket_send(sock: socket.socket, data: dict[str, Any]) -> None:
    if _RUST_WIRE:
        sock.sendall(_wire_rs.wire_frame_encode(data))  # type: ignore[union-attr]
        return
    payload = json_dumps(data)
    length_prefix = _LENGTH_STRUCT.pack(len(payload))
    sock.sendall(length_prefix + payload)


def socket_recv(sock: socket.socket) -> dict[str, Any]:
    raw_len = _recv_exactly(sock, _LENGTH_STRUCT.size, allow_eof=True)
    if not raw_len:
        return {}

    length = _LENGTH_STRUCT.unpack(raw_len)[0]
    if length > MAX_FRAME_SIZE:
        raise ValueError(f"Frame too large: {length} bytes > {MAX_FRAME_SIZE}")

    payload = _recv_exactly(sock, length)
    message = json_loads(payload)
    if not isinstance(message, dict):
        raise ValueError("Socket payload must be a JSON object")
    return message


def socket_request(address: tuple[str, int], data: dict[str, Any], timeout: int = 300) -> dict[str, Any]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(address)
        socket_send(sock, data)
        return socket_recv(sock)


def send_lm_request(
    address: tuple[str, int],
    request: LMRequest,
    timeout: int = 300,
    depth: int | None = None,
) -> LMResponse:
    try:
        if depth is not None:
            request.depth = depth
        response_data = socket_request(address, request.to_dict(), timeout)
        return LMResponse.from_dict(response_data)
    except Exception as exc:
        return LMResponse.error_response(f"Request failed: {exc}")


def send_lm_request_batched(
    address: tuple[str, int],
    prompts: list[str | dict[str, Any]],
    model: str | None = None,
    timeout: int = 300,
    depth: int = 0,
) -> list[LMResponse]:
    try:
        request = LMRequest(prompts=prompts, model=model, depth=depth)
        response_data = socket_request(address, request.to_dict(), timeout)
        response = LMResponse.from_dict(response_data)

        if not response.success:
            error_msg = response.error or "Unknown error"
            return [LMResponse.error_response(error_msg)] * len(prompts)

        if response.chat_completions is None:
            return [LMResponse.error_response("No completions returned")] * len(prompts)

        return [LMResponse.success_response(chat_completion) for chat_completion in response.chat_completions]
    except Exception as exc:
        return [LMResponse.error_response(f"Request failed: {exc}")] * len(prompts)