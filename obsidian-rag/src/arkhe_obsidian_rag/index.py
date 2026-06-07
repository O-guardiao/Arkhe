"""
index.py — VaultIndex: orquestra leitura paralela + BM25 + hipergrafo + cache.

Pipeline:
    1. varre o vault e lê+parseia .md EM PARALELO REAL (reader.py)
    2. constrói BM25 sobre os chunks (bm25.py)
    3. constrói o hipergrafo (hypergraph.py)
    4. persiste tudo em <vault>/.arkhe_rag/index.json

Refresh incremental: em runs subsequentes só re-parseia arquivos com mtime
diferente (ou novos); remove os deletados. Parsing é o custo dominante, então
reconstruir BM25/hipergrafo a partir das notas em memória é barato.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import TYPE_CHECKING, Any

from arkhe_obsidian_rag.bm25 import BM25Index
from arkhe_obsidian_rag.hypergraph import HyperGraph, build_hypergraph
from arkhe_obsidian_rag.models import Note
from arkhe_obsidian_rag.reader import (
    DEFAULT_EXCLUDES,
    iter_markdown_files,
    read_paths_parallel,
)

if TYPE_CHECKING:
    from arkhe_obsidian_rag.embeddings import EmbeddingProvider

CACHE_DIRNAME = ".arkhe_rag"
CACHE_FILENAME = "index.json"
CACHE_VERSION = 1


def _text_hash(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=12).hexdigest()


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


class VaultIndex:
    """Índice pesquisável de um vault Obsidian (notas + BM25 + hipergrafo)."""

    def __init__(
        self,
        vault_path: str,
        notes: list[Note],
        bm25: BM25Index,
        hypergraph: HyperGraph,
        *,
        build_ms: float = 0.0,
    ):
        self.vault_path = os.path.abspath(vault_path)
        self.notes = notes
        self.by_path: dict[str, Note] = {n.path: n for n in notes}
        self.chunks_by_id = {c.chunk_id: c for n in notes for c in n.chunks}
        self.bm25 = bm25
        self.hypergraph = hypergraph
        self.build_ms = build_ms
        # vetores semânticos opcionais: chunk_id -> vetor (preenchido sob demanda)
        self.vectors: dict[str, list[float]] = {}
        self.embedder_name: str | None = None

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        vault_path: str,
        notes: list[Note],
        *,
        build_ms: float = 0.0,
    ) -> VaultIndex:
        """Constrói BM25 + hipergrafo a partir de uma lista de notas."""
        bm25 = BM25Index()
        bm25.build([(c.chunk_id, c.text) for n in notes for c in n.chunks])
        hg = build_hypergraph(notes)
        return cls(vault_path, notes, bm25, hg, build_ms=build_ms)

    @classmethod
    def from_vault(
        cls,
        vault_path: str,
        *,
        workers: int | None = None,
        max_chunk_chars: int = 1200,
        use_cache: bool = True,
        excludes=DEFAULT_EXCLUDES,
    ) -> VaultIndex:
        """
        Carrega o índice do vault, usando/atualizando o cache se possível.

        Faz refresh incremental: só re-parseia .md alterados desde o cache.
        """
        t0 = time.perf_counter()
        current = {abs_p: rel_p for abs_p, rel_p in iter_markdown_files(vault_path, excludes)}
        # mapa rel -> (abs, mtime)
        disk: dict[str, tuple[str, float]] = {}
        for abs_p, rel_p in current.items():
            try:
                disk[rel_p] = (abs_p, os.stat(abs_p).st_mtime)
            except OSError:
                continue

        cached_notes: dict[str, Note] = {}
        if use_cache:
            cached_notes = _load_cached_notes(vault_path)

        to_read: list[tuple[str, str]] = []
        fresh: list[Note] = []
        for rel, (abs_p, mtime) in disk.items():
            cn = cached_notes.get(rel)
            if cn is not None and abs(cn.mtime - mtime) < 1e-6:
                fresh.append(cn)
            else:
                to_read.append((abs_p, rel))

        reparsed = read_paths_parallel(to_read, workers=workers, max_chunk_chars=max_chunk_chars)
        notes = fresh + reparsed
        build_ms = (time.perf_counter() - t0) * 1000.0
        idx = cls.build(vault_path, notes, build_ms=build_ms)
        if use_cache:
            idx.save_cache()
        return idx

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def cache_path(self) -> str:
        return os.path.join(self.vault_path, CACHE_DIRNAME, CACHE_FILENAME)

    def save_cache(self) -> str:
        path = self.cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "vault": self.vault_path,
            "notes": [n.to_dict() for n in self.notes],
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        return path

    # ------------------------------------------------------------------
    # Vetores semânticos (opcional)
    # ------------------------------------------------------------------

    def _vectors_cache_path(self, embedder_name: str) -> str:
        fname = f"vectors__{_safe_name(embedder_name)}.json"
        return os.path.join(self.vault_path, CACHE_DIRNAME, fname)

    def ensure_vectors(self, embedder: EmbeddingProvider) -> None:
        """
        Garante vetores para todos os chunks atuais usando `embedder`.

        Cacheia por HASH do texto do chunk (em .arkhe_rag/vectors__<emb>.json):
        chunks idênticos/renomeados reusam o vetor; só o que mudou é re-embeddado.
        """
        chunk_text = {cid: c.text for cid, c in self.chunks_by_id.items()}
        chunk_to_hash = {cid: _text_hash(t) for cid, t in chunk_text.items()}

        cache_path = self._vectors_cache_path(embedder.name)
        by_hash: dict[str, list[float]] = {}
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    payload = json.load(f)
                if payload.get("embedder") == embedder.name:
                    by_hash = payload.get("vectors", {})
            except (OSError, json.JSONDecodeError):
                by_hash = {}

        # embute apenas hashes ausentes
        missing = {h: chunk_text[cid] for cid, h in chunk_to_hash.items() if h not in by_hash}
        if missing:
            hashes = list(missing)
            embedded = embedder.embed([missing[h] for h in hashes])
            for h, vec in zip(hashes, embedded, strict=False):
                by_hash[h] = vec
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"embedder": embedder.name, "vectors": by_hash}, f)
            os.replace(tmp, cache_path)

        # mantém só os hashes vivos em memória, mapeados por chunk_id
        self.vectors = {cid: by_hash[h] for cid, h in chunk_to_hash.items() if h in by_hash}
        self.embedder_name = embedder.name

    # ------------------------------------------------------------------
    # Estatísticas
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        s = {
            "notes": len(self.notes),
            "chunks": len(self.chunks_by_id),
            "workers": os.cpu_count(),
            "build_ms": round(self.build_ms, 1),
            **self.hypergraph.stats(),
        }
        if self.embedder_name:
            s["embedder"] = self.embedder_name
            s["vectors"] = len(self.vectors)
        return s


def _load_cached_notes(vault_path: str) -> dict[str, Note]:
    path = os.path.join(os.path.abspath(vault_path), CACHE_DIRNAME, CACHE_FILENAME)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("version") != CACHE_VERSION:
            return {}
        return {d["path"]: Note.from_dict(d) for d in payload.get("notes", [])}
    except (OSError, json.JSONDecodeError, KeyError):
        return {}
