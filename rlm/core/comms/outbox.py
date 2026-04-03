"""
Outbox — Fila persistente (SQLite) para entrega assíncrona de mensagens.

Implementa o Transactional Outbox Pattern: ao invés de enviar direto
pelo adapter (fire-and-forget), persiste o Envelope no banco.
O DeliveryWorker drena em batches com retry e backoff exponencial.

Segue o padrão RLM de DDL inline (CREATE TABLE IF NOT EXISTS) sem
sistema de migração formal — mesmo padrão de SessionManager._init_db().

Usa o MESMO arquivo de banco (``rlm_sessions.db``) para evitar
proliferação de arquivos SQLite. O Outbox abre sua própria conexão
com ``check_same_thread=False``.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from rlm.core.comms.envelope import Direction, Envelope, MessageType
from rlm.core.structured_log import get_logger

_log = get_logger("outbox")


class OutboxStore:
    """
    CRUD sobre a tabela ``outbox`` no SQLite.

    Thread-safe via Lock interno. Todas as operações são síncronas
    (consistente com o resto do codebase que usa ``sqlite3`` diretamente).
    """

    def __init__(self, db_path: str = "rlm_sessions.db") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    # ── Schema ────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id              TEXT PRIMARY KEY,
                    correlation_id  TEXT,
                    target_channel  TEXT NOT NULL,
                    target_id       TEXT NOT NULL,
                    target_client_id TEXT NOT NULL,
                    message_type    TEXT DEFAULT 'text',
                    payload         TEXT NOT NULL,
                    priority        INTEGER DEFAULT 0,
                    status          TEXT DEFAULT 'pending',
                    attempts        INTEGER DEFAULT 0,
                    max_retries     INTEGER DEFAULT 3,
                    next_attempt_at TEXT,
                    created_at      TEXT NOT NULL,
                    delivered_at    TEXT,
                    last_error      TEXT DEFAULT '',
                    session_id      TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_outbox_status_next
                ON outbox(status, next_attempt_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_outbox_target
                ON outbox(target_client_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_outbox_correlation
                ON outbox(correlation_id)
            """)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Enqueue ───────────────────────────────────────────────────────────

    def enqueue(self, envelope: Envelope, session_id: str | None = None) -> str:
        """
        Persiste envelope no outbox com status ``pending``.
        Retorna o id do envelope.
        """
        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(
            {
                "text": envelope.text,
                "media_url": envelope.media_url,
                "media_mime": envelope.media_mime,
                "metadata": envelope.metadata,
            },
            ensure_ascii=False,
        )
        with self._lock, self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO outbox
                    (id, correlation_id, target_channel, target_id,
                     target_client_id, message_type, payload, priority,
                     status, attempts, max_retries, next_attempt_at,
                     created_at, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
                """,
                (
                    envelope.id,
                    envelope.correlation_id,
                    envelope.target_channel or "",
                    envelope.target_id or "",
                    envelope.target_client_id or envelope.delivery_target,
                    envelope.message_type.value,
                    payload,
                    envelope.priority,
                    envelope.max_retries,
                    now,
                    now,
                    session_id,
                ),
            )
            conn.commit()
        _log.info(
            "Enqueued",
            envelope_id=envelope.id,
            target=envelope.delivery_target,
        )
        return envelope.id

    # ── Fetch pending ─────────────────────────────────────────────────────

    def fetch_pending(self, batch_size: int = 20) -> list[dict[str, Any]]:
        """
        Retorna até ``batch_size`` registros prontos para entrega.
        Ordena por prioridade DESC, created_at ASC.
        Atualiza status para ``delivering`` atomicamente.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM outbox
                WHERE status = 'pending'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (now, batch_size),
            ).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE outbox SET status='delivering' WHERE id IN ({placeholders})",
                    ids,
                )
                conn.commit()
        return [dict(r) for r in rows]

    # ── Mark delivered ────────────────────────────────────────────────────

    def mark_delivered(self, envelope_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_conn() as conn:
            conn.execute(
                "UPDATE outbox SET status='delivered', delivered_at=? WHERE id=?",
                (now, envelope_id),
            )
            conn.commit()

    # ── Mark failed (retry ou DLQ) ────────────────────────────────────────

    def mark_failed(
        self,
        envelope_id: str,
        error: str,
        backoff_base: int = 2,
    ) -> str:
        """
        Incrementa attempts. Se >= max_retries → DLQ, senão → pending
        com backoff exponencial.

        Retorna o novo status: ``'pending'`` ou ``'dlq'``.
        """
        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                "SELECT attempts, max_retries FROM outbox WHERE id=?",
                (envelope_id,),
            ).fetchone()
            if not row:
                return "not_found"

            attempts = row["attempts"] + 1
            max_r = row["max_retries"]

            if attempts >= max_r:
                conn.execute(
                    """
                    UPDATE outbox
                    SET status='dlq', attempts=?, last_error=?
                    WHERE id=?
                    """,
                    (attempts, error, envelope_id),
                )
                conn.commit()
                _log.warn(
                    "DLQ",
                    envelope_id=envelope_id,
                    attempts=attempts,
                    error=error,
                )
                return "dlq"

            backoff_s = min(backoff_base ** attempts, 300)
            next_at = (
                datetime.now(timezone.utc) + timedelta(seconds=backoff_s)
            ).isoformat()
            conn.execute(
                """
                UPDATE outbox
                SET status='pending', attempts=?, last_error=?,
                    next_attempt_at=?
                WHERE id=?
                """,
                (attempts, error, next_at, envelope_id),
            )
            conn.commit()
            _log.info(
                "Retry scheduled",
                envelope_id=envelope_id,
                attempt=attempts,
                next_at=next_at,
            )
            return "pending"

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        """Contagem por status."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM outbox GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}
