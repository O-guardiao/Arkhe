"""
RLM Session Manager — Fase 7.1

Gerencia múltiplas sessões independentes do RLM com persistência SQLite.
Cada sessão tem seu próprio estado REPL isolado, permitindo que múltiplos
clientes (Telegram, Discord, Webhook) usem o RLM simultaneamente sem
interferir uns nos outros.
"""
import os
import uuid
import sqlite3
import json
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from rlm.core.structured_log import get_logger

_session_log = get_logger("session_manager")


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class SessionRecord:
    """Registro de metadados de uma sessão RLM (pool do servidor)."""
    session_id: str
    client_id: str                    # Quem disparou (ex: "telegram:123")
    status: str = "idle"              # idle | running | completed | aborted | error
    created_at: str = ""              # ISO timestamp
    last_active: str = ""             # ISO timestamp
    state_dir: str = ""               # Diretório para save_state/resume_state
    total_completions: int = 0        # Número de completions processadas
    total_tokens_used: int = 0        # Estimativa de tokens consumidos
    last_error: str = ""              # Último erro ocorrido
    metadata: dict = field(default_factory=dict)  # Dados extras (plugins carregados, etc.)

    # Runtime (não persistido no SQLite)
    rlm_instance: Any = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Session Manager
# ---------------------------------------------------------------------------

