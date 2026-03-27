"""
Knowledge Base tools — exposes the Global Knowledge Base (cross-session
persistent memory) to the REPL namespace.

Progressive retrieval:
  - kb_search(query)          → titles + summaries (Tier 1+2)
  - kb_get_full_context(id)   → full context for one document (Tier 3)
  - kb_status()               → stats about the KB
"""
from __future__ import annotations

from typing import Any


def get_kb_tools(rlm_session: Any) -> dict[str, Any]:
    """
    Build dict of KB callables for REPL injection.

    Args:
        rlm_session: RLMSession instance with .kb property.

    Returns:
        Dict of tool name -> callable.
    """

    def kb_search(query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Search the cross-session Knowledge Base. Returns titles + summaries (no full context)."""
        kb = getattr(rlm_session, "kb", None)
        if kb is None:
            return [{"error": "Knowledge Base not available"}]
        try:
            results = kb.search_hybrid(query, limit=top_k)
            # Progressive: return title + summary only, not full_context
            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "summary": r.get("summary", ""),
                    "importance": r.get("importance", 0),
                    "score": round(r.get("hybrid_score", 0), 4),
                    "domain": r.get("domain", ""),
                    "tags": r.get("tags", ""),
                }
                for r in results
            ]
        except Exception as e:
            return [{"error": str(e)}]

    def kb_get_full_context(doc_id: str) -> dict[str, Any]:
        """Get the full context of a specific KB document by ID. Use after kb_search."""
        kb = getattr(rlm_session, "kb", None)
        if kb is None:
            return {"error": "Knowledge Base not available"}
        try:
            doc = kb.get_document(doc_id)
            if doc is None:
                return {"error": f"Document {doc_id} not found"}
            return {
                "id": doc["id"],
                "title": doc["title"],
                "summary": doc.get("summary", ""),
                "full_context": doc.get("full_context", ""),
                "domain": doc.get("domain", ""),
                "tags": doc.get("tags", ""),
                "importance": doc.get("importance", 0),
                "source_session": doc.get("source_session", ""),
                "created_at": doc.get("created_at", ""),
                "updated_at": doc.get("updated_at", ""),
            }
        except Exception as e:
            return {"error": str(e)}

    def kb_status() -> dict[str, Any]:
        """Return stats about the cross-session Knowledge Base."""
        kb = getattr(rlm_session, "kb", None)
        if kb is None:
            return {"available": False}
        try:
            stats = kb.stats()
            stats["available"] = True
            return stats
        except Exception as e:
            return {"available": True, "error": str(e)}

    return {
        "kb_search": kb_search,
        "kb_get_full_context": kb_get_full_context,
        "kb_status": kb_status,
    }
