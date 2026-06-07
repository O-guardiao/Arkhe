"""
retriever.py — RAG-hipergrafo: query → ContextPack pré-LLM.

Fluxo de uma consulta:
    1. BM25 nos chunks → hits lexicais finos.
    1b. (opcional) similaridade semântica por embeddings → hits semânticos.
    2. Agrega hits por nota → notas-semente (lexical + semântico).
    3. Expande as sementes pelo hipergrafo (tags/pastas/links) → notas vizinhas.
    4. Combina lexical + semântico + grafo, escolhe as top-N notas.
    5. Monta o context pack: melhores chunks por nota, dentro do orçamento de
       tokens, com markdown pronto para colar antes do prompt do LLM.

NÃO chama nenhum LLM (de geração). A entrega é o contexto; quem chama
(plugin/hook do Obsidian) faz a chamada ao modelo.
"""

from __future__ import annotations

import time

from rlm.obsidian_rag.embeddings import EmbeddingProvider
from rlm.obsidian_rag.index import VaultIndex
from rlm.obsidian_rag.models import (
    ContextPack,
    RetrievedChunk,
    RetrievedNote,
)
from rlm.obsidian_rag.parser import tokenize


def _semantic_chunk_scores(
    index: VaultIndex, embedder: EmbeddingProvider, query: str, top_k: int
) -> list[tuple[str, float]]:
    """Cosseno query↔chunks sobre os vetores cacheados. Retorna [(chunk_id, sim)]."""
    from rlm.core.memory.vector_utils import cosine_similarity_dense

    index.ensure_vectors(embedder)
    if not index.vectors:
        return []
    q_vec = embedder.embed([query])[0]
    sims = [(cid, cosine_similarity_dense(q_vec, vec)) for cid, vec in index.vectors.items()]
    sims.sort(key=lambda t: t[1], reverse=True)
    return [(cid, s) for cid, s in sims[:top_k] if s > 0.0]


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    top = max(scores.values()) or 1.0
    return {k: v / top for k, v in scores.items()}


def retrieve(
    index: VaultIndex,
    query: str,
    *,
    top_notes: int = 8,
    chunk_pool: int = 60,
    chunks_per_note: int = 3,
    hops: int = 1,
    lexical_weight: float = 1.0,
    graph_weight: float = 0.6,
    semantic_weight: float = 0.0,
    embedder: EmbeddingProvider | None = None,
    max_chars: int = 12000,
) -> ContextPack:
    """
    Executa o retrieval e devolve um ContextPack pronto para o LLM.

    Se `embedder` for fornecido e `semantic_weight > 0`, mistura um sinal
    semântico (cosseno query↔chunks) ao BM25 e à expansão por hipergrafo.
    """
    t0 = time.perf_counter()
    use_semantic = embedder is not None and semantic_weight > 0.0

    # 1. BM25 nos chunks
    chunk_hits = index.bm25.search(query, top_k=chunk_pool)

    # 2. agrega por nota (melhor chunk define a semente) + guarda chunks
    note_lexical: dict[str, float] = {}
    note_chunks: dict[str, list[tuple[str, float]]] = {}
    for chunk_id, score in chunk_hits:
        chunk = index.chunks_by_id.get(chunk_id)
        if chunk is None:
            continue
        path = chunk.note_path
        note_lexical[path] = max(note_lexical.get(path, 0.0), score)
        note_chunks.setdefault(path, []).append((chunk_id, score))

    lexical_norm = _normalize(note_lexical)

    # 1b/2b. (opcional) sinal semântico por embeddings
    semantic_norm: dict[str, float] = {}
    if use_semantic:
        note_semantic: dict[str, float] = {}
        for chunk_id, sim in _semantic_chunk_scores(index, embedder, query, chunk_pool):
            chunk = index.chunks_by_id.get(chunk_id)
            if chunk is None:
                continue
            path = chunk.note_path
            note_semantic[path] = max(note_semantic.get(path, 0.0), sim)
            note_chunks.setdefault(path, []).append((chunk_id, sim))
        semantic_norm = _normalize(note_semantic)

    # 3. expansão pelo hipergrafo a partir das sementes (lexical + semântico).
    # Usamos a ativação BRUTA (já decaída e derivada de sementes normalizadas):
    # re-normalizar pelo próprio máximo inflaria um único vizinho fraco para 1.0
    # e o faria superar matches reais.
    seeds: dict[str, float] = {}
    for path in set(lexical_norm) | set(semantic_norm):
        seeds[path] = max(lexical_norm.get(path, 0.0), semantic_norm.get(path, 0.0))
    graph_scores = index.hypergraph.expand(seeds, hops=hops)

    # 4. combina lexical + semântico + grafo
    candidates = set(lexical_norm) | set(semantic_norm) | set(graph_scores)
    combined: list[tuple[str, float, float, float, float]] = []
    for path in candidates:
        lex = lexical_norm.get(path, 0.0)
        sem = semantic_norm.get(path, 0.0)
        grph = graph_scores.get(path, 0.0)
        total = lexical_weight * lex + semantic_weight * sem + graph_weight * grph
        combined.append((path, total, lex, grph, sem))
    combined.sort(key=lambda t: t[1], reverse=True)

    # 5. monta o pack respeitando o orçamento de caracteres
    selected: list[RetrievedNote] = []
    used_chars = 0
    for path, total, lex, grph, sem in combined:
        if len(selected) >= top_notes or used_chars >= max_chars:
            break
        note = index.by_path.get(path)
        if note is None:
            continue

        reasons = _reasons(index, path, lex, grph, sem, query)
        picked = _pick_chunks(index, path, note_chunks.get(path), chunks_per_note)

        note_chars = 0
        kept: list[RetrievedChunk] = []
        for rc in picked:
            if used_chars + note_chars + len(rc.chunk.text) > max_chars and kept:
                break
            kept.append(rc)
            note_chars += len(rc.chunk.text)
        if not kept:
            continue
        used_chars += note_chars

        selected.append(
            RetrievedNote(
                path=path,
                title=note.title,
                score=total,
                lexical=lex,
                graph=grph,
                semantic=sem,
                tags=note.tags,
                wikilinks=note.wikilinks,
                reasons=reasons,
                chunks=kept,
            )
        )

    context_markdown = _render_markdown(query, selected, semantic=use_semantic)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    stats = {
        **index.stats(),
        "retrieve_ms": round(elapsed_ms, 2),
        "candidates": len(candidates),
        "selected_notes": len(selected),
        "context_chars": len(context_markdown),
        "semantic": bool(use_semantic),
    }
    return ContextPack(
        query=query,
        vault=index.vault_path,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        notes=selected,
        context_markdown=context_markdown,
        stats=stats,
    )


