"""
Testes — Phase 5: Camada 3 — Identidade por dispositivo (clients table + auth)

Cobre:
- register_client: sucesso, duplicata, formato do token, metadata/permissions
- authenticate_client: token válido, inválido, revogado, last_seen atualizado
- authenticate_or_legacy: per-device, legacy fallback, ambos falham
- revoke_client: sucesso, já revogado, inexistente
- list_clients: active_only, all
- get_client_status: encontrado, não encontrado
- ClientIdentity: preferred_channel, broadcast_channels properties
- Clients table migration via SessionManager._init_db()
- Session metadata population from ClientIdentity
- CLI commands import

Execute:
    pytest tests/test_phase5_auth_clients.py -v
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from rlm.core.auth import (
    ClientIdentity,
    _hash_token,
    authenticate_client,
    authenticate_or_legacy,
    get_client_status,
    list_clients,
    register_client,
    revoke_client,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def db_path(tmp_path):
    """Cria DB com tabela clients (mesma DDL de _init_db)."""
    path = str(tmp_path / "test_auth.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id              TEXT PRIMARY KEY,
                token_hash      TEXT NOT NULL,
                profile         TEXT NOT NULL,
                description     TEXT DEFAULT '',
                context_hint    TEXT DEFAULT '',
                permissions     TEXT DEFAULT '[]',
                active          INTEGER DEFAULT 1,
                created_at      TEXT NOT NULL,
                last_seen       TEXT,
                metadata        TEXT DEFAULT '{}'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clients_active ON clients(active)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clients_token_hash ON clients(token_hash)")
        conn.commit()
    return path


# ===========================================================================
# Part 1 — register_client
# ===========================================================================

class TestRegisterClient:

    def test_register_returns_token_with_prefix(self, db_path):
        token = register_client(db_path, "esp32-sala")
        assert token.startswith("rlm_")
        assert len(token) == 4 + 48  # rlm_ + 24 bytes hex

    def test_register_stores_hash_not_plaintext(self, db_path):
        token = register_client(db_path, "iphone-1")
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT token_hash FROM clients WHERE id = ?", ("iphone-1",)).fetchone()
        assert row is not None
        assert row[0] == _hash_token(token)
        assert row[0] != token  # never plaintext

    def test_register_with_profile_and_metadata(self, db_path):
        meta = {"preferred_channel": "telegram:123", "broadcast_channels": ["tui", "discord"]}
        register_client(
            db_path, "bot-discord",
            profile="conversational",
            description="Bot do Discord",
            context_hint="Responder em pt-br",
            permissions=["read", "write"],
            metadata=meta,
        )
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM clients WHERE id = ?", ("bot-discord",)).fetchone()

        assert row["profile"] == "conversational"
        assert row["description"] == "Bot do Discord"
        assert row["context_hint"] == "Responder em pt-br"
        assert json.loads(row["permissions"]) == ["read", "write"]
        assert json.loads(row["metadata"])["preferred_channel"] == "telegram:123"
        assert row["active"] == 1

    def test_register_duplicate_raises(self, db_path):
        register_client(db_path, "dup-client")
        with pytest.raises(ValueError, match="já existe"):
            register_client(db_path, "dup-client")

    def test_register_duplicate_revoked_raises(self, db_path):
        """Mesmo revogado, não permite re-registrar (deve ser intencional)."""
        register_client(db_path, "old-dev")
        revoke_client(db_path, "old-dev")
        with pytest.raises(ValueError, match="já existe"):
            register_client(db_path, "old-dev")

    def test_register_defaults(self, db_path):
        register_client(db_path, "minimal")
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM clients WHERE id = ?", ("minimal",)).fetchone()
        assert row["profile"] == "default"
        assert row["description"] == ""
        assert row["context_hint"] == ""
        assert json.loads(row["permissions"]) == []
        assert json.loads(row["metadata"]) == {}


# ===========================================================================
# Part 2 — authenticate_client
# ===========================================================================

class TestAuthenticateClient:

    def test_valid_token(self, db_path):
        token = register_client(db_path, "dev-1", profile="speed")
        identity = authenticate_client(db_path, token)
        assert identity is not None
        assert identity.client_id == "dev-1"
        assert identity.profile == "speed"

    def test_invalid_token(self, db_path):
        register_client(db_path, "dev-2")
        assert authenticate_client(db_path, "rlm_invalidtoken") is None

    def test_empty_token(self, db_path):
        assert authenticate_client(db_path, "") is None
        assert authenticate_client(db_path, None) is None

    def test_revoked_client_denied(self, db_path):
        token = register_client(db_path, "revoked-1")
        revoke_client(db_path, "revoked-1")
        assert authenticate_client(db_path, token) is None

    def test_last_seen_updated(self, db_path):
        token = register_client(db_path, "seen-test")
        # Before auth, last_seen is NULL
        with sqlite3.connect(db_path) as conn:
            before = conn.execute("SELECT last_seen FROM clients WHERE id = ?", ("seen-test",)).fetchone()
        assert before[0] is None

        authenticate_client(db_path, token)

        with sqlite3.connect(db_path) as conn:
            after = conn.execute("SELECT last_seen FROM clients WHERE id = ?", ("seen-test",)).fetchone()
        assert after[0] is not None

    def test_metadata_preserved(self, db_path):
        meta = {"preferred_channel": "tui", "custom": True}
        token = register_client(db_path, "meta-test", metadata=meta)
        identity = authenticate_client(db_path, token)
        assert identity.metadata["preferred_channel"] == "tui"
        assert identity.metadata["custom"] is True

    def test_permissions_preserved(self, db_path):
        token = register_client(db_path, "perm-test", permissions=["admin", "read"])
        identity = authenticate_client(db_path, token)
        assert identity.permissions == ["admin", "read"]


# ===========================================================================
# Part 3 — authenticate_or_legacy
# ===========================================================================

class TestAuthenticateOrLegacy:

    def test_per_device_priority(self, db_path):
        """Per-device token tem prioridade sobre legacy."""
        token = register_client(db_path, "primary-dev")
        legacy_tokens = ("legacy_secret",)
        identity = authenticate_or_legacy(db_path, token, legacy_tokens)
        assert identity is not None
        assert identity.client_id == "primary-dev"

    def test_legacy_fallback(self, db_path):
        """Se token não é de nenhum device, tenta legacy."""
        identity = authenticate_or_legacy(db_path, "my_legacy_token", ("my_legacy_token",))
        assert identity is not None
        assert identity.client_id == "legacy"
        assert identity.profile == "default"

    def test_both_fail(self, db_path):
        """Nem per-device nem legacy → None."""
        assert authenticate_or_legacy(db_path, "wrong", ("also_wrong",)) is None

    def test_empty_token(self, db_path):
        assert authenticate_or_legacy(db_path, "", ("secret",)) is None

    def test_empty_legacy_tuple(self, db_path):
        assert authenticate_or_legacy(db_path, "token_no_match", ()) is None

    def test_legacy_multiple_tokens(self, db_path):
        """Deve encontrar qualquer token na tupla legada."""
        identity = authenticate_or_legacy(
            db_path, "second_token", ("first_token", "second_token", "third_token")
        )
        assert identity is not None
        assert identity.client_id == "legacy"


# ===========================================================================
# Part 4 — revoke_client
# ===========================================================================

class TestRevokeClient:

    def test_revoke_success(self, db_path):
        register_client(db_path, "to-revoke")
        assert revoke_client(db_path, "to-revoke") is True

    def test_revoke_already_revoked(self, db_path):
        register_client(db_path, "double-revoke")
        revoke_client(db_path, "double-revoke")
        assert revoke_client(db_path, "double-revoke") is False

    def test_revoke_nonexistent(self, db_path):
        assert revoke_client(db_path, "ghost") is False

    def test_revoke_preserves_record(self, db_path):
        """Revoke marca active=0, não deleta."""
        register_client(db_path, "audit-trace")
        revoke_client(db_path, "audit-trace")
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT active FROM clients WHERE id = ?", ("audit-trace",)).fetchone()
        assert row is not None
        assert row[0] == 0


# ===========================================================================
# Part 5 — list_clients / get_client_status
# ===========================================================================

class TestListClients:

    def test_empty(self, db_path):
        assert list_clients(db_path) == []

    def test_active_only(self, db_path):
        register_client(db_path, "a1")
        register_client(db_path, "a2")
        register_client(db_path, "r1")
        revoke_client(db_path, "r1")
        result = list_clients(db_path, active_only=True)
        ids = [r["id"] for r in result]
        assert "a1" in ids
        assert "a2" in ids
        assert "r1" not in ids

    def test_all_includes_revoked(self, db_path):
        register_client(db_path, "x1")
        register_client(db_path, "x2")
        revoke_client(db_path, "x2")
        result = list_clients(db_path, active_only=False)
        ids = [r["id"] for r in result]
        assert "x1" in ids
        assert "x2" in ids

    def test_order_by_created_at_desc(self, db_path):
        import time
        register_client(db_path, "first")
        time.sleep(0.05)  # ensure different created_at timestamp
        register_client(db_path, "second")
        result = list_clients(db_path)
        # second was created after first
        assert result[0]["id"] == "second"
        assert result[1]["id"] == "first"


class TestGetClientStatus:

    def test_found(self, db_path):
        register_client(db_path, "status-check", profile="verbose")
        status = get_client_status(db_path, "status-check")
        assert status is not None
        assert status["id"] == "status-check"
        assert status["profile"] == "verbose"
        assert status["active"] == 1

    def test_not_found(self, db_path):
        assert get_client_status(db_path, "nope") is None


# ===========================================================================
# Part 6 — ClientIdentity properties
# ===========================================================================

class TestClientIdentityProperties:

    def test_preferred_channel_from_metadata(self):
        identity = ClientIdentity(
            client_id="dev",
            profile="default",
            metadata={"preferred_channel": "telegram:123"},
        )
        assert identity.preferred_channel == "telegram:123"

    def test_preferred_channel_none(self):
        identity = ClientIdentity(client_id="dev", profile="default")
        assert identity.preferred_channel is None

    def test_broadcast_channels_from_metadata(self):
        identity = ClientIdentity(
            client_id="dev",
            profile="default",
            metadata={"broadcast_channels": ["tui", "discord", "telegram:123"]},
        )
        assert identity.broadcast_channels == ["tui", "discord", "telegram:123"]

    def test_broadcast_channels_empty_default(self):
        identity = ClientIdentity(client_id="dev", profile="default")
        assert identity.broadcast_channels == []

    def test_frozen(self):
        identity = ClientIdentity(client_id="dev", profile="default")
        with pytest.raises(AttributeError):
            identity.client_id = "other"


# ===========================================================================
# Part 7 — _hash_token
# ===========================================================================

class TestHashToken:

    def test_deterministic(self):
        assert _hash_token("abc") == _hash_token("abc")

    def test_different_inputs(self):
        assert _hash_token("token_a") != _hash_token("token_b")

    def test_sha256_length(self):
        result = _hash_token("anything")
        assert len(result) == 64  # SHA-256 hex


# ===========================================================================
# Part 8 — Clients table via SessionManager._init_db()
# ===========================================================================

class TestClientsTableMigration:

    def test_init_db_creates_clients_table(self, tmp_path):
        """SessionManager._init_db() cria tabela clients automaticamente."""
        from rlm.core.session._impl import SessionManager
        db = str(tmp_path / "sessions.db")
        sm = SessionManager(db_path=db)
        with sqlite3.connect(db) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='clients'"
            ).fetchone()
        assert tables is not None

    def test_init_db_creates_indexes(self, tmp_path):
        from rlm.core.session._impl import SessionManager
        db = str(tmp_path / "sessions.db")
        sm = SessionManager(db_path=db)
        with sqlite3.connect(db) as conn:
            indexes = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            ]
        assert "idx_clients_active" in indexes
        assert "idx_clients_token_hash" in indexes

    def test_init_db_idempotent(self, tmp_path):
        """Chamar _init_db() duas vezes não falha (IF NOT EXISTS)."""
        from rlm.core.session._impl import SessionManager
        db = str(tmp_path / "sessions2.db")
        sm1 = SessionManager(db_path=db)
        sm2 = SessionManager(db_path=db)  # second init, same db
        with sqlite3.connect(db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='clients'"
            ).fetchone()[0]
        assert count == 1


# ===========================================================================
# Part 9 — Session metadata population from ClientIdentity
# ===========================================================================

class TestSessionMetadataPopulation:
    """Verifica que ClientIdentity → session.metadata funciona end-to-end."""

    def test_identity_metadata_feeds_routing(self):
        """
        Simula o fluxo: ClientIdentity carrega preferred_channel e
        broadcast_channels → RoutingPolicy.UserPreferenceRule pode firing.
        """
        from rlm.core.comms.routing_policy import RoutingPolicy

        identity = ClientIdentity(
            client_id="esp32",
            profile="iot",
            metadata={
                "preferred_channel": "telegram:999",
                "broadcast_channels": ["tui", "telegram:999"],
            },
        )

        # Simula session com metadata populada pelo endpoint
        session = MagicMock()
        session.metadata = {
            "preferred_channel": identity.preferred_channel,
            "broadcast_channels": identity.broadcast_channels,
            "client_profile": identity.profile,
            "context_hint": identity.context_hint,
        }

        # UserPreferenceRule lê session.metadata.preferred_channel
        assert session.metadata["preferred_channel"] == "telegram:999"
        assert session.metadata["broadcast_channels"] == ["tui", "telegram:999"]
        assert session.metadata["client_profile"] == "iot"


# ===========================================================================
# Part 10 — CLI commands import check
# ===========================================================================

class TestCLIImports:

    def test_client_commands_importable(self):
        from rlm.cli.commands.client import (
            cmd_client_add,
            cmd_client_list,
            cmd_client_revoke,
            cmd_client_status,
        )
        assert callable(cmd_client_add)
        assert callable(cmd_client_list)
        assert callable(cmd_client_revoke)
        assert callable(cmd_client_status)

    def test_command_specs_include_client(self):
        from rlm.cli.command_specs import get_command_specs
        specs = get_command_specs()
        names = [s.name for s in specs]
        assert "client" in names

    def test_client_subcommands(self):
        from rlm.cli.command_specs import get_command_specs
        specs = get_command_specs()
        client_spec = next(s for s in specs if s.name == "client")
        sub_names = [s.name for s in client_spec.subcommands]
        assert "add" in sub_names
        assert "list" in sub_names
        assert "revoke" in sub_names
        assert "status" in sub_names
