"""
test_obsidian_bridge.py — Testes do ObsidianBridge (KB ↔ vault event-driven).

Valida: hooks do KB, export event-driven, import conceitos com hash tracking,
sync corrections (metadata + human notes), conflict resolution, wikilinks→edges,
session log export, knowledge graph Mermaid, MOCs, backward compat.
"""
from __future__ import annotations

import json
import os
import time

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def kb_db_path(tmp_path):
    return str(tmp_path / "global" / "knowledge_base.db")


@pytest.fixture
def kb(kb_db_path):
    from rlm.core.knowledge_base import GlobalKnowledgeBase
    return GlobalKnowledgeBase(db_path=kb_db_path)


@pytest.fixture
def vault_path(tmp_path):
    return str(tmp_path / "vault")


@pytest.fixture
def bridge(vault_path, kb):
    from rlm.core.obsidian_bridge import ObsidianBridge
    return ObsidianBridge(vault_path=vault_path, kb=kb)


@pytest.fixture
def sample_doc_id(kb):
    """Cria um documento no KB e retorna o ID."""
    return kb.add_document(
        title="Partition Key Design",
        summary="Escolha chaves com alta cardinalidade",
        full_context="Detalhes completos sobre design de partition keys...",
        tags=["cosmosdb", "partitioning"],
        domain="database",
        importance=0.8,
        source_sessions=["session-abc"],
    )


# ===========================================================================
# 1. Directory Setup
# ===========================================================================


class TestDirectorySetup:

    def test_creates_vault_dirs(self, bridge, vault_path):
        expected_dirs = [
            "conhecimento", "sessoes", "conceitos",
            "conflitos", "conflitos/resolvidos",
            "moc", "grafos",
        ]
        for d in expected_dirs:
            assert os.path.isdir(os.path.join(vault_path, d)), f"Missing: {d}"

    def test_creates_sync_table(self, bridge, kb):
        import sqlite3
        conn = sqlite3.connect(kb.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vault_sync'"
        )
        assert cursor.fetchone() is not None
        conn.close()


# ===========================================================================
# 2. Export: KB → Vault (event-driven)
# ===========================================================================


class TestExportDocCreated:

    def test_on_doc_created_writes_file(self, bridge, kb, sample_doc_id, vault_path):
        path = bridge.on_doc_created(sample_doc_id)
        assert os.path.isfile(path)
        assert "Partition Key Design" in os.path.basename(path)

    def test_on_doc_created_content_has_frontmatter(self, bridge, kb, sample_doc_id):
        path = bridge.on_doc_created(sample_doc_id)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert content.startswith("---")
        assert "id:" in content
        assert "importance: 0.8" in content
        assert "database" in content

    def test_on_doc_created_content_has_body_sections(self, bridge, kb, sample_doc_id):
        path = bridge.on_doc_created(sample_doc_id)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "## Resumo" in content
        assert "## Contexto Completo" in content
        assert "## Notas Humanas" in content
        assert "Escolha chaves com alta cardinalidade" in content

    def test_on_doc_created_nonexistent_returns_empty(self, bridge):
        result = bridge.on_doc_created("nonexistent-id")
        assert result == ""

    def test_on_doc_created_includes_relations_section(self, bridge, kb):
        id1 = kb.add_document(title="Doc A", summary="A summary")
        id2 = kb.add_document(title="Doc B", summary="B summary")
        kb.add_edge(id1, id2, "related")
        path = bridge.on_doc_created(id1)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "## Relacionamentos" in content
        assert "[[Doc B]]" in content


class TestExportDocUpdated:

    def test_on_doc_updated_overwrites_file(self, bridge, kb, sample_doc_id, vault_path):
        bridge.on_doc_created(sample_doc_id)
        kb.update_document(sample_doc_id, summary="Summary v2")
        path = bridge.on_doc_updated(sample_doc_id)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Summary v2" in content

    def test_on_doc_updated_nonexistent_returns_empty(self, bridge):
        assert bridge.on_doc_updated("nonexistent-id") == ""


