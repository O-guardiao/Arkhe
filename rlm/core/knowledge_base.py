"""
knowledge_base.py — Global Knowledge Base (Cross-Session Persistent Memory)

Banco de memória persistente que sobrevive entre sessões.
Armazena documentos estruturados em 3 camadas de profundidade:
  - title:        ~10-20 tokens — identificação rápida
  - summary:      ~50-150 tokens — contexto suficiente para 80% dos casos
  - full_context: ~500-5000 tokens — detalhes completos, sob demanda via tool

Retrieval progressivo: o RLM recebe títulos + summaries por padrão.
Se precisar de mais, chama kb_get_full_context(doc_id).

Inspiração acadêmica:
  - Generative Agents (Park et al., 2023): score tripartito
  - MemGPT (Packer et al., 2023): paginação de memória em camadas
  - A-MEM: memory evolution com typed edges

Localização do banco: rlm_states/global/knowledge_base.db
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import closing
from typing import Any, Dict, List, Optional

from rlm.core.structured_log import get_logger

_log = get_logger("knowledge_base")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

KB_SCORE_THRESHOLD: float = 0.40
"""Score mínimo para um documento KB ser considerado relevante."""

KB_IMPORTANCE_WEIGHT: float = 0.30
KB_RECENCY_WEIGHT: float = 0.20
KB_RELEVANCE_WEIGHT: float = 0.50
"""Pesos do score tripartito para KB — relevância pesa mais porque
   documentos KB já foram curados pelo consolidador."""

KB_RECENCY_HALF_LIFE_DAYS: float = 30.0
"""Meia-vida em dias para recência no KB — mais lenta que sessão (7d)
   porque conhecimento consolidado envelhece mais devagar."""

KB_RETRIEVAL_LIMIT: int = 10
"""Máximo de candidatos a buscar antes do score gate."""

TOKENS_PER_CHAR: float = 0.25
"""Estimativa de tokens por caractere (1 token ≈ 4 chars)."""


def _get_embedding_client() -> Any:
    """Retorna cliente OpenAI para embeddings ou None."""
    try:
        import openai
        if os.getenv("OPENAI_API_KEY"):
            return openai.OpenAI()
    except Exception:
        pass
    return None


def _get_embedding(text: str, client: Any = None, model: str = "text-embedding-3-small") -> List[float]:
    """Gera embedding para texto. Retorna [] se falhar."""
    if client is None:
        client = _get_embedding_client()
    if client is None or not text:
        return []
    try:
        response = client.embeddings.create(input=[text], model=model)
        return response.data[0].embedding
    except Exception as exc:
        _log.warn(f"Embedding falhou: {exc}")
        return []


def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Cosine similarity entre dois vetores."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


# ---------------------------------------------------------------------------
# GlobalKnowledgeBase
# ---------------------------------------------------------------------------

class GlobalKnowledgeBase:
    """
    Banco de conhecimento global persistente entre sessões.

    Armazena documentos com 3 camadas: title, summary, full_context.
    Busca usa embedding do título + summary (RRF de FTS5 + cosine similarity).
    """

    def __init__(self, db_path: str, embedding_model: str = "text-embedding-3-small"):
        self.db_path = db_path
        self.embedding_model = embedding_model
        self._client = _get_embedding_client()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kb_documents (
                    id              TEXT PRIMARY KEY,
                    title           TEXT NOT NULL,
                    summary         TEXT NOT NULL,
                    full_context    TEXT NOT NULL DEFAULT '',
                    tags            TEXT DEFAULT '[]',
                    domain          TEXT DEFAULT 'general',
                    importance      REAL DEFAULT 0.5,
                    status          TEXT DEFAULT 'active',
                    superseded_by   TEXT,
                    source_sessions TEXT DEFAULT '[]',
                    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                    embedding_title TEXT DEFAULT '[]',
                    embedding_summary TEXT DEFAULT '[]'
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
                    id UNINDEXED,
                    title,
                    summary,
                    tags,
                    tokenize="porter"
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kb_edges (
                    id          TEXT PRIMARY KEY,
                    from_id     TEXT NOT NULL,
                    to_id       TEXT NOT NULL,
                    edge_type   TEXT NOT NULL,
                    confidence  REAL DEFAULT 1.0,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_domain ON kb_documents(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_status ON kb_documents(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_importance ON kb_documents(importance DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_edges_from ON kb_edges(from_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_edges_to ON kb_edges(to_id)")
            conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_document(
        self,
        *,
        title: str,
        summary: str,
        full_context: str = "",
        tags: Optional[List[str]] = None,
        domain: str = "general",
        importance: float = 0.5,
        source_sessions: Optional[List[str]] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        """Adiciona documento ao KB. Retorna o ID."""
        doc_id = doc_id or str(uuid.uuid4())
        tags = tags or []
        source_sessions = source_sessions or []
        importance = max(0.0, min(1.0, float(importance)))

        emb_title = _get_embedding(title, self._client, self.embedding_model)
        emb_summary = _get_embedding(summary, self._client, self.embedding_model)

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO kb_documents
                    (id, title, summary, full_context, tags, domain, importance,
                     status, source_sessions, embedding_title, embedding_summary,
                     updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                doc_id, title, summary, full_context,
                json.dumps(tags), domain, importance,
                json.dumps(source_sessions),
                json.dumps(emb_title), json.dumps(emb_summary),
            ))
            # FTS index
            conn.execute("DELETE FROM kb_fts WHERE id = ?", (doc_id,))
            conn.execute(
                "INSERT INTO kb_fts (id, title, summary, tags) VALUES (?, ?, ?, ?)",
                (doc_id, title, summary, " ".join(tags)),
            )
            conn.commit()

        _log.debug(f"KB: added doc '{doc_id}' — '{title[:60]}' (importance={importance:.2f})")
        return doc_id

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Recupera documento completo pelo ID."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM kb_documents WHERE id = ?", (doc_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_dict(row)

    def update_document(
        self,
        doc_id: str,
        *,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        full_context: Optional[str] = None,
        tags: Optional[List[str]] = None,
        domain: Optional[str] = None,
        importance: Optional[float] = None,
        source_sessions: Optional[List[str]] = None,
    ) -> bool:
        """Atualiza campos de um documento existente. Retorna True se encontrou."""
        existing = self.get_document(doc_id)
        if existing is None:
            return False

        new_title = title if title is not None else existing["title"]
        new_summary = summary if summary is not None else existing["summary"]
        new_full_context = full_context if full_context is not None else existing["full_context"]
        new_tags = tags if tags is not None else existing["tags"]
        new_domain = domain if domain is not None else existing["domain"]
        new_importance = importance if importance is not None else existing["importance"]
        new_sessions = source_sessions if source_sessions is not None else existing["source_sessions"]

        if isinstance(new_importance, (int, float)):
            new_importance = max(0.0, min(1.0, float(new_importance)))

        emb_title = (
            _get_embedding(new_title, self._client, self.embedding_model)
            if title is not None else json.loads(existing.get("embedding_title_raw", "[]"))
        )
        emb_summary = (
            _get_embedding(new_summary, self._client, self.embedding_model)
            if summary is not None else json.loads(existing.get("embedding_summary_raw", "[]"))
        )

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("""
                UPDATE kb_documents SET
                    title = ?, summary = ?, full_context = ?, tags = ?,
                    domain = ?, importance = ?, source_sessions = ?,
                    embedding_title = ?, embedding_summary = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                new_title, new_summary, new_full_context,
                json.dumps(new_tags), new_domain, new_importance,
                json.dumps(new_sessions),
                json.dumps(emb_title), json.dumps(emb_summary),
                doc_id,
            ))
            conn.execute("DELETE FROM kb_fts WHERE id = ?", (doc_id,))
            conn.execute(
                "INSERT INTO kb_fts (id, title, summary, tags) VALUES (?, ?, ?, ?)",
                (doc_id, new_title, new_summary, " ".join(new_tags if isinstance(new_tags, list) else [])),
            )
            conn.commit()
        return True

    def deprecate_document(self, doc_id: str, superseded_by: Optional[str] = None) -> bool:
        """Marca documento como superseded/deprecated."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            if superseded_by:
                conn.execute(
                    "UPDATE kb_documents SET status = 'superseded', superseded_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (superseded_by, doc_id),
                )
            else:
                conn.execute(
                    "UPDATE kb_documents SET status = 'deprecated', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (doc_id,),
                )
            affected = conn.total_changes
            conn.commit()
        return affected > 0

    def list_documents(
        self,
        *,
        domain: Optional[str] = None,
        status: str = "active",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Lista documentos (sem full_context para economia de memória)."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            sql = "SELECT id, title, summary, tags, domain, importance, status, source_sessions, created_at, updated_at FROM kb_documents WHERE status = ?"
            params: list = [status]
            if domain:
                sql += " AND domain = ?"
                params.append(domain)
            sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Search (Hybrid: FTS5 + Cosine Similarity + RRF)
    # ------------------------------------------------------------------

    def search_hybrid(
        self,
        query: str,
        *,
        limit: int = KB_RETRIEVAL_LIMIT,
        status: str = "active",
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Busca híbrida: FTS5 (BM25 em title+summary+tags) + cosine similarity
        nos embeddings de title e summary, combinados via RRF.

        Retorna documentos com hybrid_score, sem full_context (economia).
        """
        query_emb = _get_embedding(query, self._client, self.embedding_model)

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row

            # 1. FTS search
            words = [w for w in query.replace("'", "").replace('"', "").split() if w.isalnum()]
            fts_query = " OR ".join(words) if words else ""
            fts_ranks: dict[str, int] = {}
            if fts_query:
                try:
                    rows = conn.execute(
                        "SELECT id FROM kb_fts WHERE kb_fts MATCH ? LIMIT 20",
                        (fts_query,),
                    ).fetchall()
                    for idx, row in enumerate(rows):
                        fts_ranks[row["id"]] = idx + 1
                except sqlite3.OperationalError:
                    pass

            # 2. Vector search (title + summary embeddings)
            sql_filter = "SELECT * FROM kb_documents WHERE status = ?"
            params: list = [status]
            if domain:
                sql_filter += " AND domain = ?"
                params.append(domain)
            all_docs = conn.execute(sql_filter, params).fetchall()

            doc_data: dict[str, sqlite3.Row] = {}
            vec_scores: list[tuple[str, float]] = []

            for row in all_docs:
                did = row["id"]
                doc_data[did] = row
                if query_emb:
                    emb_t = json.loads(row["embedding_title"] or "[]")
                    emb_s = json.loads(row["embedding_summary"] or "[]")
                    # Média ponderada: summary pesa mais (tem mais info)
                    score_t = _cosine_similarity(query_emb, emb_t) * 0.35
                    score_s = _cosine_similarity(query_emb, emb_s) * 0.65
                    vec_scores.append((did, score_t + score_s))

            vec_scores.sort(key=lambda x: x[1], reverse=True)
            vec_ranks = {did: idx + 1 for idx, (did, _) in enumerate(vec_scores[:20])}

            # 3. RRF + temporal decay
            k = 60
            now_ts = time.time()
            decay_lambda = math.log(2) / KB_RECENCY_HALF_LIFE_DAYS

            final_scores: dict[str, float] = {}
            for did in doc_data:
                score_fts = 1.0 / (k + fts_ranks[did]) if did in fts_ranks else 0.0
                score_vec = 1.0 / (k + vec_ranks[did]) if did in vec_ranks else 0.0
                total = score_fts + score_vec
                if total > 0:
                    ts_str = doc_data[did]["updated_at"] or ""
                    try:
                        import datetime as _dt
                        ts_dt = _dt.datetime.fromisoformat(ts_str.replace(" ", "T"))
                        age_days = (now_ts - ts_dt.timestamp()) / 86400.0
                        total *= math.exp(-decay_lambda * max(age_days, 0.0))
                    except Exception:
                        pass
                    final_scores[did] = total

            sorted_docs = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)[:limit]

            results = []
            for did, score in sorted_docs:
                row = doc_data[did]
                tags_raw = row["tags"]
                sessions_raw = row["source_sessions"]
                results.append({
                    "id": did,
                    "title": row["title"],
                    "summary": row["summary"],
                    "tags": json.loads(tags_raw) if tags_raw else [],
                    "domain": row["domain"],
                    "importance": float(row["importance"] or 0.5),
                    "status": row["status"],
                    "source_sessions": json.loads(sessions_raw) if sessions_raw else [],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "hybrid_score": round(score, 6),
                })
            return results

    # ------------------------------------------------------------------
    # Score tripartito para KB
    # ------------------------------------------------------------------

    def score_tripartite(self, doc: Dict[str, Any], *, now_ts: Optional[float] = None) -> float:
        """Score tripartito adaptado para documentos KB."""
        if now_ts is None:
            now_ts = time.time()

        relevance = min(1.0, doc.get("hybrid_score", 0.0) / 0.05) if doc.get("hybrid_score", 0) > 0 else 0.0
        importance = max(0.0, min(1.0, float(doc.get("importance", 0.5))))

        recency = 1.0
        ts_str = doc.get("updated_at", "")
        if ts_str:
            try:
                import datetime as _dt
                ts_dt = _dt.datetime.fromisoformat(ts_str.replace(" ", "T"))
                age_days = (now_ts - ts_dt.timestamp()) / 86400.0
                decay_lambda = math.log(2) / KB_RECENCY_HALF_LIFE_DAYS
                recency = math.exp(-decay_lambda * max(age_days, 0.0))
            except Exception:
                recency = 1.0

        return round(
            KB_RECENCY_WEIGHT * recency
            + KB_IMPORTANCE_WEIGHT * importance
            + KB_RELEVANCE_WEIGHT * relevance,
            6,
        )

    # ------------------------------------------------------------------
    # Edges (grafo de conhecimento)
    # ------------------------------------------------------------------

    def add_edge(self, from_id: str, to_id: str, edge_type: str, confidence: float = 1.0) -> str:
        """Adiciona aresta entre dois documentos."""
        edge_id = str(uuid.uuid4())
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO kb_edges (id, from_id, to_id, edge_type, confidence) VALUES (?, ?, ?, ?, ?)",
                (edge_id, from_id, to_id, edge_type, confidence),
            )
            conn.commit()
        return edge_id

    def get_related(self, doc_id: str) -> List[Dict[str, Any]]:
        """Retorna documentos relacionados via edges."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT e.edge_type, e.confidence, d.id, d.title, d.summary
                FROM kb_edges e JOIN kb_documents d ON (
                    (e.from_id = ? AND e.to_id = d.id) OR
                    (e.to_id = ? AND e.from_id = d.id)
                )
                WHERE d.status = 'active'
            """, (doc_id, doc_id)).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Retorna estatísticas do KB."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM kb_documents").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM kb_documents WHERE status = 'active'").fetchone()[0]
            edges = conn.execute("SELECT COUNT(*) FROM kb_edges").fetchone()[0]
            domains = conn.execute("SELECT domain, COUNT(*) as cnt FROM kb_documents WHERE status = 'active' GROUP BY domain ORDER BY cnt DESC").fetchall()
            return {
                "total_documents": total,
                "active_documents": active,
                "total_edges": edges,
                "domains": {d[0]: d[1] for d in domains},
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for key in ("tags", "source_sessions"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
        # Preserve raw embeddings for update_document
        d["embedding_title_raw"] = d.pop("embedding_title", "[]")
        d["embedding_summary_raw"] = d.pop("embedding_summary", "[]")
        return d

    def close(self) -> None:
        """Noop — cada operação abre e fecha conexão (thread-safe)."""
        pass
