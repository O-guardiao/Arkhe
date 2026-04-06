"""
transcript.py — Registro de eventos de sessão (TranscriptStore).

Porta fiel de packages/sessions/src/transcript.ts para Python.

Fornece:
- ``TranscriptEventType``  — Literal com todos os tipos de evento
- ``TranscriptEvent``      — dataclass de um evento individual
- ``create_transcript_event(type, session_id, data, token_delta)``
- ``TranscriptStore``      — store in-memory append-only por sessão

A tabela ``event_log`` do SQLite em ``_impl.py`` é a camada de persistência;
este módulo fornece a API limpa em memória + a factory de eventos,
exatamente como no TypeScript.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

TranscriptEventType = Literal[
    "message_in",
    "message_out",
    "tool_call",
    "tool_result",
    "model_switch",
    "session_start",
    "session_end",
    "error",
]

_VALID_EVENT_TYPES: frozenset[str] = frozenset(
    (
        "message_in",
        "message_out",
        "tool_call",
        "tool_result",
        "model_switch",
        "session_start",
        "session_end",
        "error",
    )
)


def is_valid_event_type(value: str) -> bool:
    return value in _VALID_EVENT_TYPES


# ---------------------------------------------------------------------------
# TranscriptEvent
# ---------------------------------------------------------------------------

@dataclass
class TranscriptEvent:
    """
    Um único evento gravado no transcript de uma sessão.

    ``token_delta``, quando presente, representa o número de tokens
    consumidos ou produzidos por este evento (pode ser negativo para
    correções).

    Porta de ``TranscriptEvent`` em transcript.ts.
    """
    id: str
    session_id: str
    type: TranscriptEventType
    timestamp: str
    data: dict[str, Any]
    token_delta: int | None = field(default=None)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_transcript_event(
    type: TranscriptEventType,  # noqa: A002
    session_id: str,
    data: dict[str, Any],
    token_delta: int | None = None,
) -> TranscriptEvent:
    """
    Cria um novo ``TranscriptEvent`` com UUID gerado e timestamp atual.

    Porta de ``createTranscriptEvent()`` em transcript.ts.
    """
    return TranscriptEvent(
        id=str(uuid.uuid4()),
        session_id=session_id,
        type=type,
        timestamp=datetime.now(timezone.utc).isoformat(),
        data=data,
        token_delta=token_delta,
    )


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

class TranscriptStore:
    """
    Store in-memory append-only de transcript.

    Cada sessão mantém sua própria lista ordenada de eventos.  Para
    persistência, use a camada SQLite em ``SessionManager`` (tabela
    ``event_log``); este store é para acesso em-processo sem I/O.

    Porta de ``TranscriptStore`` em transcript.ts.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[TranscriptEvent]] = {}

    # -------------------------------------------------------------------------
    # Mutations
    # -------------------------------------------------------------------------

    def append(self, event: TranscriptEvent) -> None:
        """Adiciona um evento ao transcript da sua sessão."""
        events = self._store.setdefault(event.session_id, [])
        events.append(event)

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    def get_events(self, session_id: str) -> list[TranscriptEvent]:
        """Retorna cópia rasa de todos os eventos para *session_id* (mais antigos primeiro)."""
        return list(self._store.get(session_id, []))

    def get_latest(self, session_id: str, n: int) -> list[TranscriptEvent]:
        """
        Retorna os últimos *n* eventos para *session_id*.
        Retorna menos de *n* quando o transcript é mais curto.
        """
        events = self._store.get(session_id, [])
        return events[-n:]

    def total_tokens(self, session_id: str) -> int:
        """
        Soma todos os ``token_delta`` no transcript para *session_id*.
        Eventos sem ``token_delta`` contribuem 0.

        Porta de ``totalTokens()`` em transcript.ts.
        """
        events = self._store.get(session_id, [])
        return sum(e.token_delta or 0 for e in events)

    # -------------------------------------------------------------------------
    # Maintenance
    # -------------------------------------------------------------------------

    def clear(self, session_id: str) -> None:
        """Remove todos os eventos para *session_id* do store."""
        self._store.pop(session_id, None)

    def size(self, session_id: str | None = None) -> int:
        """Retorna número de eventos para a sessão, ou total geral se session_id=None."""
        if session_id is not None:
            return len(self._store.get(session_id, []))
        return sum(len(v) for v in self._store.values())
