"""
embedding_backend.py — Protocolo e backends de embedding.

Porta de packages/memory/src/embeddings/ do @arkhe/memory TypeScript:
  - interface.ts  → EmbeddingBackend (Protocol)
  - mock.ts       → MockEmbeddingBackend (determinístico, para testes)

O backend OpenAI *não* é portado aqui porque rlm.core.memory.memory_manager
já usa ``openai.OpenAI()`` directamente com o mesmo modelo — misturar as duas
implementações introduziria duplicidade. Use ``MultiVectorMemory`` para chamadas
reais; use ``MockEmbeddingBackend`` para testes unitários.
"""
from __future__ import annotations

import math
import struct
from abc import abstractmethod
from typing import Protocol, runtime_checkable

from rlm.core.memory.memory_types import EmbeddingModel, Vector


# ---------------------------------------------------------------------------
# EmbeddingBackend Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class EmbeddingBackend(Protocol):
    """
    Interface comum que todo backend de embedding deve implementar.

    Porta de ``EmbeddingBackend`` em embeddings/interface.ts.
    """

    @abstractmethod
    def embed(self, texts: list[str]) -> list[Vector]:
        """
        Embed uma lista de textos em uma única requisição.
        Retorna um Vector por texto de entrada, na mesma ordem.
        """
        ...

    @abstractmethod
    def batch_embed(self, texts: list[str], batch_size: int = 96) -> list[Vector]:
        """
        Embed uma lista (potencialmente grande) de textos em batches.
        Resultados são achatados de volta para um único array na ordem de input.
        """
        ...

    @abstractmethod
    def model(self) -> EmbeddingModel:
        """Retorna o descritor do modelo deste backend."""
        ...


# ---------------------------------------------------------------------------
# MockEmbeddingBackend
# ---------------------------------------------------------------------------

_DEFAULT_DIMENSION = 128


class MockEmbeddingBackend:
    """
    Backend de embedding determinístico para testes unitários.

    Gera vetores unitários fazendo hash de cada texto via djb2 por dimensão,
    depois L2-normaliza. O mesmo texto sempre produz o mesmo vetor
    independentemente da ordem das chamadas.

    Porta de ``MockEmbeddingBackend`` em embeddings/mock.ts.
    Preserva a mesma lógica de hash (djb2 mesclado por dimensão) para que
    vetores sejam compatíveis entre a versão TS e esta.

    Rastreia:
        total_calls   — número de chamadas a embed() + batch_embed()
        total_tokens  — estimativa de tokens (1 token ≈ 4 chars)
    """

    def __init__(self, dimension: int = _DEFAULT_DIMENSION) -> None:
        self._dim = dimension
        self._calls = 0
        self._total_tokens = 0

    # --- EmbeddingBackend interface ---

    def model(self) -> EmbeddingModel:
        return EmbeddingModel(
            provider="mock",
            model_name="mock-embedding",
            dimension=self._dim,
        )

    def embed(self, texts: list[str]) -> list[Vector]:
        self._calls += 1
        self._total_tokens += sum(math.ceil(len(t) / 4) for t in texts)
        return [_deterministic_vector(t, self._dim) for t in texts]

    def batch_embed(self, texts: list[str], batch_size: int = 96) -> list[Vector]:
        self._calls += 1
        self._total_tokens += sum(math.ceil(len(t) / 4) for t in texts)
        return [_deterministic_vector(t, self._dim) for t in texts]

    # --- Estatísticas ---

    @property
    def total_calls(self) -> int:
        return self._calls

    @property
    def total_tokens(self) -> int:
        return self._total_tokens


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _djb2_mix(text: str, seed: int) -> float:
    """
    Hash djb2 sobre os code-points UTF de ``text``, com seed ``seed``.
    Mapeia o resultado int32 para o intervalo [−1, 1].

    Porta fiel de ``_djb2Mix`` em embeddings/mock.ts.
    Usa wrapping int32 com máscara 0xFFFFFFFF e reinterpretação com struct.
    """
    h = (5381 ^ seed) & 0xFFFFFFFF
    for ch in text:
        c = ord(ch)
        h = ((h << 5) + h + c) & 0xFFFFFFFF
    # Reinterpreta como int32 com sinal (igual ao comportamento | 0 em JS).
    h_signed = struct.unpack(">i", struct.pack(">I", h))[0]
    return h_signed / 0x7FFFFFFF


def _deterministic_vector(text: str, dim: int) -> Vector:
    """
    Produz um vetor unitário determinístico para ``text`` em ``dim`` dimensões.
    """
    raw = [_djb2_mix(text, i) for i in range(dim)]
    norm_sq = sum(x * x for x in raw)
    if norm_sq == 0.0:
        return [0.0] * dim
    norm = math.sqrt(norm_sq)
    return [x / norm for x in raw]
