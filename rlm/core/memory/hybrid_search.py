"""
hybrid_search.py — Busca híbrida: vetorial + keywords via RRF ponderado.

Porta de packages/memory/src/hybrid.ts do @arkhe/memory TypeScript.

Exporta:
  - keyword_score(doc, query)        — score BM25-like (TF com length norm)
  - rrf(rankings, k)                 — Reciprocal Rank Fusion puro
  - HybridSearcher                   — classe standalone

A lógica de RRF inline em MultiVectorMemory/KnowledgeBase não é substituída;
este módulo fornece a mesma lógica como unidade testável de forma independente.

Uso típico:
    from rlm.core.memory.hybrid_search import HybridSearcher
    searcher = HybridSearcher(entries, alpha=0.7)
    results = searcher.search("como resetar a sessão", top_k=5)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from rlm.core.memory.memory_types import MemoryEntry, SearchQuery, SearchResult, Vector
from rlm.core.memory.vector_utils import cosine_similarity_dense


# ---------------------------------------------------------------------------
# Keyword scoring (BM25-like)
# ---------------------------------------------------------------------------

def keyword_score(doc: str, query: str) -> float:
    """
    Score TF-like entre um documento e uma query.

    - Tokeniza por split em caracteres não-palavra + lowercase.
    - score = Σ_termos tf(term, doc) / (1 + len(doc) / 10)
      (normalização de comprimento inspirada em BM25)

    Porta de ``keywordScore`` em hybrid.ts.
    """
    if not query.strip():
        return 0.0

    doc_tokens = _tokenize(doc)
    query_terms = _tokenize(query)
    if not doc_tokens or not query_terms:
        return 0.0

    freq: dict[str, int] = {}
    for t in doc_tokens:
        freq[t] = freq.get(t, 0) + 1

    score = 0.0
    len_norm = 1.0 + len(doc_tokens) / 10.0

    for term in query_terms:
        tf = freq.get(term, 0)
        score += tf / len_norm

    # Normaliza pela quantidade de termos únicos da query.
    return score / max(len(query_terms), 1)


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"\W+", text.lower()) if t]


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion — puro (k único)
# ---------------------------------------------------------------------------

def rrf(rankings: list[list[SearchResult]], k: int = 60) -> list[SearchResult]:
    """
    Mescla múltiplas listas ranqueadas usando Reciprocal Rank Fusion.

    RRF(d) = Σ_i  1 / (k + rank(d, lista_i))
    onde k é constante de suavização (default 60, Cormack et al. 2009).

    Entradas são deduplicadas por ``entry.id``; source é 'hybrid'.

    Porta de ``rrf`` em hybrid.ts.
    """
    score_map: dict[str, float] = {}
    entry_map: dict[str, MemoryEntry] = {}

    for ranking in rankings:
        for rank, result in enumerate(ranking):
            eid = result.entry.id
            score_map[eid] = score_map.get(eid, 0.0) + 1.0 / (k + rank + 1)
            entry_map[eid] = result.entry

    fused: list[SearchResult] = [
        SearchResult(entry=entry_map[eid], score=score, source="hybrid")
        for eid, score in score_map.items()
    ]
    fused.sort(key=lambda r: r.score, reverse=True)
    return fused


# ---------------------------------------------------------------------------
# RRF ponderado (k_sem / k_kw separados)
# ---------------------------------------------------------------------------

def _rrf_weighted(
    semantic: list[SearchResult],
    keyword: list[SearchResult],
    k_sem: float,
    k_kw: float,
) -> list[SearchResult]:
    """RRF com constantes de suavização distintas por lista."""
    score_map: dict[str, float] = {}
    entry_map: dict[str, MemoryEntry] = {}

    def _add_list(lst: list[SearchResult], k: float) -> None:
        for rank, r in enumerate(lst):
            eid = r.entry.id
            score_map[eid] = score_map.get(eid, 0.0) + 1.0 / (k + rank + 1)
            entry_map[eid] = r.entry

    _add_list(semantic, k_sem)
    _add_list(keyword, k_kw)

    fused: list[SearchResult] = [
        SearchResult(entry=entry_map[eid], score=score, source="hybrid")
        for eid, score in score_map.items()
    ]
    fused.sort(key=lambda r: r.score, reverse=True)
    return fused


# ---------------------------------------------------------------------------
# HybridSearcher
# ---------------------------------------------------------------------------

class HybridSearcher:
    """
    Combina busca vetorial e keyword via RRF ponderado.

    ``alpha`` controla o peso da lista semântica no RRF:
      - k_sem = 60 / alpha      (menor k → scores maiores → mais peso)
      - k_kw  = 60 / (1 - alpha)

    Porta de ``HybridSearcher`` em hybrid.ts.

    Uso:
        # entries é uma lista ou callable que retorna list[MemoryEntry]
        searcher = HybridSearcher(entries, embed_fn=my_embedder.embed, alpha=0.7)
        results = searcher.search("query aqui", session_id="s1", top_k=5)
    """

    def __init__(
        self,
        entries_or_fn: "list[MemoryEntry] | Callable[[], list[MemoryEntry]]",
        *,
        embed_fn: "Callable[[list[str]], list[Vector]]",
        alpha: float = 0.7,
    ) -> None:
        """
        Parâmetros
        ----------
        entries_or_fn:
            Lista de MemoryEntry ou callable que retorna a lista (lazy).
        embed_fn:
            Função ``embed(texts) -> list[Vector]`` para vetorizar a query.
        alpha:
            Peso semântico em [0, 1]. Default 0.7.
        """
        self._entries_or_fn = entries_or_fn
        self._embed_fn = embed_fn
        self._alpha = max(0.0, min(1.0, alpha))

    def _get_entries(self) -> list[MemoryEntry]:
        if callable(self._entries_or_fn):
            return self._entries_or_fn()
        return self._entries_or_fn  # type: ignore[return-value]

    def search(
        self,
        query_text: str,
        *,
        top_k: int = 10,
        session_id: Optional[str] = None,
        min_score: Optional[float] = None,
        filters: Optional[dict] = None,
    ) -> list[SearchResult]:
        """
        Executa busca híbrida vetorial + keyword com RRF ponderado.

        Retorna os ``top_k`` melhores resultados.
        """
        entries = self._get_entries()

        # --- Aplica filtros de pré-seleção ---
        if session_id is not None:
            entries = [e for e in entries if e.session_id == session_id]
        if filters:
            entries = [e for e in entries if _matches_filters(e, filters)]

        if not entries:
            return []

        fetch_k = max(top_k * 3, 50)

        # --- Embedding da query ---
        query_vecs = self._embed_fn([query_text])
        query_vec: Vector = query_vecs[0] if query_vecs else []

        # --- Busca vetorial ---
        vector_results: list[SearchResult] = []
        if query_vec:
            scored = []
            for entry in entries:
                if entry.vector is None or len(entry.vector) == 0:
                    continue
                score = cosine_similarity_dense(entry.vector, query_vec)
                if min_score is not None and score < min_score:
                    continue
                scored.append(SearchResult(entry=entry, score=score, source="vector"))
            scored.sort(key=lambda r: r.score, reverse=True)
            vector_results = scored[:fetch_k]

        # --- Busca keyword ---
        keyword_results: list[SearchResult] = []
        if query_text.strip():
            scored_kw = []
            for entry in entries:
                score = keyword_score(entry.content, query_text)
                if score <= 0:
                    continue
                if min_score is not None and score < min_score:
                    continue
                scored_kw.append(SearchResult(entry=entry, score=score, source="keyword"))
            scored_kw.sort(key=lambda r: r.score, reverse=True)
            keyword_results = scored_kw[:fetch_k]

        # --- RRF ponderado ---
        alpha = self._alpha
        k_sem = 60.0 / alpha if alpha > 0 else 1e9
        k_kw = 60.0 / (1.0 - alpha) if (1.0 - alpha) > 0 else 1e9

        fused = _rrf_weighted(vector_results, keyword_results, k_sem, k_kw)
        return fused[:top_k]


def _matches_filters(
    entry: MemoryEntry,
    filters: dict[str, "str | int | float | bool"],
) -> bool:
    for key, val in filters.items():
        if entry.metadata.get(key) != val:
            return False
    return True