class SessionManager:
    """
    Pool de sessões RLM com persistência SQLite.
    
    Responsabilidades:
    - Criar/recuperar sessões por client_id
    - Persistir estado das sessões no SQLite
    - Instanciar RLM com configurações adequadas por sessão
    - Salvar/restaurar estado REPL entre reinicializações do servidor
    
    Usage:
        manager = SessionManager(db_path="sessions.db", state_root="./states")
        session = manager.get_or_create("telegram:12345")
        session.rlm_instance.completion("Olá!")
        manager.update_session(session)
    """

    def __init__(
        self,
        db_path: str = "rlm_sessions.db",
        state_root: str = "./rlm_states",
        default_rlm_kwargs: dict | None = None,
    ):
        self.db_path = db_path
        self.state_root = os.path.abspath(state_root)
        self.default_rlm_kwargs = default_rlm_kwargs or {
            "backend": "openai",
            "backend_kwargs": {"model_name": "gpt-4o-mini"},
            "environment": "local",
            "max_iterations": 30,
            "persistent": True,
            "verbose": True,
        }
        self._lock = threading.Lock()
        self._active_sessions: dict[str, SessionRecord] = {}  # session_id -> session
        self._close_callbacks: list[Callable[[SessionRecord], None]] = []

        os.makedirs(self.state_root, exist_ok=True)
        self._init_db()

    # --- Database Setup ---

    def _init_db(self):
        """Create the sessions table if it doesn't exist."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id   TEXT PRIMARY KEY,
                    client_id    TEXT NOT NULL,
                    status       TEXT DEFAULT 'idle',
                    created_at   TEXT NOT NULL,
                    last_active  TEXT NOT NULL,
                    state_dir    TEXT NOT NULL,
                    total_completions INTEGER DEFAULT 0,
                    total_tokens_used INTEGER DEFAULT 0,
                    last_error   TEXT DEFAULT '',
                    metadata     TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_client_id 
                ON sessions(client_id)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    timestamp   TEXT NOT NULL,
                    event_type  TEXT NOT NULL,
                    payload     TEXT DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a SQLite connection (thread-safe with check_same_thread=False)."""
        return sqlite3.connect(self.db_path, check_same_thread=False)

    # --- Core API ---

    def get_or_create(self, client_id: str, **extra_rlm_kwargs) -> SessionRecord:
        """
        Get an existing session for this client, or create a new one.
        
        Sessions are matched by client_id. If a non-terminal session exists
        (idle, running), it's returned. Otherwise a new one is created.
        """
        with self._lock:
            # Check active in-memory sessions first
            for session in self._active_sessions.values():
                if session.client_id == client_id and session.status in ("idle", "running"):
                    return session

            # Check database for a resumable session
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE client_id = ? AND status IN ('idle', 'running') "
                    "ORDER BY last_active DESC LIMIT 1",
                    (client_id,),
                ).fetchone()

            if row:
                session = self._row_to_session(row)
                self._activate_session(session, extra_rlm_kwargs)
                return session

            # Create new session
            return self._create_session(client_id, extra_rlm_kwargs)

    def get_session(self, session_id: str) -> SessionRecord | None:
        """Get a session by its ID."""
        # Check in-memory first
        if session_id in self._active_sessions:
            return self._active_sessions[session_id]

        # Check database
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, status: str | None = None, limit: int = 50) -> list[SessionRecord]:
        """List sessions, optionally filtered by status."""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE status = ? ORDER BY last_active DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sessions ORDER BY last_active DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def update_session(self, session: SessionRecord):
        """Persist in-memory session state to database."""
        session.last_active = _now_iso()
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE sessions SET
                    status = ?, last_active = ?, total_completions = ?,
                    total_tokens_used = ?, last_error = ?, metadata = ?
                WHERE session_id = ?""",
                (
                    session.status,
                    session.last_active,
                    session.total_completions,
                    session.total_tokens_used,
                    session.last_error,
                    json.dumps(session.metadata),
                    session.session_id,
                ),
            )
            conn.commit()

    def close_session(self, session_id: str) -> bool:
        """
        Close a session: save RLM state, release resources, mark as completed.
        Returns True if the session was found and closed.
        """
        with self._lock:
            session = self._active_sessions.get(session_id)
            if not session:
                return False

            # Save RLM state to disk
            if session.rlm_instance is not None:
                try:
                    session.rlm_instance.save_state(session.state_dir)
                except Exception as e:
                    session.last_error = f"save_state failed: {e}"
                finally:
                    session.rlm_instance.close()
                    session.rlm_instance = None

            for callback in list(self._close_callbacks):
                try:
                    callback(session)
                except Exception as e:
                    _session_log.warn(
                        f"session close callback failed for {session.session_id}: {e}"
                    )
                    if not session.last_error:
                        session.last_error = f"close callback failed: {e}"

            session.status = "completed"
            self.update_session(session)
            del self._active_sessions[session_id]
            return True

    def close_all(self):
        """Close all active sessions. Called on server shutdown."""
        session_ids = list(self._active_sessions.keys())
        for sid in session_ids:
            self.close_session(sid)

    def add_close_callback(self, callback: Callable[[SessionRecord], None]) -> None:
        """Registra callback executado ao fechar uma sessão ativa."""
        self._close_callbacks.append(callback)

    # --- Event Logging ---

    def log_event(self, session_id: str, event_type: str, payload: dict | None = None):
        """Log an event for a session (webhook received, completion started, etc.)."""
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO event_log (session_id, timestamp, event_type, payload) VALUES (?, ?, ?, ?)",
                    (session_id, _now_iso(), event_type, json.dumps(payload or {})),
                )
                conn.commit()
        except sqlite3.OperationalError:
            # event_log table may not exist yet (old DB); create and retry once.
            try:
                with self._get_conn() as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS event_log (
                            id          INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id  TEXT NOT NULL,
                            timestamp   TEXT NOT NULL,
                            event_type  TEXT NOT NULL,
                            payload     TEXT DEFAULT '{}',
                            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        )
                    """)
                    conn.execute(
                        "INSERT INTO event_log (session_id, timestamp, event_type, payload) VALUES (?, ?, ?, ?)",
                        (session_id, _now_iso(), event_type, json.dumps(payload or {})),
                    )
                    conn.commit()
            except Exception:
                pass

    def get_events(self, session_id: str, limit: int = 100) -> list[dict]:
        """Get recent events for a session."""
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT timestamp, event_type, payload FROM event_log "
                    "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            return [
                {"timestamp": r[0], "event_type": r[1], "payload": json.loads(r[2])}
                for r in rows
            ]
        except sqlite3.OperationalError:
            # event_log table may not exist in databases created by older versions.
            # Attempt to create it now and return empty.
            try:
                with self._get_conn() as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS event_log (
                            id          INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id  TEXT NOT NULL,
                            timestamp   TEXT NOT NULL,
                            event_type  TEXT NOT NULL,
                            payload     TEXT DEFAULT '{}',
                            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        )
                    """)
                    conn.commit()
            except Exception:
                pass
            return []

    # --- Internal Helpers ---

    def _create_session(self, client_id: str, extra_rlm_kwargs: dict) -> SessionRecord:
        """Create a brand new session."""
        session_id = str(uuid.uuid4())
        now = _now_iso()
        state_dir = os.path.join(self.state_root, session_id)
        os.makedirs(state_dir, exist_ok=True)

        session = SessionRecord(
            session_id=session_id,
            client_id=client_id,
            status="idle",
            created_at=now,
            last_active=now,
            state_dir=state_dir,
        )

        # Persist to DB
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, client_id, status, created_at, last_active, state_dir) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, client_id, "idle", now, now, state_dir),
            )
            conn.commit()

        # Create RLM instance
        self._activate_session(session, extra_rlm_kwargs)
        self.log_event(session_id, "session_created", {"client_id": client_id})
        return session

    def _activate_session(self, session: SessionRecord, extra_rlm_kwargs: dict):
        """Bring a session into memory with a live RLMSession instance."""
        if session.rlm_instance is not None:
            return  # Already active

        # Import here to avoid circular imports at module level
        from rlm.session import RLMSession

        # Merge defaults + per-request overrides, then extract/remap params
        raw = {**self.default_rlm_kwargs, **extra_rlm_kwargs}
        backend = raw.pop("backend", "openai")
        backend_kwargs = raw.pop("backend_kwargs", {})
        rlm_max_iterations = raw.pop("max_iterations", 4)
        raw.pop("persistent", None)   # RLMSession é sempre persistent — evita duplicate kwarg

        memory_db = os.path.join(session.state_dir, "memory.db")

        rlm_session = RLMSession(
            backend=backend,
            backend_kwargs=backend_kwargs,
            rlm_max_iterations=rlm_max_iterations,
            memory_db_path=memory_db,
            session_id=session.session_id,
            **raw,
        )

        # Tenta retomar estado REPL persistido em disco
        if os.path.exists(os.path.join(session.state_dir, "rlm_config.json")):
            try:
                rlm_session._rlm.resume_state(session.state_dir)
            except Exception:
                pass  # Fresh start se resume falhar

        session.rlm_instance = rlm_session
        session.status = "idle"
        self._active_sessions[session.session_id] = session

    def _row_to_session(self, row: tuple) -> SessionRecord:
        """Convert a SQLite row to a SessionRecord dataclass."""
        return SessionRecord(
            session_id=row[0],
            client_id=row[1],
            status=row[2],
            created_at=row[3],
            last_active=row[4],
            state_dir=row[5],
            total_completions=row[6],
            total_tokens_used=row[7],
            last_error=row[8],
            metadata=json.loads(row[9]) if row[9] else {},
        )

    # --- Serialization ---

    def session_to_dict(self, session: SessionRecord) -> dict:
        """Convert a session to a JSON-safe dictionary (for API responses)."""
        return {
            "session_id": session.session_id,
            "client_id": session.client_id,
            "status": session.status,
            "created_at": session.created_at,
            "last_active": session.last_active,
            "total_completions": session.total_completions,
            "total_tokens_used": session.total_tokens_used,
            "last_error": session.last_error,
            "metadata": session.metadata,
            "has_rlm_instance": session.rlm_instance is not None,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
