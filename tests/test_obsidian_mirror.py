"""
test_obsidian_mirror.py — Testes do Obsidian Mirror (KB ↔ vault).

Valida: export de documento, export_all, import de conceitos,
parse de frontmatter, split body.
"""
from __future__ import annotations

import json
import os

import pytest


@pytest.fixture
def kb_db_path(tmp_path):
    return str(tmp_path / "global" / "knowledge_base.db")


@pytest.fixture
def kb(kb_db_path):
    from rlm.core.memory.knowledge_base import GlobalKnowledgeBase
    return GlobalKnowledgeBase(db_path=kb_db_path)


@pytest.fixture
def vault_path(tmp_path):
    return str(tmp_path / "vault")


# ===========================================================================
# Export tests
# ===========================================================================


class TestObsidianExport:

    def test_export_document_creates_file(self, kb, vault_path):
        from rlm.core.integrations.obsidian_mirror import export_document_to_vault

        doc_id = kb.add_document(
            title="Deploy Docker",
            summary="Pipeline CI/CD.",
            full_context="Detalhes completos do deploy...",
            tags=["deploy", "docker"],
            importance=0.85,
            domain="devops",
            source_sessions=["sess-1"],
        )
        doc = kb.get_document(doc_id)
        path = export_document_to_vault(doc, vault_path)

        assert os.path.exists(path)
        assert path.endswith(".md")
        assert "conhecimento" in path

        content = open(path, encoding="utf-8").read()
        assert "---" in content
        assert "Deploy Docker" in content
        assert "Pipeline CI/CD" in content
        assert "Detalhes completos" in content
        assert "sess-1" in content

    def test_export_document_sanitizes_filename(self, kb, vault_path):
        from rlm.core.integrations.obsidian_mirror import export_document_to_vault

        doc_id = kb.add_document(
            title="Config: nginx/reverse?proxy",
            summary="Summary.",
            tags=["nginx"],
            importance=0.5,
            domain="infra",
            source_sessions=[],
        )
        doc = kb.get_document(doc_id)
        path = export_document_to_vault(doc, vault_path)

        filename = os.path.basename(path)
        assert ":" not in filename
        assert "/" not in filename
        assert "?" not in filename

    def test_export_all_writes_multiple(self, kb, vault_path):
        from rlm.core.integrations.obsidian_mirror import export_all_to_vault

        kb.add_document(title="Doc1", summary="S1", tags=["a"], importance=0.5,
                        domain="d", source_sessions=["s1"])
        kb.add_document(title="Doc2", summary="S2", tags=["b"], importance=0.5,
                        domain="d", source_sessions=["s1"])

        paths = export_all_to_vault(kb, vault_path)
        assert len(paths) == 2
        for p in paths:
            assert os.path.exists(p)


# ===========================================================================
# Import tests
# ===========================================================================


class TestObsidianImport:

    def _write_conceito(self, vault_path: str, filename: str, content: str):
        conceitos_dir = os.path.join(vault_path, "conceitos")
        os.makedirs(conceitos_dir, exist_ok=True)
        filepath = os.path.join(conceitos_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def test_import_conceito_basic(self, kb, vault_path):
        from rlm.core.integrations.obsidian_mirror import import_conceitos_from_vault

        self._write_conceito(vault_path, "mutex.md", """---
title: "Mutex e Semáforos"
domain: concurrency
tags: ["mutex", "semaphore"]
importance: 0.7
---

## Resumo
Mecanismos de sincronização para acesso a recursos compartilhados.

## Contexto Completo
Mutex garante exclusão mútua. Semáforo permite N acessos simultâneos.
""")
        imported = import_conceitos_from_vault(vault_path, kb)
        assert len(imported) == 1

        doc = kb.get_document(imported[0])
        assert doc is not None
        assert "Mutex" in doc["title"]
        assert "sincronização" in doc["summary"]
        assert doc["domain"] == "concurrency"

    def test_import_skips_duplicate(self, kb, vault_path):
        from rlm.core.integrations.obsidian_mirror import import_conceitos_from_vault

        # Pre-create a document with same title
        kb.add_document(
            title="Mutex e Semáforos",
            summary="Existing.",
            tags=["mutex"],
            importance=0.5,
            domain="concurrency",
            source_sessions=[],
        )

        self._write_conceito(vault_path, "mutex.md", """---
title: "Mutex e Semáforos"
domain: concurrency
---

Duplicate should be skipped.
""")

        imported = import_conceitos_from_vault(vault_path, kb)
        assert len(imported) == 0

    def test_import_no_conceitos_dir(self, kb, vault_path):
        from rlm.core.integrations.obsidian_mirror import import_conceitos_from_vault
        imported = import_conceitos_from_vault(vault_path, kb)
        assert imported == []

    def test_import_no_frontmatter(self, kb, vault_path):
        from rlm.core.integrations.obsidian_mirror import import_conceitos_from_vault

        self._write_conceito(vault_path, "plain.md", """
Just a plain note without frontmatter.
Some content here.
""")
        imported = import_conceitos_from_vault(vault_path, kb)
        assert len(imported) == 0


# ===========================================================================
# Helper function tests
# ===========================================================================


class TestFrontmatterParsing:

    def test_parse_with_frontmatter(self):
        from rlm.core.integrations.obsidian_mirror import _parse_frontmatter

        content = """---
title: "Test"
domain: dev
importance: 0.8
tags: ["a", "b"]
---

Body content here.
"""
        fm, body = _parse_frontmatter(content)
        assert fm["title"] == '"Test"'
        assert fm["domain"] == "dev"
        assert fm["importance"] == 0.8
        assert isinstance(fm["tags"], list)
        assert "Body content here." in body

    def test_parse_without_frontmatter(self):
        from rlm.core.integrations.obsidian_mirror import _parse_frontmatter

        content = "Just plain text."
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == "Just plain text."


class TestBodySplitting:

    def test_split_with_resumo_section(self):
        from rlm.core.integrations.obsidian_mirror import _split_body

        body = """## Resumo
This is the summary.

## Contexto Completo
Full details here.
"""
        summary, full = _split_body(body)
        assert summary == "This is the summary."
        assert "Full details" in full

    def test_split_fallback_paragraphs(self):
        from rlm.core.integrations.obsidian_mirror import _split_body

        body = """First paragraph is summary.

Second paragraph is context.

Third paragraph too."""
        summary, full = _split_body(body)
        assert summary == "First paragraph is summary."
        assert "Second paragraph" in full