def _pick_chunks(
    index: VaultIndex,
    path: str,
    lexical_chunks: list[tuple[str, float]] | None,
    limit: int,
) -> list[RetrievedChunk]:
    """Escolhe os melhores chunks da nota; se a nota veio só do grafo, usa o início."""
    picked: list[RetrievedChunk] = []
    if lexical_chunks:
        # dedup: o mesmo chunk pode vir de BM25 e do semântico; fica o maior score
        best: dict[str, float] = {}
        for chunk_id, score in lexical_chunks:
            best[chunk_id] = max(best.get(chunk_id, 0.0), score)
        for chunk_id, score in sorted(best.items(), key=lambda t: t[1], reverse=True)[:limit]:
            chunk = index.chunks_by_id.get(chunk_id)
            if chunk is not None:
                picked.append(RetrievedChunk(chunk=chunk, score=score))
        return picked
    note = index.by_path.get(path)
    if note is not None:
        for chunk in note.chunks[:limit]:
            picked.append(RetrievedChunk(chunk=chunk, score=0.0))
    return picked


def _reasons(
    index: VaultIndex, path: str, lex: float, grph: float, sem: float, query: str
) -> list[str]:
    """Proveniência legível do porquê a nota foi trazida."""
    reasons: list[str] = []
    if lex > 0:
        reasons.append("bm25")
    if sem > 0:
        reasons.append("semantic")
    if grph > 0:
        q_terms = set(tokenize(query))
        for edge in index.hypergraph.edges_for(path):
            if edge.type == "tag" and edge.label.lower() in q_terms:
                reasons.append(f"tag:{edge.label}")
            elif edge.type == "links":
                reasons.append(f"link:{edge.label}")
        if not any(r.startswith(("tag:", "link:")) for r in reasons):
            reasons.append("graph")
    return reasons[:5]


def _render_markdown(query: str, notes: list[RetrievedNote], *, semantic: bool = False) -> str:
    """Renderiza o contexto pré-LLM como markdown pronto para colar."""
    if not notes:
        return f"<!-- nenhum contexto encontrado para: {query} -->"
    via = "BM25 + semântico + hipergrafo" if semantic else "BM25 + hipergrafo"
    lines = [
        f"# Contexto recuperado do vault (query: {query})",
        "",
        f"> {len(notes)} nota(s) relevante(s), via {via}.",
        "",
    ]
    for i, n in enumerate(notes, 1):
        tags = " ".join(f"#{t}" for t in n.tags[:8])
        lines.append(f"## {i}. {n.title}")
        meta = [f"`{n.path}`", f"score={n.score:.3f}"]
        if n.reasons:
            meta.append("via " + ", ".join(n.reasons))
        lines.append("  ".join(meta))
        if tags:
            lines.append(tags)
        lines.append("")
        for rc in n.chunks:
            if rc.chunk.heading:
                lines.append(f"### {rc.chunk.heading}")
            lines.append(rc.chunk.text.strip())
            lines.append("")
    return "\n".join(lines).strip()
