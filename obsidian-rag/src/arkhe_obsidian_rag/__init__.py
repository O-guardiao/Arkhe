"""
arkhe_obsidian_rag — Motor RAG-hipergrafo focado em Obsidian.

Leitura PARALELA REAL de muitos .md → índice (BM25 + hipergrafo) → context pack
pré-LLM. NÃO é conversacional: serve como backend de um hook/plugin do Obsidian,
entregando contexto pronto para o prompt antes da chamada ao modelo.

Uso típico (biblioteca):

    from arkhe_obsidian_rag import VaultIndex, retrieve
    index = VaultIndex.from_vault("/caminho/vault")
    pack = retrieve(index, "como funciona X?")
    print(pack.context_markdown)

Uso via CLI (para o plugin chamar e ler JSON do stdout):

    python -m arkhe_obsidian_rag retrieve --vault /caminho/vault --query "..."
"""

from __future__ import annotations

from arkhe_obsidian_rag.index import VaultIndex
from arkhe_obsidian_rag.models import ContextPack, HyperEdge, Note
from arkhe_obsidian_rag.reader import read_vault_parallel
from arkhe_obsidian_rag.retriever import retrieve

__all__ = [
    "VaultIndex",
    "ContextPack",
    "HyperEdge",
    "Note",
    "read_vault_parallel",
    "retrieve",
]
