"""
RLM Client Registry & Audit Log — Phase 9.4 (CiberSeg)

Per-device/client registration with:
  - SHA-256 hashed token storage (never raw)
  - Profile-based behavior injection
  - Activation/deactivation without deletion
  - Authentication audit trail

Uses the same SQLite database as ``SessionManager`` (``rlm_sessions.db``).
Thread-safe via ``threading.Lock``.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from rlm.core.security.auth import hash_token, issue_token
from rlm.core.structured_log import get_logger

_log = get_logger("client_registry")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ClientRecord:
    """A registered API client."""
    id: str
    token_hash: str
    profile: str = "default"
    active: bool = True
    permissions: list[str] = field(default_factory=lambda: ["execute", "read"])
    created_at: str = ""
    last_seen: str | None = None


@dataclass
class AuditEntry:
    """One authentication event."""
    id: int
    timestamp: str
    client_id: str
    event_type: str  # "auth_success", "auth_failure", "token_revoked", ...
    ip_address: str = ""
    detail: str = ""


# ---------------------------------------------------------------------------
# Client Registry
# ---------------------------------------------------------------------------


class ClientRegistry:
    """SQLite-backed client registry with audit logging.

    Args:
        db_path: SQLite database file (shared with SessionManager).
    """

    def __init__(self, db_path: str = "rlm_sessions.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_tables()

    # -- DB helpers ----------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_tables(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    id          TEXT PRIMARY KEY,
                    token_hash  TEXT NOT NULL,
                    profile     TEXT DEFAULT 'default',
                    permissions TEXT DEFAULT '["execute","read"]',
                    active      INTEGER DEFAULT 1,
                    created_at  TEXT NOT NULL,
                    last_seen   TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auth_audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    client_id   TEXT NOT NULL,
                    event_type  TEXT NOT NULL,
                    ip_address  TEXT DEFAULT '',
                    detail      TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_client
                ON auth_audit_log(client_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_ts
                ON auth_audit_log(timestamp)
            """)
            conn.commit()

    # -- Client CRUD ---------------------------------------------------------

    def register_client(
        self,
        client_id: str,
        profile: str = "default",
        permissions: list[str] | None = None,
    ) -> tuple[str, str]:
        """Register a new client and return ``(client_id, raw_token)``.

        The raw token is shown ONCE. It is stored as a SHA-256 hash.

        Raises:
            ValueError: If a client with this ID already exists.
        """
        import json as _json

        perms = permissions or ["execute", "read"]
        raw_token = secrets.token_urlsafe(48)
        t_hash = hash_token(raw_token)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        with self._lock, self._get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM clients WHERE id = ?", (client_id,)
            ).fetchone()
            if existing:
                raise ValueError(f"Client '{client_id}' already exists.")

            conn.execute(
                "INSERT INTO clients (id, token_hash, profile, permissions, active, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (client_id, t_hash, profile, _json.dumps(perms), now),
            )
            conn.commit()

        self._audit("client_registered", client_id, detail=f"profile={profile}")
        _log.info(f"Client registered: {client_id} profile={profile}")
        return client_id, raw_token

    def authenticate(
        self,
        raw_token: str,
        ip_address: str = "",
    ) -> ClientRecord | None:
        """Authenticate by raw token. Returns ``ClientRecord`` or ``None``.

        Updates ``last_seen`` on success.
        """
        import json as _json

        t_hash = hash_token(raw_token)

        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                "SELECT id, token_hash, profile, permissions, active, created_at, last_seen "
                "FROM clients WHERE token_hash = ? AND active = 1",
                (t_hash,),
            ).fetchone()

            if not row:
                self._audit("auth_failure", "unknown", ip_address=ip_address,
                            detail="invalid token or inactive client")
                return None

            client_id = row[0]
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "UPDATE clients SET last_seen = ? WHERE id = ?",
                (now, client_id),
            )
            conn.commit()

        self._audit("auth_success", client_id, ip_address=ip_address)

        return ClientRecord(
            id=row[0],
            token_hash=row[1],
            profile=row[2],
            active=bool(row[4]),
            permissions=_json.loads(row[3]) if row[3] else ["execute", "read"],
            created_at=row[5],
            last_seen=now,
        )

    def get_client(self, client_id: str) -> ClientRecord | None:
        """Lookup a client by ID (no auth)."""
        import json as _json

        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT id, token_hash, profile, permissions, active, created_at, last_seen "
                "FROM clients WHERE id = ?",
                (client_id,),
            ).fetchone()
        if not row:
            return None
        return ClientRecord(
            id=row[0],
            token_hash=row[1],
            profile=row[2],
            active=bool(row[4]),
            permissions=_json.loads(row[3]) if row[3] else ["execute", "read"],
            created_at=row[5],
            last_seen=row[6],
        )

    def deactivate_client(self, client_id: str) -> bool:
        """Deactivate (soft-revoke) a client. Returns True if found."""
        with self._lock, self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE clients SET active = 0 WHERE id = ?",
                (client_id,),
            )
            conn.commit()
            revoked = cursor.rowcount > 0

        if revoked:
            self._audit("client_deactivated", client_id)
            _log.info(f"Client deactivated: {client_id}")
        return revoked

    def reactivate_client(self, client_id: str) -> bool:
        """Reactivate a previously deactivated client."""
        with self._lock, self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE clients SET active = 1 WHERE id = ?",
                (client_id,),
            )
            conn.commit()
            ok = cursor.rowcount > 0

        if ok:
            self._audit("client_reactivated", client_id)
            _log.info(f"Client reactivated: {client_id}")
        return ok

    def rotate_token(self, client_id: str) -> str | None:
        """Generate a new token for an existing client. Returns raw token or None."""
        raw_token = secrets.token_urlsafe(48)
        t_hash = hash_token(raw_token)

        with self._lock, self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE clients SET token_hash = ? WHERE id = ? AND active = 1",
                (t_hash, client_id),
            )
            conn.commit()

            if cursor.rowcount == 0:
                return None

        self._audit("token_rotated", client_id)
        _log.info(f"Token rotated: {client_id}")
        return raw_token

    def list_clients(self, active_only: bool = True) -> list[ClientRecord]:
        """List registered clients."""
        import json as _json

        query = "SELECT id, token_hash, profile, permissions, active, created_at, last_seen FROM clients"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY created_at DESC"

        with self._get_conn() as conn:
            rows = conn.execute(query).fetchall()

        return [
            ClientRecord(
                id=r[0],
                token_hash=r[1],
                profile=r[2],
                active=bool(r[4]),
                permissions=_json.loads(r[3]) if r[3] else ["execute", "read"],
                created_at=r[5],
                last_seen=r[6],
            )
            for r in rows
        ]

    def issue_jwt(
        self,
        client_id: str,
        ttl_hours: float = 24,
    ) -> str | None:
        """Issue a JWT for an already-registered and active client.

        Returns the JWT string, or ``None`` if client not found/inactive.
        """
        client = self.get_client(client_id)
        if not client or not client.active:
            return None

        token = issue_token(
            client_id=client.id,
            profile=client.profile,
            permissions=client.permissions,
            ttl_hours=ttl_hours,
        )
        self._audit("jwt_issued", client_id, detail=f"ttl={ttl_hours}h")
        return token

    # -- Audit ---------------------------------------------------------------

    def _audit(
        self,
        event_type: str,
        client_id: str,
        ip_address: str = "",
        detail: str = "",
    ) -> None:
        """Write an audit log entry."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO auth_audit_log (timestamp, client_id, event_type, ip_address, detail) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (now, client_id, event_type, ip_address, detail),
                )
                conn.commit()
        except Exception as exc:
            _log.error(f"Failed to write audit log: {exc}")

    def get_audit_log(
        self,
        client_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Retrieve audit log entries."""
        query = "SELECT id, timestamp, client_id, event_type, ip_address, detail FROM auth_audit_log"
        params: list[Any] = []
        if client_id:
            query += " WHERE client_id = ?"
            params.append(client_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            AuditEntry(id=r[0], timestamp=r[1], client_id=r[2],
                       event_type=r[3], ip_address=r[4], detail=r[5])
            for r in rows
        ]
