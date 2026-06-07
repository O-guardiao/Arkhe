"""
embeddings.py — Embeddings OPCIONAIS para retrieval semântico.

Por padrão o motor é lexical (BM25) + estrutural (hipergrafo) e não precisa de
nada disto. Quando você quer recall semântico (achar notas que falam do mesmo
assunto com outras palavras), pluga um provider aqui.

Providers:
    HashingEmbedding  offline, determinístico, sem rede/deps — vetor lexical por
                      bag-of-tokens com projeção por hash. NÃO é embedding
                      "profundo": serve de fallback e para testes. Útil quando
                      você não tem/quer chamadas externas.
    OpenAIEmbedding   semântico de verdade (extra opcional: `pip install
                      arkhe-obsidian-rag[openai]` + OPENAI_API_KEY). Batches
                      automáticos.

Use `get_embedder("hashing" | "openai" | "openai:modelo" | "none")`.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface mínima: nome estável + embed em lote."""

    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    """Similaridade cosseno entre vetores densos (0 se algum tiver norma 0)."""
    if len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for ai, bi in zip(a, b, strict=False):
        dot += ai * bi
        na += ai * ai
        nb += bi * bi
    denom = math.sqrt(na) * math.sqrt(nb)
    return dot / denom if denom else 0.0


class HashingEmbedding:
    """
    Vetor lexical determinístico por hashing de tokens (offline).

    Tokens iguais caem na mesma dimensão (com sinal estável), então textos que
    compartilham vocabulário ficam próximos no cosseno. É um baseline barato e
    testável — não captura sinônimos como um modelo neural.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _hash(self, token: str) -> tuple[int, float]:
        h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % self.dim
        sign = 1.0 if h[4] & 1 else -1.0
        return idx, sign

    def embed(self, texts: list[str]) -> list[list[float]]:
        from arkhe_obsidian_rag.parser import tokenize

        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in tokenize(text):
                idx, sign = self._hash(tok)
                vec[idx] += sign
            out.append(_l2(vec))
        return out


class OpenAIEmbedding:
    """Embeddings semânticos via OpenAI (dep `openai`, exige OPENAI_API_KEY)."""

    def __init__(self, model: str = "text-embedding-3-small", batch_size: int = 128):
        self.model = model
        self.batch_size = batch_size
        self.name = f"openai:{model}"
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # extra opcional
                raise ImportError(
                    "OpenAIEmbedding requer o extra openai: pip install arkhe-obsidian-rag[openai]"
                ) from exc

            self._client = OpenAI()
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t or " " for t in texts[i : i + self.batch_size]]
            resp = client.embeddings.create(model=self.model, input=batch)
            out.extend(d.embedding for d in resp.data)
        return out


def get_embedder(spec: str | None) -> EmbeddingProvider | None:
    """
    Resolve um provider a partir de uma spec textual.

    None / "" / "none"      → None (sem semântica)
    "hashing" / "hashing:N" → HashingEmbedding(dim=N)
    "openai" / "openai:m"   → OpenAIEmbedding(model=m)
    """
    if not spec or spec.lower() == "none":
        return None
    spec = spec.strip()
    if spec.startswith("hashing"):
        _, _, n = spec.partition(":")
        return HashingEmbedding(dim=int(n) if n.isdigit() else 256)
    if spec.startswith("openai"):
        _, _, model = spec.partition(":")
        return OpenAIEmbedding(model=model or "text-embedding-3-small")
    if os.path.exists(spec):  # reservado p/ extensões futuras (modelo local)
        raise ValueError(f"embedder local ainda não suportado: {spec}")
    raise ValueError(f"embedder desconhecido: {spec!r} (use none|hashing|openai)")
