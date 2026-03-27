import sqlite3
import json
import os
import uuid
import math
from typing import Any, Callable, List, Dict, Optional
import time

try:
    import openai
except ImportError:
    openai = None

from rlm.core.structured_log import get_logger

mem_log = get_logger("memory")

# ---------------------------------------------------------------------------
# Phase 9.3 (Gap A): Memory injection guard
# ---------------------------------------------------------------------------

def _sanitize_memory_chunk(content: str, chunk_id: str = "") -> str:
    """
    Scans a memory chunk for prompt injection patterns before exposing to the LLM.

    Strategy:
    - Clean content  → returned unchanged (zero overhead on the happy path).
    - HIGH severity  → content quarantined; sanitized version (patterns stripped)
                       returned with a quarantine header so the LLM knows something
                       was removed — it can still use the rest of the chunk.
    - MEDIUM / LOW   → warning prefix prepended; sanitized content retained.

    Called at READ TIME only (search_hybrid, get_memory).  The SQLite database is
    never modified — stored content is always the original, unaltered text.
    """
    if not content:
        return content
    try:
        from rlm.core.security import auditor as _sec_auditor
    except Exception:
        return content  # security module unavailable → fail open (never break memory)

    report = _sec_auditor.audit_input(content, session_id=f"mem:{chunk_id[:60]}")
    if not report.is_suspicious:
        return content

    mem_log.warn(
        f"Memory chunk '{chunk_id}' — injection patterns detected "
        f"({report.threat_level}): {report.patterns_found}"
    )

    if report.threat_level == "high":
        return (
            f"[MEMÓRIA QUARENTENADA — chunk={chunk_id}]\n"
            f"Padrões de injeção bloqueados: {report.patterns_found}\n"
            f"Conteúdo sanitizado:\n{report.sanitized_text}"
        )
    # medium / low: tag + sanitized text (content preserved, attack phrases removed)
    return (
        f"[⚠️ MEMÓRIA SUSPEITA ({report.threat_level}): {report.patterns_found}]\n"
        f"{report.sanitized_text}"
    )


# ---------------------------------------------------------------------------

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2:
        return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_v1 = math.sqrt(sum(a * a for a in v1))
    norm_v2 = math.sqrt(sum(b * b for b in v2))
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return dot_product / (norm_v1 * norm_v2)