class TestExportEdgeCreated:

    def test_on_edge_created_updates_both_files(self, bridge, kb):
        id1 = kb.add_document(title="Causa", summary="S1")
        id2 = kb.add_document(title="Efeito", summary="S2")
        bridge.on_doc_created(id1)
        bridge.on_doc_created(id2)
        bridge.on_edge_created(id1, id2, "causes")
        # Both files should be updated with the relationship
        for did in (id1, id2):
            doc = kb.get_document(did)
            path = bridge.on_doc_created(did)
            assert os.path.isfile(path)


class TestExportAll:

    def test_export_all_creates_files(self, bridge, kb, vault_path):
        kb.add_document(title="Doc 1", summary="S1")
        kb.add_document(title="Doc 2", summary="S2")
        kb.add_document(title="Doc 3", summary="S3")
        paths = bridge.export_all()
        assert len(paths) == 3
        for p in paths:
            assert os.path.isfile(p)

    def test_export_all_creates_index(self, bridge, kb, vault_path):
        kb.add_document(title="Doc 1", summary="S1", domain="testing")
        bridge.export_all()
        index_path = os.path.join(vault_path, "_index.md")
        assert os.path.isfile(index_path)


# ===========================================================================
# 3. KB Hooks Integration
# ===========================================================================


class TestKBHooks:

    def test_register_hook_fires_on_add(self, kb, vault_path):
        events = []
        def recorder(event_type, *args):
            events.append((event_type, args))
        kb.register_hook(recorder)
        kb.add_document(title="Test", summary="Test")
        assert len(events) == 1
        assert events[0][0] == "doc_created"

    def test_register_hook_fires_on_update(self, kb, vault_path):
        events = []
        def recorder(event_type, *args):
            events.append((event_type, args))
        doc_id = kb.add_document(title="Test", summary="Test")
        kb.register_hook(recorder)
        kb.update_document(doc_id, summary="Updated")
        assert any(e[0] == "doc_updated" for e in events)

    def test_register_hook_fires_on_edge(self, kb, vault_path):
        events = []
        def recorder(event_type, *args):
            events.append((event_type, args))
        id1 = kb.add_document(title="A", summary="A")
        id2 = kb.add_document(title="B", summary="B")
        kb.register_hook(recorder)
        kb.add_edge(id1, id2, "related")
        assert any(e[0] == "edge_created" for e in events)

    def test_unregister_hook_stops_firing(self, kb):
        events = []
        def recorder(event_type, *args):
            events.append(event_type)
        kb.register_hook(recorder)
        kb.add_document(title="A", summary="A")
        kb.unregister_hook(recorder)
        kb.add_document(title="B", summary="B")
        assert len(events) == 1

    def test_hook_exception_does_not_crash(self, kb):
        def bad_hook(event_type, *args):
            raise RuntimeError("Hook crashed")
        kb.register_hook(bad_hook)
        # Should not raise
        doc_id = kb.add_document(title="X", summary="X")
        assert doc_id

    def test_handle_kb_event_dispatches(self, bridge, kb, sample_doc_id, vault_path):
        bridge.handle_kb_event("doc_created", sample_doc_id)
        filepath = os.path.join(vault_path, "conhecimento", "Partition Key Design.md")
        assert os.path.isfile(filepath)


# ===========================================================================
# 4. Session Log Export
# ===========================================================================


class TestSessionLogExport:

    def test_export_session_log_creates_file(self, bridge, vault_path):
        data = {
            "session_id": "abc-123-def",
            "model": "gpt-4o",
            "created_at": "2025-01-01T10:00:00",
            "status": "completed",
            "total_completions": 5,
            "total_tokens_used": 1200,
            "prompt": "Explain RLM architecture",
            "iterations": [
                {"repl_code": "print('hello')", "output": "hello"},
            ],
            "final_output": "RLM uses recursive self-improvement...",
            "kb_docs_created": ["doc-1", "doc-2"],
        }
        path = bridge.export_session_log(data)
        assert os.path.isfile(path)
        assert "sessoes" in path

    def test_session_log_content(self, bridge):
        data = {
            "session_id": "xyz-789",
            "prompt": "Test prompt",
            "final_output": "Test output",
        }
        path = bridge.export_session_log(data)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "session_id: xyz-789" in content
        assert "## Prompt Original" in content
        assert "Test prompt" in content
        assert "## FINAL" in content


