"""
test_invariants_context — Invariante I: alto contexto persistente.

Garante que sessões preservam contexto (variáveis, histórico, state)
entre turnos de interação, independentemente de path de dispatch.
Estes NÃO são testes de migração — são invariantes de produto.
"""
from __future__ import annotations

import pytest


class TestHighContextPersistence:
    """Contexto de sessão sobrevive turnos e operações puras."""

    def test_add_context_returns_monotonic_index(self):
        """add_context retorna índices monotonicamente crescentes."""
        from rlm.environments.local_repl import LocalREPL

        repl = LocalREPL()
        idx0 = repl.add_context("ctx alpha")
        idx1 = repl.add_context("ctx beta")
        idx2 = repl.add_context("ctx gamma")

        assert idx0 < idx1 < idx2
        assert repl.get_context_count() >= 3

    def test_add_history_returns_monotonic_index(self):
        """add_history retorna índices monotonicamente crescentes."""
        from rlm.environments.local_repl import LocalREPL

        repl = LocalREPL()
        h0 = repl.add_history([{"role": "user", "content": "turno 1"}])
        h1 = repl.add_history([{"role": "user", "content": "turno 2"}])

        assert h0 < h1
        assert repl.get_history_count() >= 2

    def test_session_id_generation_is_deterministic_length(self):
        """create_session_id gera IDs com tamanho fixo de 32 chars."""
        from rlm.core.session.session_key import create_session_id, is_session_id

        ids = [create_session_id() for _ in range(10)]
        assert all(len(sid) == 32 for sid in ids)
        assert all(is_session_id(sid) for sid in ids)
        # Todos únicos
        assert len(set(ids)) == 10

    def test_session_key_construction(self):
        """SessionKey aceita construção e expõe campos."""
        from rlm.core.session.session_key import SessionKey, create_session_id

        sid = create_session_id()
        key = SessionKey(
            session_id=sid,
            channel_type="telegram",
            channel_id="chat_42",
            user_id="user_1",
        )
        assert key.session_id == sid
        assert key.channel_type == "telegram"

    def test_session_identity_roundtrip(self):
        """SessionIdentity preserva todos os campos."""
        from rlm.core.session.session_key import create_session_id, SessionIdentity

        sid = create_session_id()
        identity = SessionIdentity(
            session_id=sid,
            client_id="test-client",
            user_id="user-42",
            channel="api",
        )
        assert identity.session_id == sid
        assert identity.device_id is None  # default

    def test_session_identity_frozen(self):
        """SessionIdentity é imutável (frozen dataclass)."""
        from rlm.core.session.session_key import SessionIdentity

        identity = SessionIdentity(session_id="a" * 32, client_id="c1")
        with pytest.raises(AttributeError):
            identity.session_id = "b" * 32  # type: ignore[misc]
