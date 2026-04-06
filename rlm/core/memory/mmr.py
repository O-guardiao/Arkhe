"""
mmr.py — Maximal Marginal Relevance (MMR) reranking.

Porta directa de packages/memory/src/mmr.ts do @arkhe/memory TypeScript.

MMR seleciona iterativamente o candidato com o melhor trade-off entre:
  - Relevância para o vetor de query (cosine similarity com query_vector)
  - Diversidade em relação aos itens já selecionados

Fórmula (Carbonell & Goldstein, 1998):
  MMR(d) = λ · sim(d, q) − (1 − λ) · max_{dj ∈ S} sim(d, dj)

Uso típico:
    from rlm.core.memory.mmr import mmr_rerank
    results = mmr_rerank(search_results, query_vector, lambda_=0.5, top_k=5)
"""
from __future__ import annotations

from typing import Optional

from rlm.core.memory.memory_types import SearchResult, Vector
from rlm.core.memory.vector_utils import cosine_similarity_dense


def mmr_rerank(
    results: list[SearchResult],
    query_vector: Vector,
    *,
    lambda_: float = 0.5,
    top_k: Optional[int] = None,
) -> list[SearchResult]:
    """
    Reordena `results` por MMR.

    Parâmetros
    ----------
    results:
        Lista de resultados de busca ranqueados (com entry.vector preenchido).
    query_vector:
        Vetor denso de query (mesma dimensionalidade dos vetores de entry).
    lambda_:
        Trade-off relevância vs. diversidade (0 = pure diversity, 1 = pure relevance).
        Default 0.5.
    top_k:
        Número máximo de resultados a retornar. Default = len(results).

    Retorna
    -------
    Lista reordenada por MMR (score ajustado pelo MMR substituído no campo `score`).

    Notas
    -----
    - Entradas sem ``entry.vector`` são ignoradas.
    - A lista original não é modificada.
    """
    effective_top_k = top_k if top_k is not None else len(results)

    if not results or not query_vector:
        return []

    # Filtra apenas candidatos que possuem vetor.
    candidates = [r for r in results if r.entry.vector is not None and len(r.entry.vector) > 0]
    if not candidates:
        return results[:effective_top_k]

    selected: list[SearchResult] = []
    remaining = list(candidates)

    while len(selected) < effective_top_k and remaining:
        best_score = float("-inf")
        best_idx = 0

        for i, candidate in enumerate(remaining):
            vec = candidate.entry.vector
            if vec is None:
                continue

            rel_score = cosine_similarity_dense(vec, query_vector)

            # Diversidade: max cosine similarity com qualquer item já selecionado.
            max_sim_to_selected = 0.0
            for sel in selected:
                sel_vec = sel.entry.vector
                if sel_vec is None:
                    continue
                s = cosine_similarity_dense(vec, sel_vec)
                if s > max_sim_to_selected:
                    max_sim_to_selected = s

            mmr_score = lambda_ * rel_score - (1.0 - lambda_) * max_sim_to_selected

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        chosen = remaining[best_idx]
        # Substitui o score original pelo score MMR (como no TS).
        import dataclasses
        chosen_entry = dataclasses.replace(chosen, score=best_score)
        selected.append(chosen_entry)
        remaining.pop(best_idx)

    return selected
