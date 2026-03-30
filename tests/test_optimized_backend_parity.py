from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
import importlib.util

import pytest

from rlm.core import optimized_wire
from rlm.core.optimized_parsing import find_code_blocks as optimized_find_code_blocks
from rlm.core.optimized_parsing import find_final_answer as optimized_find_final_answer
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


def test_rust_and_optimized_match_for_plain_final() -> None:
    if importlib.util.find_spec("rlm_rust") is None:
        pytest.skip("rlm_rust indisponivel")

    try:
        from rlm_rust import find_code_blocks as rust_find_code_blocks
        from rlm_rust import find_final_answer as rust_find_final_answer
    except ImportError:
        pytest.skip("rlm_rust presente, mas o binario nao carregou")

    text = """```repl\nprint(1)\n```\nFINAL(42)"""

    assert rust_find_code_blocks(text) == optimized_find_code_blocks(text) == original_find_code_blocks(text)
    assert rust_find_final_answer(text) == optimized_find_final_answer(text) == original_find_final_answer(text)


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
    assert parsed == {"when": "2026-03-30 12:00:00"}


def test_socket_send_sanitizes_invalid_unicode() -> None:
    sock = _CaptureSock()

    optimized_wire.socket_send(sock, {"text": "bad\ud800value"})

    payload = sock.data[optimized_wire._LENGTH_STRUCT.size :]
    parsed = optimized_wire.json_loads(payload)
    assert parsed["text"] == "bad�value"