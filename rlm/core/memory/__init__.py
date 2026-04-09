"""Superfície pública do subsistema de memória.

Este pacote agrega contratos estáveis do runtime de memória:
- armazenamento vetorial por sessão e knowledge base global;
- budget gate e cache quente usados pela sessão/runtime pipeline;
- tipos formais, utilitários vetoriais e busca híbrida standalone.

Os símbolos são resolvidos sob demanda porque partes do subsistema puxam
SQLite, OpenAI e aceleração Rust opcional. Isso evita penalizar o import de
``rlm.core`` com carregamento prematuro de dependências pesadas.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rlm.core.memory.embedding_backend import EmbeddingBackend, MockEmbeddingBackend
    from rlm.core.memory.hybrid_search import HybridSearcher, keyword_score, rrf
    from rlm.core.memory.knowledge_base import GlobalKnowledgeBase
    from rlm.core.memory.memory_budget import (
        IMPORTANCE_WEIGHT,
        MEMORY_BUDGET_PCT,
        RECENCY_HALF_LIFE_DAYS,
        RECENCY_WEIGHT,
        RELEVANCE_WEIGHT,
        RETRIEVAL_LIMIT,
        SCORE_THRESHOLD,
        TOKENS_PER_CHAR,
        estimate_tokens_from_text,
        format_memory_block,
        inject_memory_with_budget,
        score_tripartite,
    )
    from rlm.core.memory.memory_hot_cache import (
        MemorySessionCache,
        evict_cache,
        get_or_create_cache,
        registry_size,
    )
    from rlm.core.memory.memory_manager import MultiVectorMemory, cosine_similarity
    from rlm.core.memory.memory_types import (
        EmbeddingModel,
        EmbeddingProvider,
        EmbeddingRequest,
        EmbeddingResult,
        MemoryEntry,
        MemoryManagerConfig,
        SearchQuery,
        SearchResult,
        Vector,
    )
    from rlm.core.memory.mmr import mmr_rerank
    from rlm.core.memory.temporal_decay import age_in_days, apply_temporal_decay
    from rlm.core.memory.vector_utils import cosine_similarity_dense, dot_product, normalize_vector

_LAZY_MODULES: dict[str, str] = {
    "embedding_backend": "rlm.core.memory.embedding_backend",
    "hybrid_search": "rlm.core.memory.hybrid_search",
    "knowledge_base": "rlm.core.memory.knowledge_base",
    "knowledge_consolidator": "rlm.core.memory.knowledge_consolidator",
    "memory_budget": "rlm.core.memory.memory_budget",
    "memory_hot_cache": "rlm.core.memory.memory_hot_cache",
    "memory_manager": "rlm.core.memory.memory_manager",
    "memory_mini_agent": "rlm.core.memory.memory_mini_agent",
    "memory_types": "rlm.core.memory.memory_types",
    "mmr": "rlm.core.memory.mmr",
    "semantic_retrieval": "rlm.core.memory.semantic_retrieval",
    "temporal_decay": "rlm.core.memory.temporal_decay",
    "vector_utils": "rlm.core.memory.vector_utils",
}

_LAZY_ATTRS: dict[str, str] = {
    "Vector": "rlm.core.memory.memory_types",
    "EmbeddingProvider": "rlm.core.memory.memory_types",
    "EmbeddingModel": "rlm.core.memory.memory_types",
    "EmbeddingRequest": "rlm.core.memory.memory_types",
    "EmbeddingResult": "rlm.core.memory.memory_types",
    "MemoryEntry": "rlm.core.memory.memory_types",
    "SearchQuery": "rlm.core.memory.memory_types",
    "SearchResult": "rlm.core.memory.memory_types",
    "MemoryManagerConfig": "rlm.core.memory.memory_types",
    "cosine_similarity_dense": "rlm.core.memory.vector_utils",
    "normalize_vector": "rlm.core.memory.vector_utils",
    "dot_product": "rlm.core.memory.vector_utils",
    "mmr_rerank": "rlm.core.memory.mmr",
    "age_in_days": "rlm.core.memory.temporal_decay",
    "apply_temporal_decay": "rlm.core.memory.temporal_decay",
    "EmbeddingBackend": "rlm.core.memory.embedding_backend",
    "MockEmbeddingBackend": "rlm.core.memory.embedding_backend",
    "keyword_score": "rlm.core.memory.hybrid_search",
    "rrf": "rlm.core.memory.hybrid_search",
    "HybridSearcher": "rlm.core.memory.hybrid_search",
    "MultiVectorMemory": "rlm.core.memory.memory_manager",
    "cosine_similarity": "rlm.core.memory.memory_manager",
    "GlobalKnowledgeBase": "rlm.core.memory.knowledge_base",
    "MEMORY_BUDGET_PCT": "rlm.core.memory.memory_budget",
    "SCORE_THRESHOLD": "rlm.core.memory.memory_budget",
    "IMPORTANCE_WEIGHT": "rlm.core.memory.memory_budget",
    "RECENCY_WEIGHT": "rlm.core.memory.memory_budget",
    "RELEVANCE_WEIGHT": "rlm.core.memory.memory_budget",
    "RETRIEVAL_LIMIT": "rlm.core.memory.memory_budget",
    "TOKENS_PER_CHAR": "rlm.core.memory.memory_budget",
    "RECENCY_HALF_LIFE_DAYS": "rlm.core.memory.memory_budget",
    "score_tripartite": "rlm.core.memory.memory_budget",
    "inject_memory_with_budget": "rlm.core.memory.memory_budget",
    "estimate_tokens_from_text": "rlm.core.memory.memory_budget",
    "format_memory_block": "rlm.core.memory.memory_budget",
    "MemorySessionCache": "rlm.core.memory.memory_hot_cache",
    "get_or_create_cache": "rlm.core.memory.memory_hot_cache",
    "evict_cache": "rlm.core.memory.memory_hot_cache",
    "registry_size": "rlm.core.memory.memory_hot_cache",
}

__all__ = [
    "Vector",
    "EmbeddingProvider",
    "EmbeddingModel",
    "EmbeddingRequest",
    "EmbeddingResult",
    "MemoryEntry",
    "SearchQuery",
    "SearchResult",
    "MemoryManagerConfig",
    "cosine_similarity_dense",
    "normalize_vector",
    "dot_product",
    "mmr_rerank",
    "age_in_days",
    "apply_temporal_decay",
    "EmbeddingBackend",
    "MockEmbeddingBackend",
    "keyword_score",
    "rrf",
    "HybridSearcher",
    "MultiVectorMemory",
    "cosine_similarity",
    "GlobalKnowledgeBase",
    "MEMORY_BUDGET_PCT",
    "SCORE_THRESHOLD",
    "IMPORTANCE_WEIGHT",
    "RECENCY_WEIGHT",
    "RELEVANCE_WEIGHT",
    "RETRIEVAL_LIMIT",
    "TOKENS_PER_CHAR",
    "RECENCY_HALF_LIFE_DAYS",
    "score_tripartite",
    "inject_memory_with_budget",
    "estimate_tokens_from_text",
    "format_memory_block",
    "MemorySessionCache",
    "get_or_create_cache",
    "evict_cache",
    "registry_size",
]


def __getattr__(name: str):
    if name in _LAZY_MODULES:
        module = importlib.import_module(_LAZY_MODULES[name])
        globals()[name] = module
        return module
    if name in _LAZY_ATTRS:
        module = importlib.import_module(_LAZY_ATTRS[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_LAZY_MODULES))
