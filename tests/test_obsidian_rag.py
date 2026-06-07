"""
test_obsidian_rag.py — Testes do motor RAG-hipergrafo para Obsidian.

Cobre: parsing (frontmatter/tags/wikilinks/chunks), leitura paralela real,
BM25, construção e expansão do hipergrafo, retrieval ponta-a-ponta, cache
incremental e a CLI (JSON no stdout).
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from rlm.obsidian_rag import VaultIndex, retrieve
from rlm.obsidian_rag.bm25 import BM25Index
from rlm.obsidian_rag.hypergraph import build_hypergraph
from rlm.obsidian_rag.parser import parse_note, tokenize
from rlm.obsidian_rag.reader import iter_markdown_files, read_vault_parallel

# ---------------------------------------------------------------------------
# Vault de exemplo
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path):
    """Cria um vault pequeno com tags, wikilinks e pastas."""
    (tmp_path / "conceitos").mkdir()
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")

    (tmp_path / "arkhe.md").write_text(
        "---\n"
        "title: Arkhe\n"
        "tags: [filosofia, principio]\n"
        "---\n"
        "# Arkhe\n"
        "Arkhe é o princípio originário de todas as coisas.\n"
        "Ver também [[Tales]] e [[Agua]].\n\n"
        "## Detalhes\n"
        "O conceito vem dos pré-socráticos. #grego\n",
        encoding="utf-8",
    )
    (tmp_path / "conceitos" / "Tales.md").write_text(
        "---\ntags: [filosofia]\n---\n"
        "# Tales de Mileto\n"
        "Tales propôs que a água é o arkhe, o princípio de tudo.\n",
        encoding="utf-8",
    )
    (tmp_path / "conceitos" / "Agua.md").write_text(
        "# Agua\nA água como elemento fundamental. #grego\n",
        encoding="utf-8",
    )
    (tmp_path / "nao_relacionado.md").write_text(
        "# Receita de bolo\nMisture farinha e ovos.\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_frontmatter_tags_links():
    content = (
        "---\ntitle: Nota\ntags: [a, b]\n---\n"
        "# Cabeçalho\nTexto com [[Alvo|alias]] e #inline.\n"
        "```\n#nao_e_tag dentro de code\n[[NaoLink]]\n```\n"
    )
    note = parse_note("Nota.md", content, mtime=1.0, size=len(content))
    assert note.title == "Nota"
    assert set(note.tags) >= {"a", "b", "inline"}
    assert "nao_e_tag" not in note.tags  # ignorado dentro de bloco de código
    assert note.wikilinks == ["Alvo"]  # alias removido, link em code ignorado
    assert "Cabeçalho" in note.headings


def test_chunking_respects_budget():
    body = "# H\n" + ("palavra " * 2000)
    note = parse_note("big.md", body, mtime=1.0, size=len(body), max_chunk_chars=500)
    assert len(note.chunks) > 1
    assert all(len(c.text) <= 600 for c in note.chunks)
    assert all(c.heading == "H" for c in note.chunks)


def test_tokenize_unicode():
    assert tokenize("Água é Princípio!") == ["água", "é", "princípio"]


# ---------------------------------------------------------------------------
# Leitura paralela
# ---------------------------------------------------------------------------


def test_iter_skips_obsidian_dir(vault):
    rels = {rel for _, rel in iter_markdown_files(str(vault))}
    assert "arkhe.md" in rels
    assert "conceitos/Tales.md" in rels
    assert not any(".obsidian" in r for r in rels)


def test_read_vault_parallel_reads_all(vault):
    notes = read_vault_parallel(str(vault), workers=2)
    paths = {n.path for n in notes}
    assert paths == {
        "arkhe.md",
        "conceitos/Tales.md",
        "conceitos/Agua.md",
        "nao_relacionado.md",
    }


def test_parallel_matches_sequential(vault):
    seq = {n.path: n for n in read_vault_parallel(str(vault), workers=1)}
    par = {n.path: n for n in read_vault_parallel(str(vault), workers=4)}
    assert seq.keys() == par.keys()
    for path in seq:
        assert seq[path].tags == par[path].tags
        assert seq[path].wikilinks == par[path].wikilinks


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------


def test_bm25_ranks_relevant_first():
    idx = BM25Index()
    idx.build(
        [
            ("d1", "arkhe princípio originário pré-socráticos"),
            ("d2", "receita de bolo farinha ovos"),
            ("d3", "tales água arkhe princípio"),
        ]
    )
    results = idx.search("arkhe princípio", top_k=3)
    assert results[0][0] in {"d1", "d3"}
    assert results[0][1] > 0
    ids = [r[0] for r in results]
    assert ids.index("d2") == len(ids) - 1 if "d2" in ids else True


# ---------------------------------------------------------------------------
# Hipergrafo
# ---------------------------------------------------------------------------


def test_hypergraph_builds_edges(vault):
    notes = read_vault_parallel(str(vault), workers=2)
    hg = build_hypergraph(notes)
    types = {e.type for e in hg.edges}
    assert "tag" in types  # filosofia compartilhada por arkhe + Tales
    assert "folder" in types  # Tales + Agua na pasta conceitos
    assert "links" in types  # arkhe -> Tales, Agua

    # arkhe linka Tales e Agua → devem ser vizinhos
    neigh = hg.neighbors("arkhe.md")
    assert "conceitos/Tales.md" in neigh
    assert "conceitos/Agua.md" in neigh


def test_hypergraph_expand_brings_neighbors(vault):
    notes = read_vault_parallel(str(vault), workers=2)
    hg = build_hypergraph(notes)
    activation = hg.expand({"arkhe.md": 1.0}, hops=1)
    assert "conceitos/Tales.md" in activation
    assert "arkhe.md" not in activation  # sementes não voltam


# ---------------------------------------------------------------------------
# Retrieval ponta-a-ponta
# ---------------------------------------------------------------------------


def test_retrieve_returns_context_pack(vault):
    index = VaultIndex.from_vault(str(vault), workers=2, use_cache=False)
    pack = retrieve(index, "o que é o arkhe?", top_notes=5)
    assert pack.notes, "deveria recuperar ao menos uma nota"
    top = pack.notes[0]
    assert top.path in {"arkhe.md", "conceitos/Tales.md"}
    assert "arkhe" in pack.context_markdown.lower()
    # nota não relacionada não deve aparecer no topo
    assert pack.notes[0].path != "nao_relacionado.md"
    assert pack.stats["selected_notes"] == len(pack.notes)


def test_retrieve_graph_expansion_includes_linked(vault):
    index = VaultIndex.from_vault(str(vault), workers=2, use_cache=False)
    # query casa fortemente com arkhe; expansão deve trazer notas linkadas/co-tag
    pack = retrieve(index, "princípio originário arkhe", top_notes=8, hops=1)
    paths = {n.path for n in pack.notes}
    assert "arkhe.md" in paths
    assert any(n.graph > 0 for n in pack.notes)


def test_max_chars_budget_respected(vault):
    index = VaultIndex.from_vault(str(vault), workers=2, use_cache=False)
    pack = retrieve(index, "arkhe água tales", max_chars=200)
    assert len(pack.context_markdown) <= 200 + 600  # markdown header overhead


# ---------------------------------------------------------------------------
# Cache incremental
# ---------------------------------------------------------------------------


def test_cache_incremental_refresh(vault):
    idx1 = VaultIndex.from_vault(str(vault), workers=2, use_cache=True)
    n1 = len(idx1.notes)
    # adiciona nova nota e refaz; cache deve ser usado + nova nota parseada
    (vault / "nova.md").write_text("# Nova\nconteúdo novo sobre arkhe.\n", encoding="utf-8")
    idx2 = VaultIndex.from_vault(str(vault), workers=2, use_cache=True)
    assert len(idx2.notes) == n1 + 1
    assert "nova.md" in idx2.by_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_retrieve_json_stdout(vault):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "rlm.obsidian_rag",
            "retrieve",
            "--vault",
            str(vault),
            "--query",
            "arkhe princípio",
            "--no-cache",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)  # stdout deve ser JSON puro
    assert data["query"] == "arkhe princípio"
    assert data["notes"]
    assert "context_markdown" in data


def test_cli_index_stats(vault):
    proc = subprocess.run(
        [sys.executable, "-m", "rlm.obsidian_rag", "index", "--vault", str(vault)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    stats = json.loads(proc.stdout)
    assert stats["notes"] >= 4
    assert stats["hyperedges"] >= 1
