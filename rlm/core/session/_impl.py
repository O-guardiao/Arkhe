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

SESSION_SELECT_COLUMNS = (
    "session_id, client_id, user_id, status, created_at, last_active, state_dir, "
    "total_completions, total_tokens_used, last_error, metadata, delivery_context"
)
SESSION_OPERATION_EVENT = "session_operation"


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class SessionRecord:
    """Registro de metadados de uma sessão RLM (pool do servidor).

    Campos de identidade (inspirados no MsgContext do OpenClaw):
    - ``user_id``   → chave unificada de sessão (ex: ``"main"``, ``"user:123"``).
                      Todos os canais que resolvem para o mesmo user_id
                      compartilham sessão, memória e estado REPL.
    - ``client_id`` → canal de origem (*originating_channel*) do último
                      request recebido (ex: ``"telegram:123"``, ``"tui:default"``).
                      Usado pelo ChannelRegistry para rotear respostas.
    - ``delivery_context`` → rota persistida para entregas assíncronas e retomadas.
                             Não deve ser confundida com o canal de origem do request atual.
    """
    session_id: str
    client_id: str                    # Último canal de origem (originating_channel)
    user_id: str = "main"             # Chave unificada de sessão (dmScope)
    delivery_context: dict = field(default_factory=dict)  # Rota persistida de entrega
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

    @property
    def originating_channel(self) -> str:
        return self.client_id

    @originating_channel.setter
    def originating_channel(self, value: str) -> None:
        self.client_id = value

    @property
    def session_status(self) -> str:
        return self.status

    @session_status.setter
    def session_status(self, value: str) -> None:
        self.status = value


# ---------------------------------------------------------------------------
# Session Manager
# ---------------------------------------------------------------------------

