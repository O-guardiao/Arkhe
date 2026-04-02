from __future__ import annotations

from datetime import datetime
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rlm.core.optimized import wire as optimized_wire
from rlm.core.optimized.parsing import find_code_blocks as optimized_find_code_blocks
from rlm.core.optimized.parsing import format_iteration_rs as optimized_format_iteration
from rlm.core.optimized.parsing import find_final_answer as optimized_find_final_answer
from rlm.core.optimized.parsing import compute_hash as optimized_compute_hash
from rlm.utils.parsing import find_code_blocks as original_find_code_blocks
from rlm.utils.parsing import find_final_answer as original_find_final_answer


class _PendingEnv:
    def __init__(self, pending: str | None = None, stdout: str = "resolved") -> None:
        self.pending = pending
        self.stdout = stdout
        self.calls: list[str] = []

    def get_pending_final(self) -> str | None:
        return self.pending

    def execute_code(self, code: str) -> SimpleNamespace:
        self.calls.append(code)
        return SimpleNamespace(stdout=self.stdout, stderr="", locals={})


class _PartialHeaderSock:
    def __init__(self, header_parts: list[bytes], payload: bytes) -> None:
        self._parts = list(header_parts) + [payload]

    def recv_into(self, view) -> int:
        if not self._parts:
            return 0
        data = self._parts.pop(0)
        view[: len(data)] = data
        return len(data)


class _CaptureSock:
    def __init__(self) -> None:
        self.data = b""

    def sendall(self, data: bytes) -> None:
        self.data = data


def test_find_code_blocks_matches_original() -> None:
    text = """Primeiro bloco:\r\n```repl\r\nprint(1)\r\n```\r\nSegundo bloco:\n```repl\nx = 2\n```"""

    assert optimized_find_code_blocks(text) == original_find_code_blocks(text)


def test_find_final_answer_pending_final_matches_original() -> None:
    env = _PendingEnv(pending="valor_pendente")

    assert optimized_find_final_answer("texto sem FINAL", environment=env) == original_find_final_answer(
        "texto sem FINAL", environment=env
    )


def test_find_final_answer_final_identifier_matches_original_contract() -> None:
    env = _PendingEnv(stdout="valor_resolvido")
    text = "FINAL(answer)"

    assert optimized_find_final_answer(text, environment=env) == original_find_final_answer(text, environment=env)
    assert env.calls == []


def test_find_final_answer_final_var_missing_variable_matches_original() -> None:
    env = Mock()
    env.get_pending_final.return_value = None
    env.execute_code.return_value = SimpleNamespace(stdout="Error: missing", stderr="", locals={})
    text = "FINAL_VAR(answer)"

    assert optimized_find_final_answer(text, environment=env) == original_find_final_answer(text, environment=env)


def test_compute_hash_matches_loop_detector_contract() -> None:
    text = "algum texto para hashing"

    assert optimized_compute_hash(text) == hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def test_format_iteration_helper_matches_python_contract() -> None:
    pairs = optimized_format_iteration(
        "Resposta do modelo",
        [("print('x')", "y" * 20)],
        8,
    )

    assert pairs[0] == ("assistant", "Resposta do modelo")
    assert pairs[1][0] == "user"
    assert "Code executed:" in pairs[1][1]
    assert "print('x')" in pairs[1][1]
    assert "... + [12 chars...]" in pairs[1][1]


def test_socket_recv_handles_partial_header_reads() -> None:
    payload = optimized_wire.json_dumps({"a": 1})
    header = optimized_wire._LENGTH_STRUCT.pack(len(payload))
    sock = _PartialHeaderSock([header[:2], header[2:]], payload)

    assert optimized_wire.socket_recv(sock) == {"a": 1}


def test_socket_recv_rejects_frame_larger_than_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(optimized_wire, "MAX_FRAME_SIZE", 4)
    payload = b"12345"
    header = optimized_wire._LENGTH_STRUCT.pack(len(payload))
    sock = _PartialHeaderSock([header], payload)

    with pytest.raises(ValueError, match="Frame too large"):
        optimized_wire.socket_recv(sock)


def test_socket_send_serializes_datetime_and_roundtrips_json() -> None:
    sock = _CaptureSock()

    optimized_wire.socket_send(sock, {"when": datetime(2026, 3, 30, 12, 0, 0)})

    payload = sock.data[optimized_wire._LENGTH_STRUCT.size :]
    parsed = optimized_wire.json_loads(payload)
    # orjson serialises datetime natively as ISO-8601 (with T separator);
    # Rust uses datetime.isoformat() which also uses T separator;
    # stdlib json falls through to _json_default → str() which uses a space.
    expected = "2026-03-30T12:00:00" if optimized_wire.JSON_BACKEND in ("orjson", "rust") else "2026-03-30 12:00:00"
    assert parsed == {"when": expected}


def test_socket_send_sanitizes_invalid_unicode() -> None:
    sock = _CaptureSock()

    optimized_wire.socket_send(sock, {"text": "bad\ud800value"})

    payload = sock.data[optimized_wire._LENGTH_STRUCT.size :]
    parsed = optimized_wire.json_loads(payload)
    assert parsed["text"] == "bad�value"