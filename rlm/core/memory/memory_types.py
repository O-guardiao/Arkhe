"""
memory_types.py — Tipos formais para o sistema de memória vetorial.

Porta directa de packages/memory/src/types.ts do @arkhe/memory TypeScript.
Mantém compatibilidade com o restante de rlm.core.memory (sem quebrar imports
existentes de MultiVectorMemory, memory_budget, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# Tipos primitivos
# ---------------------------------------------------------------------------

Vector = list[float]
"""Vetor denso de embeddings."""

EmbeddingProvider = Literal["openai", "voyage", "mistral", "ollama", "mock"]
"""Providers de embedding suportados."""


# ---------------------------------------------------------------------------
# Modelos de embedding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmbeddingModel:
    """Identifica um modelo de embedding específico em um provider."""
    provider: EmbeddingProvider
    model_name: str
    dimension: int


@dataclass(frozen=True)
class EmbeddingRequest:
    """Input para uma requisição de embedding em batch."""
    texts: list[str]
    model: EmbeddingModel


@dataclass
class EmbeddingResult:
    """Output de uma chamada de embedding em batch."""
    vectors: list[Vector]
    model: EmbeddingModel
    tokens_used: int


# ---------------------------------------------------------------------------
# Entradas de memória
# ---------------------------------------------------------------------------

@dataclass
class MemoryEntry:
    """
    Um item de memória armazenado.

    Porta de ``MemoryEntry`` em types.ts.
    O ``id`` usa o mesmo formato nanoid (21 chars) gerado por
    ``secrets.token_urlsafe`` no Python — compatível com os IDs gerados
    pelo ``MultiVectorMemory`` (UUID v4 em hex sem hifens).
    """
    id: str
    session_id: str
    content: str
    created_at: str
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)
    # Vetor denso — opcional até o embedding ser calculado.
    vector: Optional[Vector] = None
    # Timestamp de último acesso (atualizado em search/get).
    accessed_at: Optional[str] = None
    # Score de relevância ao recuperar (não armazenado).
    score: Optional[float] = None


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------

@dataclass
class SearchQuery:
    """Parâmetros para uma busca de memória."""
    top_k: int
    text: Optional[str] = None
    vector: Optional[Vector] = None
    session_id: Optional[str] = None
    min_score: Optional[float] = None
    filters: Optional[dict[str, str | int | float | bool]] = None


@dataclass
class SearchResult:
    """Um resultado ranqueado de busca."""
    entry: MemoryEntry
    score: float
    source: Literal["vector", "keyword", "hybrid"] = "hybrid"


# ---------------------------------------------------------------------------
# Configuração do gerenciador
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MemoryManagerConfig:
    """Configuração para o MemoryManager (porta de MemoryManagerConfig.ts)."""
    embedding_model: EmbeddingModel
    vector_dim: int
    max_entries_per_session: Optional[int] = None
    decay_enabled: bool = False
    # Meia-vida para decay temporal em dias (ex.: 7).
    decay_half_life_days: Optional[float] = None