class SessionManager:
    """
    Pool de sessões RLM com persistência SQLite.

    Arquitetura de sessão unificada (inspirada no OpenClaw):
    - Sessões são resolvidas por ``user_id``, não por ``client_id``.
    - ``resolve_user_id(client_id)`` mapeia canais → sessão unificada.
    - Todos os canais que resolvem para o mesmo ``user_id`` compartilham
      sessão, memory.db, estado REPL e contexto de longo prazo.
    - ``client_id`` é preservado como *originating_channel* para roteamento
      de respostas via ChannelRegistry.

    Scopes (env ``RLM_SESSION_SCOPE``):
    - ``"main"``       — TODOS os canais → sessão ``"main"`` (agente single-user)
    - ``"per-user"``   — ``"telegram:123"`` → ``"user:123"`` (multi-user)
    - ``"per-channel"``— legado, ``"telegram:123"`` → ``"telegram:123"`` (isolado)

    Usage:
        manager = SessionManager(db_path="sessions.db", state_root="./states")
        session = manager.get_or_create("telegram:12345")
        session.rlm_instance.completion("Olá!")
        manager.update_session(session)
    """

    # ── Session scope (dmScope) ───────────────────────────────────────────

    @staticmethod
    def resolve_user_id(client_id: str, scope: str | None = None) -> str:
        """Map a channel-specific client_id to a unified user_id.

        Inspired by OpenClaw's ``dmScope`` config:
        - ``"main"``       → all channels share session ``"main"``
        - ``"per-user"``   → ``"telegram:123"`` → ``"user:123"``
        - ``"per-channel"``→ ``"telegram:123"`` → ``"telegram:123"`` (legacy)
        """
        if scope is None:
            scope = os.environ.get("RLM_SESSION_SCOPE", "main")
        if scope == "main":
            return "main"
        if scope == "per-user":
            if ":" in client_id:
                _, user_part = client_id.split(":", 1)
                return f"user:{user_part}"
            return f"user:{client_id}"
        # per-channel (backward compat)
        return client_id

    # ── Constructor ───────────────────────────────────────────────────────

    def __init__(
        self,
        db_path: str = "rlm_sessions.db",
        state_root: str = "./rlm_states",
        default_rlm_kwargs: dict | None = None,
        hooks: Any | None = None,
    ):
        self.db_path = db_path
        self.state_root = os.path.abspath(state_root)
        self.default_rlm_kwargs = default_rlm_kwargs or {
            "backend": "openai",
            "backend_kwargs": {"model_name": "gpt-4o-mini"},
            "environment": "local",
            "max_iterations": 30,
            "max_depth": 3,
            "persistent": True,
            "verbose": True,
        }
        self._lock = threading.Lock()
        self._active_sessions: dict[str, SessionRecord] = {}  # session_id -> session
        self._close_callbacks: list[Callable[[SessionRecord], None]] = []
        self._hooks = hooks

        os.makedirs(self.state_root, exist_ok=True)
        self._init_db()

    def set_hook_system(self, hooks: Any | None) -> None:
        self._hooks = hooks

    # --- Database Setup ---

    def _init_db(self):
        """Create the sessions table if it doesn't exist, apply migrations."""
        with self._get_conn() as conn:
            # --- Migration first: add user_id to legacy tables that lack it --
            existing_columns: set[str] = set()
            try:
                cursor = conn.execute("PRAGMA table_info(sessions)")
                existing_columns = {row[1] for row in cursor.fetchall()}
                if existing_columns and "user_id" not in existing_columns:
                    conn.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'main'")
                    conn.execute("UPDATE sessions SET user_id = 'main' WHERE user_id = '' OR user_id IS NULL")
                    conn.commit()
                    existing_columns.add("user_id")
                    _session_log.info("DB migration: added user_id column to sessions table")
                if existing_columns and "delivery_context" not in existing_columns:
                    conn.execute("ALTER TABLE sessions ADD COLUMN delivery_context TEXT NOT NULL DEFAULT '{}' ")
                    conn.commit()
                    existing_columns.add("delivery_context")
                    _session_log.info("DB migration: added delivery_context column to sessions table")
            except Exception as exc:
                _session_log.warn(f"DB migration user_id check: {exc}")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id   TEXT PRIMARY KEY,
                    client_id    TEXT NOT NULL,
                    user_id      TEXT NOT NULL DEFAULT 'main',
                    status       TEXT DEFAULT 'idle',
                    created_at   TEXT NOT NULL,
                    last_active  TEXT NOT NULL,
                    state_dir    TEXT NOT NULL,
                    total_completions INTEGER DEFAULT 0,
                    total_tokens_used INTEGER DEFAULT 0,
                    last_error   TEXT DEFAULT '',
                    metadata     TEXT DEFAULT '{}',
                    delivery_context TEXT NOT NULL DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_client_id 
                ON sessions(client_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id
                ON sessions(user_id)
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

            # ── Camada 3: tabela clients (identidade por dispositivo) ─────
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
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_clients_active
                ON clients(active)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_clients_token_hash
                ON clients(token_hash)
            """)

            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a SQLite connection (thread-safe with check_same_thread=False)."""
        return sqlite3.connect(self.db_path, check_same_thread=False)

    # --- Core API ---

    def get_or_create(self, client_id: str, **extra_rlm_kwargs) -> SessionRecord:
        """
        Get an existing session for this user, or create a new one.

        Sessões são resolvidas por ``user_id`` (via ``resolve_user_id``),
        NÃO por ``client_id``.  Isso garante que Telegram, TUI, Discord, etc.
        compartilhem a mesma sessão quando o scope é ``"main"`` ou ``"per-user"``.

        O ``client_id`` (canal de origem) é atualizado a cada request para que
        o ChannelRegistry saiba para onde rotear a resposta.
        """
        user_id = self.resolve_user_id(client_id)

        with self._lock:
            # Check active in-memory sessions first (by user_id)
            for session in self._active_sessions.values():
                if session.user_id == user_id and session.status in ("idle", "running"):
                    self.bind_originating_channel(session, client_id, source="request")
                    return session

            # Check database for a resumable session (by user_id)
            with self._get_conn() as conn:
                row = conn.execute(
                    f"SELECT {SESSION_SELECT_COLUMNS} FROM sessions WHERE user_id = ? AND status IN ('idle', 'running') "
                    "ORDER BY last_active DESC LIMIT 1",
                    (user_id,),
                ).fetchone()

            if row:
                session = self._row_to_session(row)
                self._activate_session(session, extra_rlm_kwargs)
                self.bind_originating_channel(session, client_id, source="request")
                return session

            # Create new session
            return self._create_session(client_id, extra_rlm_kwargs, user_id=user_id)

    def get_session(self, session_id: str) -> SessionRecord | None:
        """Get a session by its ID."""
        # Check in-memory first
        if session_id in self._active_sessions:
            return self._active_sessions[session_id]

        # Check database
        with self._get_conn() as conn:
            row = conn.execute(
                f"SELECT {SESSION_SELECT_COLUMNS} FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, status: str | None = None, limit: int = 50) -> list[SessionRecord]:
        """List sessions, optionally filtered by status."""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    f"SELECT {SESSION_SELECT_COLUMNS} FROM sessions WHERE status = ? ORDER BY last_active DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {SESSION_SELECT_COLUMNS} FROM sessions ORDER BY last_active DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def update_session(self, session: SessionRecord):
        """Persist in-memory session state to database."""
        session.last_active = _now_iso()
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE sessions SET
                    client_id = ?, status = ?, last_active = ?, total_completions = ?,
                    total_tokens_used = ?, last_error = ?, metadata = ?, delivery_context = ?
                WHERE session_id = ?""",
                (
                    session.client_id,
                    session.status,
                    session.last_active,
                    session.total_completions,
                    session.total_tokens_used,
                    session.last_error,
                    json.dumps(session.metadata or {}),
                    json.dumps(session.delivery_context or {}),
                    session.session_id,
                ),
            )
            conn.commit()

    def bind_originating_channel(
        self,
        session: SessionRecord,
        client_id: str,
        *,
        source: str = "request",
        adopt_delivery_context: bool = True,
    ) -> bool:
        previous_channel = session.client_id
        changed = previous_channel != client_id
        if changed:
            session.client_id = client_id
            if session.rlm_instance is not None:
                session.rlm_instance.client_id = client_id

        delivery_changed = False
        if adopt_delivery_context:
            delivery_changed = self.maybe_refresh_delivery_context(session, client_id, source=source, persist=False)

        if changed or delivery_changed:
            self.update_session(session)

        if changed:
            payload = {
                "previous_channel": previous_channel,
                "originating_channel": client_id,
                "source": source,
            }
            self.log_event(session.session_id, "session_origin_updated", payload)
            self.log_operation(
                session.session_id,
                "session.origin",
                phase="updated",
                status="completed",
                source=source,
                payload=payload,
            )
            self._trigger_hook("session.origin.updated", session_id=session.session_id, context=payload)
            _session_log.info(
                "session channel switch",
                session_id=session.session_id,
                user_id=session.user_id,
                new_channel=client_id,
            )
        return changed or delivery_changed

    def maybe_refresh_delivery_context(
        self,
        session: SessionRecord,
        client_id: str,
        *,
        source: str = "request",
        persist: bool = True,
    ) -> bool:
        delivery_context = self.build_delivery_context(client_id, source=source)
        if not delivery_context or not delivery_context.get("replyable"):
            return False
        return self.update_delivery_context(
            session,
            delivery_context,
            source=source,
            persist=persist,
        )

    def update_delivery_context(
        self,
        session: SessionRecord,
        delivery_context: dict | None,
        *,
        source: str = "request",
        persist: bool = True,
    ) -> bool:
        normalized = self._normalize_delivery_context(delivery_context, source=source)
        if not normalized:
            return False

        previous = dict(session.delivery_context or {})
        if self._same_delivery_context(previous, normalized):
            return False

        session.delivery_context = normalized
        if persist:
            self.update_session(session)

        payload = {
            "previous_delivery_context": previous,
            "delivery_context": normalized,
            "source": source,
        }
        self.log_event(session.session_id, "session_delivery_updated", payload)
        self.log_operation(
            session.session_id,
            "session.delivery",
            phase="updated",
            status="completed",
            source=source,
            payload=payload,
        )
        self._trigger_hook("session.delivery.updated", session_id=session.session_id, context=payload)
        return True

    def transition_status(
        self,
        session: SessionRecord,
        new_status: str,
        *,
        source: str = "session_manager",
        reason: str = "",
        error: str = "",
        persist: bool = True,
        metadata: dict | None = None,
    ) -> bool:
        previous_status = session.status
        changed = previous_status != new_status
        if metadata:
            merged = dict(session.metadata or {})
            merged.update(metadata)
            session.metadata = merged
        if not changed and not reason and not error and not metadata:
            return False

        session.status = new_status
        if error:
            session.last_error = str(error)[:500]
        if persist:
            self.update_session(session)

        payload = {
            "previous_status": previous_status,
            "session_status": new_status,
            "reason": reason,
            "error": str(error or "")[:500],
            "source": source,
        }
        self.log_event(session.session_id, "session_status_changed", payload)
        self.log_operation(
            session.session_id,
            "session.status",
            phase="transition",
            status=new_status,
            source=source,
            payload=payload,
        )
        self._trigger_hook("session.status_changed", session_id=session.session_id, context=payload)
        return True

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

            self.transition_status(
                session,
                "completed",
                source="session_manager.close",
                reason="session closed",
            )
            self.log_operation(
                session.session_id,
                "session.lifecycle",
                phase="closed",
                status="completed",
                source="session_manager.close",
                payload={"delivery_context": session.delivery_context},
            )
            self._trigger_hook(
                "session.closed",
                session_id=session.session_id,
                context={
                    "originating_channel": session.client_id,
                    "delivery_context": session.delivery_context,
                },
            )
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

    def log_operation(
        self,
        session_id: str,
        operation: str,
        *,
        phase: str,
        status: str,
        source: str = "session_manager",
        payload: dict | None = None,
        operation_id: str | None = None,
        parent_operation_id: str = "",
    ) -> str:
        op_id = operation_id or uuid.uuid4().hex
        body = {
            "kind": "operation",
            "operation_id": op_id,
            "parent_operation_id": parent_operation_id,
            "operation": operation,
            "phase": phase,
            "status": status,
            "source": source,
            "recorded_at": _now_iso(),
            "payload": dict(payload or {}),
        }
        self.log_event(session_id, SESSION_OPERATION_EVENT, body)
        self._trigger_hook("session.operation", session_id=session_id, context=body)
        return op_id

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

    def get_operation_log(self, session_id: str, limit: int = 100) -> list[dict]:
        """Get structured operation log entries for a session."""
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT timestamp, payload FROM event_log "
                    "WHERE session_id = ? AND event_type = ? ORDER BY id DESC LIMIT ?",
                    (session_id, SESSION_OPERATION_EVENT, limit),
                ).fetchall()
            operations: list[dict] = []
            for timestamp, raw_payload in rows:
                payload = _loads_json_object(raw_payload)
                operations.append({"timestamp": timestamp, **payload})
            return operations
        except sqlite3.OperationalError:
            return []

    # --- Internal Helpers ---

    def _create_session(self, client_id: str, extra_rlm_kwargs: dict, *, user_id: str = "main") -> SessionRecord:
        """Create a brand new session."""
        session_id = str(uuid.uuid4())
        now = _now_iso()
        state_dir = os.path.join(self.state_root, session_id)
        os.makedirs(state_dir, exist_ok=True)
        delivery_context = self.build_delivery_context(client_id, source="session_create")
        if not delivery_context.get("replyable"):
            delivery_context = {}

        session = SessionRecord(
            session_id=session_id,
            client_id=client_id,
            user_id=user_id,
            delivery_context=delivery_context,
            status="idle",
            created_at=now,
            last_active=now,
            state_dir=state_dir,
        )

        # Persist to DB
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, client_id, user_id, status, created_at, last_active, state_dir, delivery_context) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, client_id, user_id, "idle", now, now, state_dir, json.dumps(delivery_context)),
            )
            conn.commit()

        # Create RLM instance
        self._activate_session(session, extra_rlm_kwargs)
        payload = {
            "client_id": client_id,
            "originating_channel": client_id,
            "user_id": user_id,
            "delivery_context": delivery_context,
        }
        self.log_event(session_id, "session_created", payload)
        self.log_operation(
            session_id,
            "session.lifecycle",
            phase="created",
            status="idle",
            source="session_manager.create",
            payload=payload,
        )
        self._trigger_hook("session.created", session_id=session_id, context=payload)
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
            state_dir=session.state_dir,
            client_id=session.client_id,
            user_id=session.user_id,
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
        return SessionRecord(
            session_id=row[0],
            client_id=row[1],
            user_id=row[2] or "main",
            status=row[3],
            created_at=row[4],
            last_active=row[5],
            state_dir=row[6],
            total_completions=row[7],
            total_tokens_used=row[8],
            last_error=row[9],
            metadata=_loads_json_object(row[10]),
            delivery_context=_loads_json_object(row[11]),
        )

    # --- Serialization ---

    def session_to_dict(self, session: SessionRecord) -> dict:
        """Convert a session to a JSON-safe dictionary (for API responses)."""
        return {
            "session_id": session.session_id,
            "client_id": session.client_id,
            "originating_channel": session.originating_channel,
            "user_id": session.user_id,
            "status": session.status,
            "session_status": session.session_status,
            "created_at": session.created_at,
            "last_active": session.last_active,
            "total_completions": session.total_completions,
            "total_tokens_used": session.total_tokens_used,
            "last_error": session.last_error,
            "delivery_context": session.delivery_context,
            "metadata": session.metadata,
            "has_rlm_instance": session.rlm_instance is not None,
        }

    @staticmethod
    def build_delivery_context(
        client_id: str,
        *,
        source: str = "request",
        replyable: bool | None = None,
        metadata: dict | None = None,
    ) -> dict:
        if not client_id:
            return {}
        prefix, _, target_id = client_id.partition(":")
        if replyable is None:
            replyable = False
            if prefix and target_id:
                try:
                    from rlm.plugins.channel_registry import ChannelRegistry

                    replyable = ChannelRegistry.get_adapter(prefix) is not None
                except Exception:
                    replyable = False
        context = {
            "channel": client_id,
            "transport": prefix,
            "target_id": target_id or client_id,
            "replyable": bool(replyable),
            "source": source,
            "updated_at": _now_iso(),
        }
        if metadata:
            context["metadata"] = dict(metadata)
        return context

    def _normalize_delivery_context(self, delivery_context: dict | None, *, source: str) -> dict:
        if not delivery_context:
            return {}
        if "channel" not in delivery_context:
            return self.build_delivery_context(
                str(delivery_context.get("client_id", "")),
                source=source,
                metadata=delivery_context,
            )
        normalized = dict(delivery_context)
        normalized.setdefault("source", source)
        normalized.setdefault("updated_at", _now_iso())
        normalized.setdefault("transport", str(normalized.get("channel", "")).partition(":")[0])
        channel = str(normalized.get("channel", ""))
        _, _, target_id = channel.partition(":")
        normalized.setdefault("target_id", target_id or channel)
        normalized["replyable"] = bool(normalized.get("replyable"))
        return normalized

    def _same_delivery_context(self, left: dict, right: dict) -> bool:
        compare_keys = ("channel", "transport", "target_id", "replyable")
        return all(left.get(key) == right.get(key) for key in compare_keys)

    def _trigger_hook(self, event_type: str, *, session_id: str, context: dict | None = None) -> None:
        if self._hooks is None:
            return
        try:
            self._hooks.trigger(event_type, session_id=session_id, context=context or {})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads_json_object(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}
