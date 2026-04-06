"""
vector_utils.py — Utilitários matemáticos para vetores densos.

Porta de ``cosineSimilarity`` e ``normalizeVector`` de
packages/memory/src/store.ts do @arkhe/memory TypeScript.

Quando ``arkhe_memory`` (Rust) está disponível, ``cosine_similarity_dense``
delega para ele (20-100× mais rápido). O fallback é Python puro.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from rlm.core.memory.memory_types import Vector

try:
    import arkhe_memory as _ark_mem  # type: ignore[import]
    _RUST_AVAILABLE = True
except ImportError:
    _ark_mem = None
    _RUST_AVAILABLE = False


# ---------------------------------------------------------------------------
# Cosine similarity — vetores densos (float list)
# ---------------------------------------------------------------------------

def cosine_similarity_dense(a: Vector, b: Vector) -> float:
    """
    Similaridade cosseno entre dois vetores densos.

    Retorna 0 quando qualquer vetor tem magnitude zero (evita NaN).
    Porta de ``cosineSimilarity`` em store.ts.

    Delega ao Rust (``arkhe_memory.cosine_similarity``) se disponível.
    """
    if len(a) != len(b):
        raise ValueError(
            f"cosine_similarity_dense: dimensão incompatível ({len(a)} vs {len(b)})"
        )
    if _RUST_AVAILABLE:
        return float(_ark_mem.cosine_similarity(a, b))

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for ai, bi in zip(a, b):
        dot += ai * bi
        norm_a += ai * ai
        norm_b += bi * bi
    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    return 0.0 if denom == 0.0 else dot / denom


# ---------------------------------------------------------------------------
# L2 normalisation
# ---------------------------------------------------------------------------

def normalize_vector(v: Vector) -> Vector:
    """
    Retorna uma cópia L2-normalizada do vetor de entrada.

    Se a norma for zero, retorna vetor de zeros (sem divisão).
    Porta de ``normalizeVector`` em store.ts.
    """
    norm_sq = sum(x * x for x in v)
    if norm_sq == 0.0:
        return [0.0] * len(v)
    norm = math.sqrt(norm_sq)
    return [x / norm for x in v]


# ---------------------------------------------------------------------------
# Dot product (conveniência)
# ---------------------------------------------------------------------------

def dot_product(a: Vector, b: Vector) -> float:
    """Produto escalar entre dois vetores densos."""
    return sum(ai * bi for ai, bi in zip(a, b))
