"""
Session memory tools — exposes RLMSession's persistent conversational memory
to the REPL namespace.

These wrap MultiVectorMemory (session-scoped long-term memory) — NOT workspace
memory (RLMMemory). The distinction matters:

  - session_memory_*  → conversational memories curated by the MINI agent
  - memory_*          → workspace/codebase files and analyses (RLMMemory)
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any


def get_session_memory_tools(rlm_session: Any) -> dict[str, Any]:
    """
    Build dict of session memory callables for REPL injection.

    Args:
        rlm_session: RLMSession instance with .memory and .session_id properties.

    Returns:
        Dict of tool name -> callable.
    """

    def session_memory_search(query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Search long-term session memories by relevance (hybrid FTS + vector + temporal decay)."""
        mem = getattr(rlm_session, "memory", None)
        if mem is None:
            return []
        sid = getattr(rlm_session, "session_id", "")
        try:
            return mem.search_hybrid(query, limit=top_k, session_id=sid, temporal_decay=True)
        except Exception as e:
            return [{"error": str(e)}]

    def session_memory_status() -> dict[str, Any]:
        """Return quick stats about the session memory store."""
        mem = getattr(rlm_session, "memory", None)
        if mem is None:
            return {"available": False}
        sid = getattr(rlm_session, "session_id", "")
        try:
            with closing(sqlite3.connect(mem.db_path)) as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM memory_chunks WHERE session_id = ? AND is_deprecated = 0",
                    (sid,),
                ).fetchone()[0]
                deprecated = conn.execute(
                    "SELECT COUNT(*) FROM memory_chunks WHERE session_id = ? AND is_deprecated = 1",
                    (sid,),
                ).fetchone()[0]
                edges = conn.execute("SELECT COUNT(*) FROM memory_edges").fetchone()[0]
            return {
                "available": True,
                "session_id": sid,
                "active_chunks": total,
                "deprecated_chunks": deprecated,
                "edges": edges,
            }
        except Exception as e:
            return {"available": True, "error": str(e)}

    def session_memory_recent(limit: int = 10) -> list[dict[str, Any]]:
        """Return the N most recently added session memories (newest first)."""
        mem = getattr(rlm_session, "memory", None)
        if mem is None:
            return []
        sid = getattr(rlm_session, "session_id", "")
        try:
            with closing(sqlite3.connect(mem.db_path)) as conn:
                rows = conn.execute(
                    "SELECT id, content, importance_score, timestamp "
                    "FROM memory_chunks "
                    "WHERE session_id = ? AND is_deprecated = 0 "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (sid, limit),
                ).fetchall()
            return [
                {"id": r[0], "content": r[1], "importance": r[2], "timestamp": r[3]}
                for r in rows
            ]
        except Exception as e:
            return [{"error": str(e)}]

    return {
        "session_memory_search": session_memory_search,
        "session_memory_status": session_memory_status,
        "session_memory_recent": session_memory_recent,
    }
