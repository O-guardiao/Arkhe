"""
bm25.py — Índice lexical BM25 em Python puro (sem dependências).

Indexa os chunks (não as notas inteiras) para retrieval fino. BM25 dá um
baseline forte de relevância textual; o hipergrafo (hypergraph.py) entra
depois expandindo a vizinhança semântica/estrutural.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from arkhe_obsidian_rag.parser import tokenize


class BM25Index:
    """BM25 Okapi sobre uma coleção de documentos (chunks)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids: list[str] = []
        self.doc_len: list[int] = []
        self.avgdl: float = 0.0
        # token -> list[(doc_index, term_freq)]
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.idf: dict[str, float] = {}

    def build(self, docs: list[tuple[str, str]]) -> None:
        """docs: lista de (chunk_id, texto). Constrói o índice do zero."""
        self.doc_ids = []
        self.doc_len = []
        self.postings = {}
        df: Counter[str] = Counter()

        for chunk_id, text in docs:
            tokens = tokenize(text)
            self.doc_ids.append(chunk_id)
            self.doc_len.append(len(tokens))
            tf = Counter(tokens)
            doc_index = len(self.doc_ids) - 1
            for term, freq in tf.items():
                self.postings.setdefault(term, []).append((doc_index, freq))
            df.update(tf.keys())

        n = len(self.doc_ids)
        self.avgdl = (sum(self.doc_len) / n) if n else 0.0
        # idf BM25 com piso positivo (evita scores negativos em termos comuns)
        self.idf = {term: math.log(1 + (n - d + 0.5) / (d + 0.5)) for term, d in df.items()}

    def search(self, query: str, top_k: int = 40) -> list[tuple[str, float]]:
        """Retorna [(chunk_id, score)] ordenado desc, no máximo top_k."""
        q_terms = tokenize(query)
        if not q_terms or not self.doc_ids:
            return []
        scores: dict[int, float] = {}
        for term in set(q_terms):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for doc_index, freq in self.postings.get(term, ()):
                dl = self.doc_len[doc_index]
                denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                scores[doc_index] = scores.get(doc_index, 0.0) + idf * (
                    freq * (self.k1 + 1) / (denom or 1)
                )
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [(self.doc_ids[i], s) for i, s in ranked]

    def to_dict(self) -> dict[str, Any]:
        return {
            "k1": self.k1,
            "b": self.b,
            "doc_ids": self.doc_ids,
            "doc_len": self.doc_len,
            "avgdl": self.avgdl,
            "postings": {t: p for t, p in self.postings.items()},
            "idf": self.idf,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BM25Index:
        idx = cls(k1=d.get("k1", 1.5), b=d.get("b", 0.75))
        idx.doc_ids = d["doc_ids"]
        idx.doc_len = d["doc_len"]
        idx.avgdl = d["avgdl"]
        idx.postings = {t: [tuple(x) for x in p] for t, p in d["postings"].items()}
        idx.idf = d["idf"]
        return idx
