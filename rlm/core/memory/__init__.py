"""Memory system: vector storage, budget, caching, knowledge base."""

# ---------------------------------------------------------------------------
# Tipos formais (porta de packages/memory/src/types.ts)
# ---------------------------------------------------------------------------
from rlm.core.memory.memory_types import (  # noqa: F401
    Vector,
    EmbeddingProvider,
    EmbeddingModel,
    EmbeddingRequest,
    EmbeddingResult,
    MemoryEntry,
    SearchQuery,
    SearchResult,
    MemoryManagerConfig,
)

# ---------------------------------------------------------------------------
# Utilitários de vetores densos (porta de store.ts)
# ---------------------------------------------------------------------------
from rlm.core.memory.vector_utils import (  # noqa: F401
    cosine_similarity_dense,
    normalize_vector,
    dot_product,
)

# ---------------------------------------------------------------------------
# Maximal Marginal Relevance (porta de mmr.ts)
# ---------------------------------------------------------------------------
from rlm.core.memory.mmr import mmr_rerank  # noqa: F401

# ---------------------------------------------------------------------------
# Decaimento temporal (porta de temporal-decay.ts)
# ---------------------------------------------------------------------------
from rlm.core.memory.temporal_decay import (  # noqa: F401
    age_in_days,
    apply_temporal_decay,
)

# ---------------------------------------------------------------------------
# Embedding backends (porta de embeddings/interface.ts + mock.ts)
# ---------------------------------------------------------------------------
from rlm.core.memory.embedding_backend import (  # noqa: F401
    EmbeddingBackend,
    MockEmbeddingBackend,
)

# ---------------------------------------------------------------------------
# Busca híbrida standalone (porta de hybrid.ts)
# ---------------------------------------------------------------------------
from rlm.core.memory.hybrid_search import (  # noqa: F401
    keyword_score,
    rrf,
    HybridSearcher,
)