class MultiVectorMemory:
    """
    Advanced Hybrid Memory System (SQLite FTS5 + Python Cosine Similarity Vector Search)
    Inspired by OpenClaw's memory.manager.ts
    Uses Reciprocal Rank Fusion (RRF) to merge keyword and semantic search results dynamically.
    """
    def __init__(self, db_path: str = "rlm_memory_v2.db", embedding_model: str = "text-embedding-3-small"):
        self.db_path = db_path
        self.embedding_model = embedding_model
        
        # Initialize OpenAI client for embeddings
        if openai is not None and os.getenv("OPENAI_API_KEY"):
            # Uses environment variable OPENAI_API_KEY naturally
            self.client = openai.OpenAI()
        else:
            self.client = None
            mem_log.warn("OpenAI API key or package missing. Vector search will fallback to zeroes.")
            
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            # Main storage table including vector raw str
            conn.execute('''
                CREATE TABLE IF NOT EXISTS memory_chunks (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    content TEXT,
                    metadata TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    embedding TEXT,
                    importance_score REAL DEFAULT 0.5,
                    is_deprecated INTEGER DEFAULT 0
                )
            ''')
            # Full Text Search virtual table
            conn.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    id UNINDEXED,
                    content,
                    tokenize="porter"
                )
            ''')
            # Grafo de relações entre chunks (construído pelo mini agent)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS memory_edges (
                    id TEXT PRIMARY KEY,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_id) REFERENCES memory_chunks(id),
                    FOREIGN KEY (to_id) REFERENCES memory_chunks(id)
                )
            ''')
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_edges_from ON memory_edges(from_id)'
            )
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_edges_to ON memory_edges(to_id)'
            )
            conn.commit()
            # Migração segura: adiciona colunas em DBs criados antes desta versão
            self._migrate_db(conn)

    def _migrate_db(self, conn: sqlite3.Connection) -> None:
        """
        Adiciona colunas opcionais ausentes em bancos criados antes desta versão.
        Usa try/except por coluna — ignora OperationalError se a coluna já existe.
        """
        migrations = [
            "ALTER TABLE memory_chunks ADD COLUMN importance_score REAL DEFAULT 0.5",
            "ALTER TABLE memory_chunks ADD COLUMN is_deprecated INTEGER DEFAULT 0",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # coluna já existe — ignorar é o comportamento correto

    def get_embedding(self, text: str) -> List[float]:
        try:
            if not self.client:
                return []
            response = self.client.embeddings.create(
                input=[text],
                model=self.embedding_model
            )
            return response.data[0].embedding
        except Exception as e:
            mem_log.error(f"Failed to generate embedding: {e}")
            return []

    def add_memory(
        self,
        session_id: str,
        content: str,
        metadata: Optional[dict] = None,
        memory_id: Optional[str] = None,
        importance_score: float = 0.5,
    ) -> str:
        """
        Adds a new memory fragment, generating its embedding and FTS index.

        Args:
            session_id: Identificador da sessão que originou esta memória.
            content: Texto do fragmento de memória.
            metadata: Metadados opcionais (dict serializável em JSON).
            memory_id: ID explícito. Se None, um UUID é gerado.
            importance_score: Score de importância 0.0–1.0 (padrão 0.5).
                              Idealmente atribuído pelo memory_mini_agent.
        """
        if not memory_id:
            memory_id = str(uuid.uuid4())
        meta_str = json.dumps(metadata) if metadata else "{}"

        # Garante que importance_score esteja dentro do intervalo válido
        importance_score = max(0.0, min(1.0, float(importance_score)))

        embedding = self.get_embedding(content)
        embedding_str = json.dumps(embedding) if embedding else "[]"

        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO memory_chunks
                    (id, session_id, content, metadata, embedding, importance_score, is_deprecated)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (memory_id, session_id, content, meta_str, embedding_str, importance_score))

            conn.execute('DELETE FROM memory_fts WHERE id = ?', (memory_id,))

            conn.execute('''
                INSERT INTO memory_fts (id, content)
                VALUES (?, ?)
            ''', (memory_id, content))

            conn.commit()

        mem_log.debug(f"Added memory chunk {memory_id} (importance={importance_score:.2f}) for session {session_id}")
        return memory_id

    def update_importance(self, memory_id: str, importance_score: float) -> None:
        """
        Atualiza o score de importância de um chunk existente.

        Args:
            memory_id: ID do chunk a atualizar.
            importance_score: Novo score (0.0–1.0).
        """
        importance_score = max(0.0, min(1.0, float(importance_score)))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memory_chunks SET importance_score = ? WHERE id = ?",
                (importance_score, memory_id),
            )
            conn.commit()

    def deprecate(self, memory_id: str) -> None:
        """
        Marca um chunk como depreciado (is_deprecated = 1).

        Chunks depreciados são excluídos das buscas mas não deletados fisicamente,
        preservando o histórico do grafo de memória.

        Args:
            memory_id: ID do chunk a depreciar.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memory_chunks SET is_deprecated = 1 WHERE id = ?",
                (memory_id,),
            )
            conn.commit()
        mem_log.debug(f"Deprecated memory chunk {memory_id}")

    def add_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: str,
        confidence: float = 1.0,
    ) -> str:
        """
        Adiciona uma aresta tipada ao grafo de memória.

        Args:
            from_id: ID do chunk de origem (geralmente o chunk existente).
            to_id: ID do chunk de destino (geralmente o chunk novo).
            edge_type: Tipo da relação — um de: "contradicts", "extends",
                       "updates", "causes", "fixes".
            confidence: Confiança da aresta (0.0–1.0).

        Returns:
            ID da aresta criada.
        """
        edge_id = str(uuid.uuid4())
        confidence = max(0.0, min(1.0, float(confidence)))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                '''
                INSERT OR IGNORE INTO memory_edges (id, from_id, to_id, edge_type, confidence)
                VALUES (?, ?, ?, ?, ?)
                ''',
                (edge_id, from_id, to_id, edge_type, confidence),
            )
            conn.commit()
        mem_log.debug(f"Added edge {from_id} --[{edge_type}]--> {to_id}")
        return edge_id

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a memory directly by its ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM memory_chunks WHERE id = ?", (memory_id,)).fetchone()
            if row:
                # Phase 9.3 (Gap A): sanitize at read time
                safe_content = _sanitize_memory_chunk(row["content"], chunk_id=row["id"])
                return {
                    "id": row["id"],
                    "content": safe_content,
                    "metadata": json.loads(row["metadata"]),
                    "session_id": row["session_id"],
                    "timestamp": row["timestamp"],
                    "embedding": json.loads(row["embedding"]) if row["embedding"] else []
                }
        return None

    def search_hybrid(
        self,
        query: str,
        limit: int = 5,
        session_id: Optional[str] = None,
        temporal_decay: bool = True,
        half_life_days: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """
        Queries both the vector space (via Cosine Similarity) and keyword index (via FTS5).
        Combines rankings using RRF (Reciprocal Rank Fusion).

        Args:
            query: Texto da consulta.
            limit: Máximo de resultados.
            session_id: Filtra por sessão (None = todas).
            temporal_decay: Se True, penaliza memórias mais antigas pelo fator e^(-λ·age_days).
            half_life_days: Meia-vida em dias — após esse período o score cai 50%.
        """
        query_embedding = self.get_embedding(query)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # 1. FTS Search
            fts_query_clean = query.replace("'", "").replace('"', "")
            # Prepare FTS query for simplicity by extracting words
            words = [w for w in fts_query_clean.split() if w.isalnum()]
            fts_query_match = " OR ".join(words) if words else ""
            
            fts_rows = []
            if fts_query_match:
                try:
                    sql_fts = "SELECT id, content FROM memory_fts WHERE memory_fts MATCH ? LIMIT 20"
                    fts_rows = conn.execute(sql_fts, (fts_query_match,)).fetchall()
                except sqlite3.OperationalError:
                    pass # Invalid query syntax fallback
                    
            fts_ranks = {}
            for idx, row in enumerate(fts_rows):
                fts_ranks[row['id']] = idx + 1
                
            # 2. Vector Search (Python side filtering based on session constraint)
            sql_vec = "SELECT id, content, metadata, embedding, session_id, timestamp, importance_score FROM memory_chunks WHERE is_deprecated = 0"
            params = []
            if session_id:
                sql_vec += " AND session_id = ?"
                params.append(session_id)
                
            all_chunks = conn.execute(sql_vec, params).fetchall()
            
            vec_scores = []
            chunk_data = {}
            for row in all_chunks:
                cid = row['id']
                chunk_data[cid] = row
                emb_str = row['embedding']
                emb = json.loads(emb_str) if emb_str else []
                score = cosine_similarity(query_embedding, emb) if query_embedding and emb else 0.0
                vec_scores.append((cid, score))
                
            # Sort by vector similarity descending
            vec_scores.sort(key=lambda x: x[1], reverse=True)
            
            vec_ranks = {}
            for idx, (cid, score) in enumerate(vec_scores[:20]):
                vec_ranks[cid] = idx + 1
                
            # 3. RRF (Reciprocal Rank Fusion) + Temporal Decay
            k = 60
            now_ts = time.time()
            decay_lambda = math.log(2) / half_life_days if temporal_decay and half_life_days > 0 else 0.0

            final_scores = {}
            for cid in chunk_data.keys():
                score_fts = 0
                score_vec = 0
                if cid in fts_ranks:
                    score_fts = 1.0 / (k + fts_ranks[cid])
                if cid in vec_ranks:
                    score_vec = 1.0 / (k + vec_ranks[cid])

                total_score = score_fts + score_vec
                if total_score > 0:
                    if decay_lambda > 0:
                        # Parse ISO timestamp from SQLite ("YYYY-MM-DD HH:MM:SS")
                        ts_str = chunk_data[cid]["timestamp"]
                        try:
                            import datetime as _dt
                            ts_dt = _dt.datetime.fromisoformat(ts_str.replace(" ", "T"))
                            age_days = (now_ts - ts_dt.timestamp()) / 86400.0
                        except Exception:
                            age_days = 0.0
                        decay_factor = math.exp(-decay_lambda * max(age_days, 0.0))
                        total_score *= decay_factor

                    final_scores[cid] = total_score
                    
            # Get top N limit
            sorted_cids = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)[:limit]
            
            final_results = []
            for cid, score in sorted_cids:
                row = chunk_data[cid]
                # Phase 9.3 (Gap A): sanitize at read time — never blocks, just strips/flags
                safe_content = _sanitize_memory_chunk(row["content"], chunk_id=cid)
                final_results.append({
                    "id": cid,
                    "content": safe_content,
                    "metadata": json.loads(row["metadata"]),
                    "session_id": row["session_id"],
                    "timestamp": row["timestamp"],
                    "hybrid_score": round(score, 4),
                    "importance_score": round(float(row["importance_score"] or 0.5), 4),
                })
                
        return final_results

    def search_with_query_expansion(
        self,
        query: str,
        llm_fn: Callable[[str], str],
        limit: int = 5,
        session_id: Optional[str] = None,
        temporal_decay: bool = True,
        half_life_days: float = 30.0,
        n_variants: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Expande a query com variantes geradas pelo LLM antes de buscar.

        Fluxo:
          1. Pede ao LLM n_variants reformulações da query original.
          2. Executa search_hybrid para cada variante (limit*2 resultados).
          3. Agrega todos os resultados por RRF com deduplicação por id.
          4. Retorna top-limit.

        Args:
            query: Query original.
            llm_fn: Função que recebe um prompt e retorna string com as variantes
                    separadas por newline (ex.: `lambda p: openai_chat(p)`).
            limit: Máximo de resultados finais.
            session_id: Filtra por sessão.
            temporal_decay: Repassa para search_hybrid.
            half_life_days: Repassa para search_hybrid.
            n_variants: Quantas variantes gerar (incluindo a original).
        """
        # 1. Gerar variantes
        expansion_prompt = (
            f"Gere {n_variants} reformulações diferentes da seguinte consulta de busca.\n"
            f"Retorne apenas as reformulações, uma por linha, sem numeração.\n"
            f"Consulta original: {query}"
        )
        variants: List[str] = [query]  # sempre inclui a original
        try:
            raw = llm_fn(expansion_prompt)
            for line in raw.strip().splitlines():
                stripped = line.strip(" -•·1234567890.).")
                if stripped and stripped.lower() != query.lower():
                    variants.append(stripped)
                    if len(variants) >= n_variants + 1:
                        break
        except Exception as e:
            mem_log.warn(f"Query expansion LLM call failed: {e} — usando apenas query original")

        # 2. Buscar por todas as variantes
        per_variant_limit = limit * 2
        all_pools: List[List[Dict[str, Any]]] = []
        for v in variants:
            try:
                results = self.search_hybrid(
                    v,
                    limit=per_variant_limit,
                    session_id=session_id,
                    temporal_decay=temporal_decay,
                    half_life_days=half_life_days,
                )
                all_pools.append(results)
            except Exception as e:
                mem_log.warn(f"search_hybrid failed for variant '{v}': {e}")

        if not all_pools:
            return []

        # 3. RRF sobre todos os pools
        k = 60
        seen: Dict[str, Dict[str, Any]] = {}  # id → item
        rrf_scores: Dict[str, float] = {}

        for pool in all_pools:
            for rank, item in enumerate(pool):
                cid = item["id"]
                seen[cid] = item
                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank + 1)

        # 4. Top-limit
        top = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        final: List[Dict[str, Any]] = []
        for cid, score in top:
            item = dict(seen[cid])
            item["expansion_score"] = round(score, 4)
            final.append(item)

        return final
