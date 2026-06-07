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

import json
import os
import time
from typing import Any

from rlm.obsidian_rag.bm25 import BM25Index
from rlm.obsidian_rag.hypergraph import HyperGraph, build_hypergraph
from rlm.obsidian_rag.models import Note
from rlm.obsidian_rag.reader import (
    DEFAULT_EXCLUDES,
    iter_markdown_files,
    read_paths_parallel,
)

CACHE_DIRNAME = ".arkhe_rag"
CACHE_FILENAME = "index.json"
CACHE_VERSION = 1


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
    # Estatísticas
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return {
            "notes": len(self.notes),
            "chunks": len(self.chunks_by_id),
            "workers": os.cpu_count(),
            "build_ms": round(self.build_ms, 1),
            **self.hypergraph.stats(),
        }


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
