"""
reader.py — Leitura PARALELA REAL do vault Obsidian.

O gargalo do retrieval em vaults grandes é ler+parsear centenas/milhares de
.md. Fazer isso sequencialmente é lento. Aqui usamos ProcessPoolExecutor:
processos separados, sem disputa de GIL, então o parsing (regex/CPU) escala
de verdade com os núcleos da máquina.

Funções top-level apenas (precisam ser picklable para o pool).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor

from rlm.obsidian_rag.models import Note
from rlm.obsidian_rag.parser import parse_note

# Pastas ignoradas por padrão (config do Obsidian, caches, VCS, saída do RAG).
DEFAULT_EXCLUDES = (
    ".obsidian",
    ".trash",
    ".git",
    ".arkhe_rag",
    "node_modules",
)


def iter_markdown_files(
    vault_path: str, excludes: Iterable[str] = DEFAULT_EXCLUDES
) -> Iterator[tuple[str, str]]:
    """Gera (caminho_absoluto, caminho_relativo_posix) de cada .md do vault."""
    excl = set(excludes)
    root = os.path.abspath(vault_path)
    for dirpath, dirnames, filenames in os.walk(root):
        # poda in-place: evita descer em diretórios excluídos
        dirnames[:] = [d for d in dirnames if d not in excl and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
            yield abs_path, rel


def _read_and_parse(task: tuple[str, str, int]) -> Note | None:
    """Worker: lê um arquivo do disco e o parseia. Roda em processo separado."""
    abs_path, rel_path, max_chunk_chars = task
    try:
        st = os.stat(abs_path)
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None
    return parse_note(rel_path, content, st.st_mtime, st.st_size, max_chunk_chars)


def read_paths_parallel(
    tasks: list[tuple[str, str]],
    *,
    workers: int | None = None,
    max_chunk_chars: int = 1200,
) -> list[Note]:
    """
    Lê+parseia uma lista de (abs_path, rel_path) em paralelo real.

    Cai para execução sequencial quando há pouco trabalho ou workers==1
    (evita overhead de spawn de processos em vaults pequenos).
    """
    if not tasks:
        return []
    payload = [(a, r, max_chunk_chars) for a, r in tasks]
    cpu = os.cpu_count() or 1
    workers = max(1, min(workers or cpu, cpu, len(payload)))

    if workers == 1 or len(payload) <= 4:
        return [n for n in map(_read_and_parse, payload) if n is not None]

    chunksize = max(1, len(payload) // (workers * 4))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        results = ex.map(_read_and_parse, payload, chunksize=chunksize)
        return [n for n in results if n is not None]


def read_vault_parallel(
    vault_path: str,
    *,
    workers: int | None = None,
    max_chunk_chars: int = 1200,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
) -> list[Note]:
    """Varre o vault inteiro e lê+parseia todos os .md em paralelo real."""
    tasks = list(iter_markdown_files(vault_path, excludes))
    return read_paths_parallel(tasks, workers=workers, max_chunk_chars=max_chunk_chars)