# ===========================================================================
# 5. Import: Vault → KB
# ===========================================================================


class TestSyncConceitos:

    def test_import_conceito_basic(self, bridge, kb, vault_path):
        conceito_dir = os.path.join(vault_path, "conceitos")
        content = """---
title: "RLM Pattern"
domain: ai
tags: ["rlm", "recursion"]
importance: 0.9
---

## Resumo
RLM usa loops recursivos de self-improvement.

## Contexto Completo
Detalhes do padrão RLM e como implementar...
"""
        with open(os.path.join(conceito_dir, "rlm_pattern.md"), "w", encoding="utf-8") as f:
            f.write(content)

        imported = bridge.sync_conceitos()
        assert len(imported) == 1
        doc = kb.get_document(imported[0])
        assert doc["title"] == "RLM Pattern"
        assert doc["domain"] == "ai"
        assert doc["importance"] == 0.9

    def test_import_no_conceitos_dir(self, bridge, vault_path):
        # Remove dir
        import shutil
        conceito_dir = os.path.join(vault_path, "conceitos")
        if os.path.isdir(conceito_dir):
            shutil.rmtree(conceito_dir)
        result = bridge.sync_conceitos()
        assert result == []

    def test_import_skips_unchanged(self, bridge, kb, vault_path):
        conceito_dir = os.path.join(vault_path, "conceitos")
        content = """---
title: "Test Dedup"
domain: test
---
Summary text.
"""
        filepath = os.path.join(conceito_dir, "test_dedup.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        first = bridge.sync_conceitos()
        assert len(first) == 1

        # Second import should skip (hash match)
        second = bridge.sync_conceitos()
        assert len(second) == 0


# ===========================================================================
# 6. Sync Corrections (human edits)
# ===========================================================================


class TestSyncCorrections:

    def test_metadata_update_detected(self, bridge, kb, sample_doc_id, vault_path):
        # Export first
        bridge.on_doc_created(sample_doc_id)
        # Edit the file — change importance
        filepath = os.path.join(vault_path, "conhecimento", "Partition Key Design.md")
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("importance: 0.8", "importance: 0.95")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        result = bridge.sync_corrections()
        assert sample_doc_id in result["metadata_updated"]
        doc = kb.get_document(sample_doc_id)
        assert abs(doc["importance"] - 0.95) < 0.01

    def test_human_notes_merged(self, bridge, kb, sample_doc_id, vault_path):
        bridge.on_doc_created(sample_doc_id)
        filepath = os.path.join(vault_path, "conhecimento", "Partition Key Design.md")
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        # Add human notes
        content = content.replace(
            "## Notas Humanas\n<!-- Seção editável. Bridge detecta mudanças via hash. -->",
            "## Notas Humanas\nEste conceito precisa de mais exemplos práticos."
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        result = bridge.sync_corrections()
        assert sample_doc_id in result["human_notes_merged"]
        doc = kb.get_document(sample_doc_id)
        assert "exemplos práticos" in doc["full_context"]

    def test_no_changes_skips(self, bridge, kb, sample_doc_id, vault_path):
        bridge.on_doc_created(sample_doc_id)
        result = bridge.sync_corrections()
        assert len(result["metadata_updated"]) == 0
        assert len(result["human_notes_merged"]) == 0


# ===========================================================================
# 7. Conflict Resolution
# ===========================================================================


class TestConflictResolution:

    def test_on_consolidation_conflict_creates_file(self, bridge, kb, vault_path):
        id1 = kb.add_document(title="Version A", summary="Summary A")
        id2 = kb.add_document(title="Version B", summary="Summary B")
        path = bridge.on_consolidation_conflict(id1, id2, 0.82, "Different conclusions")
        assert os.path.isfile(path)
        assert "conflitos" in path
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "merge_score: 0.82" in content

    def test_conflict_resolution_depreciates_loser(self, bridge, kb, vault_path):
        winner = kb.add_document(title="Winner", summary="W")
        loser = kb.add_document(title="Loser", summary="L")
        # Create resolved conflict file
        conflicts_dir = os.path.join(vault_path, "conflitos")
        content = f"""---
doc_a_id: "{winner}"
doc_b_id: "{loser}"
loser_id: "{loser}"
merge_score: 0.8
resolucao: "{winner}"
---
# Resolved
"""
        with open(os.path.join(conflicts_dir, "conflict_test.md"), "w", encoding="utf-8") as f:
            f.write(content)

        resolved = bridge.sync_conflict_resolutions()
        assert len(resolved) == 1
        doc = kb.get_document(loser)
        assert doc["status"] == "superseded"

    def test_conflict_moved_to_resolved(self, bridge, kb, vault_path):
        winner = kb.add_document(title="W2", summary="W")
        loser = kb.add_document(title="L2", summary="L")
        conflicts_dir = os.path.join(vault_path, "conflitos")
        content = f"""---
doc_a_id: "{winner}"
doc_b_id: "{loser}"
loser_id: "{loser}"
resolucao: "{winner}"
---
"""
        filepath = os.path.join(conflicts_dir, "c_move_test.md")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        bridge.sync_conflict_resolutions()
        assert not os.path.exists(filepath)
        assert os.path.exists(os.path.join(conflicts_dir, "resolvidos", "c_move_test.md"))


# ===========================================================================
# 8. Wikilinks → Edges
# ===========================================================================


class TestWikilinksAsEdges:

    def test_wikilink_creates_edge(self, bridge, kb, vault_path):
        id1 = kb.add_document(title="Source Note", summary="S1")
        id2 = kb.add_document(title="Target Note", summary="S2")
        # Write a file with wikilink
        bridge.on_doc_created(id1)
        filepath = os.path.join(vault_path, "conhecimento", "Source Note.md")
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        # Inject wikilink
        content = content.replace("## Resumo", "## Resumo\nSee also [[Target Note]]")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        created = bridge.sync_wikilinks_as_edges()
        assert created >= 1
        related = kb.get_related(id1)
        titles = [r["title"] for r in related]
        assert "Target Note" in titles

    def test_duplicate_wikilinks_no_duplicate_edges(self, bridge, kb, vault_path):
        id1 = kb.add_document(title="A", summary="A")
        id2 = kb.add_document(title="B", summary="B")
        bridge.on_doc_created(id1)
        filepath = os.path.join(vault_path, "conhecimento", "A.md")
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("## Resumo", "## Resumo\n[[B]] and [[B]]")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        first = bridge.sync_wikilinks_as_edges()
        second = bridge.sync_wikilinks_as_edges()
        assert second == 0  # Already exists


# ===========================================================================
# 9. Knowledge Graph Export
# ===========================================================================


class TestKnowledgeGraph:

    def test_export_graph_creates_file(self, bridge, kb, vault_path):
        id1 = kb.add_document(title="Node1", summary="S1")
        id2 = kb.add_document(title="Node2", summary="S2")
        kb.add_edge(id1, id2, "causes")
        path = bridge.export_knowledge_graph()
        assert os.path.isfile(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "```mermaid" in content
        assert "graph TD" in content
        assert "causes" in content

    def test_export_graph_empty_db(self, bridge, vault_path):
        path = bridge.export_knowledge_graph()
        assert os.path.isfile(path)


# ===========================================================================
# 10. MOCs (Maps of Content)
# ===========================================================================


class TestMOCs:

    def test_regenerate_mocs_creates_per_domain(self, bridge, kb, vault_path):
        kb.add_document(title="D1", summary="S1", domain="database")
        kb.add_document(title="D2", summary="S2", domain="database")
        kb.add_document(title="D3", summary="S3", domain="ai")
        paths = bridge.regenerate_mocs()
        assert len(paths) >= 2
        db_moc = os.path.join(vault_path, "moc", "database.md")
        ai_moc = os.path.join(vault_path, "moc", "ai.md")
        assert os.path.isfile(db_moc)
        assert os.path.isfile(ai_moc)

    def test_moc_content(self, bridge, kb, vault_path):
        kb.add_document(title="Important Doc", summary="S", domain="test", importance=0.9)
        bridge.regenerate_mocs()
        moc_path = os.path.join(vault_path, "moc", "test.md")
        with open(moc_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "[[Important Doc]]" in content
        assert "type: moc" in content


# ===========================================================================
# 11. sync_all
# ===========================================================================


class TestSyncAll:

    def test_sync_all_returns_stats(self, bridge, kb, vault_path):
        stats = bridge.sync_all()
        assert "conceitos_imported" in stats
        assert "metadata_updated" in stats
        assert "conflicts_resolved" in stats
        assert "edges_from_wikilinks" in stats


# ===========================================================================
# 12. Helpers
# ===========================================================================


class TestHelpers:

    def test_safe_filename(self):
        from rlm.core.obsidian_bridge import _safe_filename
        assert _safe_filename('Hello "World"') == "Hello _World_"
        assert _safe_filename("a" * 200) == "a" * 80

    def test_content_hash_deterministic(self):
        from rlm.core.obsidian_bridge import _content_hash
        h1 = _content_hash("test")
        h2 = _content_hash("test")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256

    def test_parse_frontmatter(self):
        from rlm.core.obsidian_bridge import _parse_frontmatter
        content = '---\ntitle: "Test"\nimportance: 0.8\ntags: ["a", "b"]\n---\nBody text'
        fm, body = _parse_frontmatter(content)
        assert fm["title"] == '"Test"'
        assert fm["importance"] == 0.8
        assert fm["tags"] == ["a", "b"]
        assert body == "Body text"

    def test_parse_frontmatter_no_frontmatter(self):
        from rlm.core.obsidian_bridge import _parse_frontmatter
        fm, body = _parse_frontmatter("Just text")
        assert fm == {}
        assert body == "Just text"

    def test_extract_human_notes(self):
        from rlm.core.obsidian_bridge import _extract_human_notes
        body = "## Resumo\nR\n\n## Notas Humanas\nThis is my note.\n\n## Other"
        notes = _extract_human_notes(body)
        assert notes == "This is my note."

    def test_extract_human_notes_empty(self):
        from rlm.core.obsidian_bridge import _extract_human_notes
        body = "## Notas Humanas\n<!-- Seção editável. Bridge detecta mudanças via hash. -->"
        notes = _extract_human_notes(body)
        assert notes is None

    def test_extract_wikilinks(self):
        from rlm.core.obsidian_bridge import _extract_wikilinks
        body = "See [[Doc A]] and [[Doc B|alias]] but not [[Doc A]] again"
        links = _extract_wikilinks(body)
        assert "Doc A" in links
        assert "Doc B" in links

    def test_split_body_with_sections(self):
        from rlm.core.obsidian_bridge import _split_body
        body = "## Resumo\nMy summary\n\n## Contexto Completo\nFull details"
        summary, ctx = _split_body(body)
        assert summary == "My summary"
        assert ctx == "Full details"

    def test_split_body_fallback(self):
        from rlm.core.obsidian_bridge import _split_body
        body = "First paragraph\n\nSecond paragraph"
        summary, ctx = _split_body(body)
        assert summary == "First paragraph"
        assert ctx == "Second paragraph"


# ===========================================================================
# 13. Backward Compat
# ===========================================================================


class TestBackwardCompat:

    def test_obsidian_mirror_still_importable(self):
        from rlm.core.obsidian_mirror import export_document_to_vault
        assert callable(export_document_to_vault)

    def test_obsidian_mirror_re_exports_bridge(self):
        from rlm.core.obsidian_mirror import ObsidianBridge
        assert ObsidianBridge is not None
