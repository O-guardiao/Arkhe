from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

try:
    import arkhe_memory as _ark_mem
    _RUST_SPARSE_AVAILABLE = True
except ImportError:
    _ark_mem = None
    _RUST_SPARSE_AVAILABLE = False

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "com",
    "como",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "for",
    "from",
    "in",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "of",
    "or",
    "os",
    "para",
    "por",
    "the",
    "to",
    "um",
    "uma",
    "use",
    "via",
}

_CANONICAL_ALIASES = {
    "agenda": "agenda",
    "agendar": "agenda",
    "agendamento": "agenda",
    "arquivo": "file",
    "arquivos": "file",
    "calendario": "agenda",
    "commit": "git",
    "deploy": "deploy",
    "deployment": "deploy",
    "docs": "documentation",
    "documentacao": "documentation",
    "email": "email",
    "e-mail": "email",
    "erro": "error",
    "falha": "error",
    "github": "git",
    "issue": "ticket",
    "issues": "ticket",
    "ler": "read",
    "log": "logs",
    "mensagem": "message",
    "mensagens": "message",
    "navegar": "browser",
    "pagina": "page",
    "pesquisa": "search",
    "pesquisar": "search",
    "repositorio": "repository",
    "responder": "reply",
    "roteiro": "travel",
    "salvar": "write",
    "shell": "terminal",
    "ssh": "terminal",
    "tempo": "weather",
    "terminal": "terminal",
    "tweet": "social",
    "tweets": "social",
    "viagem": "travel",
}


def normalize_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"[a-z0-9_+-]+", normalize_text(text)):
        if len(raw) <= 1 or raw in _STOP_WORDS:
            continue
        tokens.append(_CANONICAL_ALIASES.get(raw, raw))
    return tokens


def vectorize_text(text: str) -> Counter[str]:
    vector: Counter[str] = Counter()
    for token in tokenize(text):
        vector[f"tok:{token}"] += 1.0
        if len(token) >= 4:
            for idx in range(len(token) - 2):
                vector[f"tri:{token[idx:idx + 3]}"] += 0.2
    return vector


def cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0.0) for key, value in left.items())
    if dot <= 0.0:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def semantic_similarity(query: str, candidate: str) -> float:
    return cosine_similarity(vectorize_text(query), vectorize_text(candidate))


@dataclass
class SemanticDocument:
    key: str
    text: str
    vector: Counter[str] = field(default_factory=Counter)


class SemanticTextIndex:
    """Índice semântico leve em memória, sem dependências externas.

    Quando o módulo Rust ``arkhe_memory`` está disponível, delega
    ``add()`` e ``search()`` para ``ArkheSemanticIndex`` (20-100× mais
    rápido).  Caso contrário, usa o caminho Python puro como fallback.
    """

    def __init__(self, entries: Iterable[tuple[str, str]] | None = None):
        self._rust_index = _ark_mem.ArkheSemanticIndex() if _RUST_SPARSE_AVAILABLE else None
        self._docs: list[SemanticDocument] = []
        if entries is not None:
            for key, text in entries:
                self.add(key, text)

    def add(self, key: str, text: str) -> None:
        content = (text or "").strip()
        if not content:
            return
        if self._rust_index is not None:
            self._rust_index.add(str(key), content)
        else:
            self._docs.append(
                SemanticDocument(
                    key=str(key),
                    text=content,
                    vector=vectorize_text(content),
                )
            )

    def search(self, query: str, top_k: int = 5) -> list[dict[str, float | str]]:
        if self._rust_index is not None:
            results = self._rust_index.search(query, top_k)
            return [
                {"key": k, "text": t, "similarity": round(float(s), 4)}
                for k, t, s in results
            ]
        # Python fallback
        query_vector = vectorize_text(query)
        if not query_vector:
            return []
        ranked: list[tuple[float, SemanticDocument]] = []
        for doc in self._docs:
            score = cosine_similarity(query_vector, doc.vector)
            if score <= 0.0:
                continue
            ranked.append((score, doc))
        ranked.sort(key=lambda item: (-item[0], item[1].key))
        return [
            {"key": doc.key, "text": doc.text, "similarity": round(score, 4)}
            for score, doc in ranked[:top_k]
        ]