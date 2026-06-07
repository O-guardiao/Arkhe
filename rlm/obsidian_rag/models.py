"""
models.py — Estruturas de dados do motor Obsidian-RAG-hipergrafo.

Tudo aqui é dataclass simples e serializável (para cache em disco e para o
JSON entregue ao plugin/hook do Obsidian). Nenhuma dependência externa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _approx_tokens(text: str) -> int:
    """Estimativa barata de tokens (~4 chars/token). Suficiente para budget."""
    return max(1, len(text) // 4)


@dataclass(slots=True)
class Chunk:
    """Um trecho contínuo de uma nota, ancorado no heading mais próximo."""

    note_path: str  # caminho relativo ao vault (posix)
    chunk_id: str  # f"{note_path}#{idx}"
    index: int  # ordem dentro da nota
    heading: str  # heading mais próximo (contexto)
    text: str  # conteúdo bruto do trecho
    start_line: int  # linha inicial (1-based) no arquivo original
    tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "heading": self.heading,
            "text": self.text,
            "start_line": self.start_line,
            "tokens": self.tokens,
        }


@dataclass(slots=True)
class Note:
    """Uma nota .md já parseada (resultado do worker paralelo)."""

    path: str  # relativo ao vault (posix)
    title: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)  # frontmatter + inline #tags
    wikilinks: list[str] = field(default_factory=list)  # alvos normalizados
    headings: list[str] = field(default_factory=list)
    body: str = ""
    chunks: list[Chunk] = field(default_factory=list)
    mtime: float = 0.0
    size: int = 0
    word_count: int = 0

    @property
    def folder(self) -> str:
        i = self.path.rfind("/")
        return self.path[:i] if i >= 0 else ""

    @property
    def basename(self) -> str:
        name = self.path.rsplit("/", 1)[-1]
        return name[:-3] if name.endswith(".md") else name

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "frontmatter": self.frontmatter,
            "tags": self.tags,
            "wikilinks": self.wikilinks,
            "headings": self.headings,
            "body": self.body,
            "chunks": [c.to_dict() for c in self.chunks],
            "mtime": self.mtime,
            "size": self.size,
            "word_count": self.word_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Note:
        chunks = [
            Chunk(
                note_path=d["path"],
                chunk_id=c["chunk_id"],
                index=i,
                heading=c.get("heading", ""),
                text=c.get("text", ""),
                start_line=c.get("start_line", 0),
                tokens=c.get("tokens", 0),
            )
            for i, c in enumerate(d.get("chunks", []))
        ]
        return cls(
            path=d["path"],
            title=d.get("title", ""),
            frontmatter=d.get("frontmatter", {}),
            tags=d.get("tags", []),
            wikilinks=d.get("wikilinks", []),
            headings=d.get("headings", []),
            body=d.get("body", ""),
            chunks=chunks,
            mtime=d.get("mtime", 0.0),
            size=d.get("size", 0),
            word_count=d.get("word_count", 0),
        )


@dataclass(slots=True)
class HyperEdge:
    """
    Hiperaresta: conecta um CONJUNTO de notas (não apenas um par).

    type: 'tag' | 'folder' | 'links' | 'cocitation'
    members: caminhos (relativos) das notas conectadas.
    weight: peso base da aresta (antes da normalização por tamanho).
    """

    id: str
    type: str
    label: str
    members: tuple[str, ...]
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "members": list(self.members),
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HyperEdge:
        return cls(
            id=d["id"],
            type=d["type"],
            label=d.get("label", ""),
            members=tuple(d.get("members", [])),
            weight=d.get("weight", 1.0),
        )


@dataclass(slots=True)
class RetrievedChunk:
    chunk: Chunk
    score: float


@dataclass(slots=True)
class RetrievedNote:
    """Uma nota selecionada para o context pack, com proveniência do score."""

    path: str
    title: str
    score: float
    lexical: float
    graph: float
    semantic: float
    tags: list[str]
    wikilinks: list[str]
    reasons: list[str]
    chunks: list[RetrievedChunk]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "score": round(self.score, 6),
            "lexical": round(self.lexical, 6),
            "graph": round(self.graph, 6),
            "semantic": round(self.semantic, 6),
            "tags": self.tags,
            "wikilinks": self.wikilinks,
            "reasons": self.reasons,
            "chunks": [{**c.chunk.to_dict(), "score": round(c.score, 6)} for c in self.chunks],
        }


@dataclass(slots=True)
class ContextPack:
    """Pacote de contexto pré-LLM entregue ao plugin/hook do Obsidian."""

    query: str
    vault: str
    generated_at: str
    notes: list[RetrievedNote]
    context_markdown: str
    stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "vault": self.vault,
            "generated_at": self.generated_at,
            "stats": self.stats,
            "notes": [n.to_dict() for n in self.notes],
            "context_markdown": self.context_markdown,
        }
