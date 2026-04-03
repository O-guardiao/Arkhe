"""
rlm.core.auth — Camada 3: Identidade por dispositivo/cliente.

Cada dispositivo (ESP32, iPhone, PC, bot) registra-se com um token
SHA-256 na tabela ``clients`` do SQLite.  A autenticação é feita
comparando o hash do token recebido com o armazenado — o token em
claro nunca é persistido.

Fluxo:
  1. ``register_client()`` → gera token, grava hash, retorna token em claro (única vez)
  2. ``authenticate_client()`` → recebe token raw, verifica hash, retorna ``ClientIdentity``
  3. ``revoke_client()`` → marca ``active=0`` (sem DELETE, para auditoria)

ClientIdentity carrega perfil, context_hint e permissions, permitindo
que endpoints e RoutingPolicy ajustem comportamento por dispositivo.

Referência: docs/arquitetura-config-multidevice.md §7 (Camada 3).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from rlm.core.structured_log import get_logger

_auth_log = get_logger("auth")


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ClientIdentity:
    """Identidade autenticada de um cliente/dispositivo."""
    client_id: str
    profile: str
    context_hint: str = ""
    permissions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def preferred_channel(self) -> str | None:
        """Canal preferido do cliente, extraído de metadata."""
        return self.metadata.get("preferred_channel")

    @property
    def broadcast_channels(self) -> list[str]:
        """Canais de broadcast configurados para este cliente."""
        return self.metadata.get("broadcast_channels", [])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hash_token(raw_token: str) -> str:
    """SHA-256 do token. Determinístico, sem salt (lookup por hash)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_client(
    db_path: str,
    client_id: str,
    profile: str = "default",
    description: str = "",
    context_hint: str = "",
    permissions: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """
    Registra um novo cliente e retorna o token em claro (exibido uma única vez).

    Raises:
        ValueError: se ``client_id`` já existe (ativo ou revogado).
    """
    raw_token = f"rlm_{secrets.token_hex(24)}"
    token_hash = _hash_token(raw_token)
    perms_json = json.dumps(permissions or [])
    meta_json = json.dumps(metadata or {})
    now = _now_iso()

    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        existing = conn.execute(
            "SELECT id, active FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
        if existing:
            status = "ativo" if existing[1] else "revogado"
            raise ValueError(
                f"Cliente '{client_id}' já existe ({status}). "
                "Use revoke + register para recriar."
            )

        conn.execute(
            """INSERT INTO clients
               (id, token_hash, profile, description, context_hint,
                permissions, active, created_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (client_id, token_hash, profile, description, context_hint,
             perms_json, now, meta_json),
        )
        conn.commit()

    _auth_log.info(f"Client registered: {client_id} (profile={profile})")
    return raw_token


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate_client(db_path: str, raw_token: str) -> ClientIdentity | None:
    """
    Verifica token contra tabela ``clients``.

    Retorna ``ClientIdentity`` se válido e ativo, ``None`` caso contrário.
    Atualiza ``last_seen`` automaticamente.
    """
    if not raw_token:
        return None

    token_hash = _hash_token(raw_token)

    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        row = conn.execute(
            """SELECT id, profile, context_hint, permissions, metadata
               FROM clients
               WHERE token_hash = ? AND active = 1""",
            (token_hash,),
        ).fetchone()

        if row is None:
            return None

        client_id, profile, context_hint, perms_json, meta_json = row
        permissions = json.loads(perms_json or "[]")
        metadata = json.loads(meta_json or "{}")

        conn.execute(
            "UPDATE clients SET last_seen = ? WHERE id = ?",
            (_now_iso(), client_id),
        )
        conn.commit()

    return ClientIdentity(
        client_id=client_id,
        profile=profile,
        context_hint=context_hint,
        permissions=permissions,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Revocation & Queries
# ---------------------------------------------------------------------------

def revoke_client(db_path: str, client_id: str) -> bool:
    """Marca cliente como inativo. Retorna True se encontrado e revogado."""
    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        cursor = conn.execute(
            "UPDATE clients SET active = 0 WHERE id = ? AND active = 1",
            (client_id,),
        )
        conn.commit()
        revoked = cursor.rowcount > 0

    if revoked:
        _auth_log.info(f"Client revoked: {client_id}")
    return revoked


def list_clients(
    db_path: str,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Lista clientes registrados como dicts."""
    where = "WHERE active = 1" if active_only else ""
    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT id, profile, description, context_hint, permissions, "
            f"active, created_at, last_seen, metadata FROM clients {where} "
            f"ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_client_status(db_path: str, client_id: str) -> dict[str, Any] | None:
    """Retorna status detalhado de um cliente específico."""
    with sqlite3.connect(db_path, check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Hybrid auth: try client token first, fallback to legacy global token
# ---------------------------------------------------------------------------

def authenticate_or_legacy(
    db_path: str,
    raw_token: str,
    legacy_tokens: tuple[str, ...],
) -> ClientIdentity | None:
    """
    Tenta autenticar via tabela ``clients`` primeiro.
    Se falhar, verifica contra tokens globais legados (RLM_WS_TOKEN, etc.).

    Quando autenticado via token legado, retorna ClientIdentity com
    client_id='legacy' e profile='default' — compatível mas sem
    identidade granular.

    Isso garante backward compatibility durante a migração.
    """
    # 1. Try per-device auth (graceful if DB/table missing)
    try:
        identity = authenticate_client(db_path, raw_token)
        if identity is not None:
            return identity
    except Exception:
        # DB sem tabela clients, path inválido, etc. — fallback silencioso
        pass

    # 2. Fallback to legacy global tokens
    if not raw_token or not legacy_tokens:
        return None

    import hmac
    raw_bytes = raw_token.encode("utf-8")
    for legacy in legacy_tokens:
        if legacy and hmac.compare_digest(raw_bytes, legacy.encode("utf-8")):
            _auth_log.info("Auth via legacy global token (no device identity)")
            return ClientIdentity(
                client_id="legacy",
                profile="default",
                context_hint="",
                permissions=[],
                metadata={},
            )

    return None
