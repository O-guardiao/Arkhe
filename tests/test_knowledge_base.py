"""
test_knowledge_base.py — Testes do Global Knowledge Base

Valida: schema, CRUD, search_hybrid, score tripartito, edges,
consolidação de sessão, retrieval progressivo.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kb_db_path(tmp_path):
    return str(tmp_path / "global" / "knowledge_base.db")


@pytest.fixture
def kb(kb_db_path):
    from rlm.core.memory.knowledge_base import GlobalKnowledgeBase
    return GlobalKnowledgeBase(db_path=kb_db_path)


@pytest.fixture
def memory_db_path(tmp_path):
    """Cria um memory.db de sessão com nuggets de teste."""
    db_path = str(tmp_path / "memory.db")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE memory_chunks (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                content TEXT,
                metadata TEXT DEFAULT '{}',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                embedding TEXT DEFAULT '[]',
                importance_score REAL DEFAULT 0.5,
                is_deprecated INTEGER DEFAULT 0
            )
        """)
        # Insere nuggets de teste
        nuggets = [
            ("n1", "sess-test", "O workflow de deploy usa GitHub Actions com parallelism 4", 0.8),
            ("n2", "sess-test", "Race condition detectada no job de lock quando parallelism > 2", 0.9),
            ("n3", "sess-test", "Correção aplicada: serialização dos jobs de lock em deploy.yaml L42", 0.85),
            ("n4", "sess-test", "O projeto usa Python 3.11 com Poetry para gerenciamento de dependências", 0.6),
            ("n5", "sess-test", "Testes rodam com pytest e xdist para paralelização", 0.5),
        ]
        for nid, sid, content, imp in nuggets:
            conn.execute(
                "INSERT INTO memory_chunks (id, session_id, content, importance_score) VALUES (?, ?, ?, ?)",
                (nid, sid, content, imp),
            )
        conn.commit()
    return db_path


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestKBSchema:
    def test_creates_tables(self, kb_db_path, kb):
        with closing(sqlite3.connect(kb_db_path)) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            assert "kb_documents" in tables
            assert "kb_edges" in tables

    def test_creates_fts_table(self, kb_db_path, kb):
        with closing(sqlite3.connect(kb_db_path)) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            assert "kb_fts" in tables

    def test_creates_indexes(self, kb_db_path, kb):
        with closing(sqlite3.connect(kb_db_path)) as conn:
            indexes = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()]
            assert "idx_kb_domain" in indexes
            assert "idx_kb_status" in indexes
            assert "idx_kb_importance" in indexes


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestKBCrud:
    def test_add_document(self, kb):
        doc_id = kb.add_document(
            title="Workflow Deploy Fix",
            summary="Race condition corrigida no deploy.",
            full_context="Detalhes completos...",
            tags=["deploy", "fix"],
            domain="devops",
            importance=0.85,
            source_sessions=["sess-001"],
        )
        assert doc_id
        doc = kb.get_document(doc_id)
        assert doc is not None
        assert doc["title"] == "Workflow Deploy Fix"
        assert doc["summary"] == "Race condition corrigida no deploy."
        assert doc["full_context"] == "Detalhes completos..."
        assert doc["tags"] == ["deploy", "fix"]
        assert doc["domain"] == "devops"
        assert abs(doc["importance"] - 0.85) < 0.01
        assert doc["source_sessions"] == ["sess-001"]

    def test_add_document_defaults(self, kb):
        doc_id = kb.add_document(title="Test", summary="Test summary")
        doc = kb.get_document(doc_id)
        assert doc["domain"] == "general"
        assert doc["status"] == "active"
        assert doc["tags"] == []
        assert doc["source_sessions"] == []
        assert abs(doc["importance"] - 0.5) < 0.01

    def test_get_nonexistent_document(self, kb):
        assert kb.get_document("nonexistent") is None

    def test_update_document(self, kb):
        doc_id = kb.add_document(
            title="Original",
            summary="Original summary",
            importance=0.5,
        )
        result = kb.update_document(
            doc_id,
            title="Updated",
            summary="Updated summary",
            importance=0.9,
            tags=["new-tag"],
        )
        assert result is True
        doc = kb.get_document(doc_id)
        assert doc["title"] == "Updated"
        assert doc["summary"] == "Updated summary"
        assert abs(doc["importance"] - 0.9) < 0.01
        assert doc["tags"] == ["new-tag"]

    def test_update_nonexistent_returns_false(self, kb):
        assert kb.update_document("nonexistent", title="X") is False

    def test_update_partial_fields(self, kb):
        doc_id = kb.add_document(
            title="Title", summary="Summary", domain="python"
        )
        kb.update_document(doc_id, importance=0.99)
        doc = kb.get_document(doc_id)
        assert doc["title"] == "Title"  # unchanged
        assert doc["domain"] == "python"  # unchanged
        assert abs(doc["importance"] - 0.99) < 0.01

    def test_deprecate_document(self, kb):
        doc_id = kb.add_document(title="Old", summary="Old stuff")
        result = kb.deprecate_document(doc_id)
        doc = kb.get_document(doc_id)
        assert doc["status"] == "deprecated"

    def test_deprecate_with_superseded_by(self, kb):
        old_id = kb.add_document(title="Old", summary="Old")
        new_id = kb.add_document(title="New", summary="New")
        kb.deprecate_document(old_id, superseded_by=new_id)
        doc = kb.get_document(old_id)
        assert doc["status"] == "superseded"
        assert doc["superseded_by"] == new_id

    def test_importance_clamped(self, kb):
        doc_id = kb.add_document(title="T", summary="S", importance=1.5)
        doc = kb.get_document(doc_id)
        assert doc["importance"] <= 1.0

        doc_id2 = kb.add_document(title="T2", summary="S2", importance=-0.5)
        doc2 = kb.get_document(doc_id2)
        assert doc2["importance"] >= 0.0

    def test_list_documents(self, kb):
        kb.add_document(title="A", summary="A summary", domain="python")
        kb.add_document(title="B", summary="B summary", domain="devops")
        kb.add_document(title="C", summary="C summary", domain="python")

        all_docs = kb.list_documents()
        assert len(all_docs) == 3

        python_docs = kb.list_documents(domain="python")
        assert len(python_docs) == 2

    def test_list_excludes_deprecated(self, kb):
        doc_id = kb.add_document(title="Dep", summary="Dep")
        kb.deprecate_document(doc_id)
        active = kb.list_documents(status="active")
        assert not any(d["id"] == doc_id for d in active)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestKBSearch:
    def test_search_fts_by_title(self, kb):
        kb.add_document(title="Python asyncio tutorial", summary="Uso de async/await.")
        kb.add_document(title="Docker compose setup", summary="Multi-container.")
        results = kb.search_hybrid("asyncio")
        assert len(results) >= 1
        assert "asyncio" in results[0]["title"].lower()

    def test_search_fts_by_tags(self, kb):
        kb.add_document(
            title="Deploy fix",
            summary="Race condition fix",
            tags=["workflow", "github-actions"],
        )
        results = kb.search_hybrid("workflow")
        assert len(results) >= 1

    def test_search_excludes_deprecated(self, kb):
        doc_id = kb.add_document(title="Deprecated thing", summary="Old")
        kb.deprecate_document(doc_id)
        results = kb.search_hybrid("deprecated")
        assert not any(r["id"] == doc_id for r in results)

    def test_search_returns_no_full_context(self, kb):
        kb.add_document(
            title="Test doc",
            summary="Summary",
            full_context="Very long context...",
        )
        results = kb.search_hybrid("test")
        assert "full_context" not in results[0]

    def test_search_respects_domain_filter(self, kb):
        kb.add_document(title="Python thing", summary="X", domain="python")
        kb.add_document(title="Python other", summary="Y", domain="devops")
        results = kb.search_hybrid("python", domain="python")
        assert all(r["domain"] == "python" for r in results)


# ---------------------------------------------------------------------------
# Score Tripartite
# ---------------------------------------------------------------------------

class TestKBScoreTripartite:
    def test_score_within_range(self, kb):
        doc = {"hybrid_score": 0.03, "importance": 0.8, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        score = kb.score_tripartite(doc)
        assert 0.0 <= score <= 1.5  # weighted sum can exceed 1.0

    def test_higher_relevance_higher_score(self, kb):
        base = {"importance": 0.5, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        low = kb.score_tripartite({**base, "hybrid_score": 0.001})
        high = kb.score_tripartite({**base, "hybrid_score": 0.04})
        assert high > low

    def test_recency_decays_with_age(self, kb):
        import datetime
        now = time.time()
        recent = datetime.datetime.fromtimestamp(now).isoformat()
        old = datetime.datetime.fromtimestamp(now - 60 * 86400).isoformat()  # 60 days ago

        base = {"hybrid_score": 0.03, "importance": 0.5}
        score_recent = kb.score_tripartite({**base, "updated_at": recent}, now_ts=now)
        score_old = kb.score_tripartite({**base, "updated_at": old}, now_ts=now)
        assert score_recent > score_old


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------

class TestKBEdges:
    def test_add_and_get_related(self, kb):
        id1 = kb.add_document(title="Doc 1", summary="Summary 1")
        id2 = kb.add_document(title="Doc 2", summary="Summary 2")
        kb.add_edge(id1, id2, "extends")
        related = kb.get_related(id1)
        assert len(related) == 1
        assert related[0]["edge_type"] == "extends"

    def test_bidirectional_related(self, kb):
        id1 = kb.add_document(title="A", summary="A")
        id2 = kb.add_document(title="B", summary="B")
        kb.add_edge(id1, id2, "relates")
        assert len(kb.get_related(id1)) == 1
        assert len(kb.get_related(id2)) == 1


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestKBStats:
    def test_stats_basic(self, kb):
        kb.add_document(title="A", summary="X", domain="python")
        kb.add_document(title="B", summary="Y", domain="python")
        kb.add_document(title="C", summary="Z", domain="devops")
        stats = kb.stats()
        assert stats["total_documents"] == 3
        assert stats["active_documents"] == 3
        assert stats["domains"]["python"] == 2


# ---------------------------------------------------------------------------
# Consolidator
# ---------------------------------------------------------------------------

class TestConsolidator:
    def test_collect_session_nuggets(self, memory_db_path):
        from rlm.core.memory.knowledge_consolidator import collect_session_nuggets
        nuggets = collect_session_nuggets("sess-test", memory_db_path)
        assert len(nuggets) == 5
        assert "workflow" in nuggets[0]["content"].lower()

    def test_collect_excludes_deprecated(self, memory_db_path):
        from rlm.core.memory.knowledge_consolidator import collect_session_nuggets
        # Depreca um nugget
        with closing(sqlite3.connect(memory_db_path)) as conn:
            conn.execute("UPDATE memory_chunks SET is_deprecated = 1 WHERE id = 'n5'")
            conn.commit()
        nuggets = collect_session_nuggets("sess-test", memory_db_path)
        assert len(nuggets) == 4

    def test_collect_wrong_session_returns_empty(self, memory_db_path):
        from rlm.core.memory.knowledge_consolidator import collect_session_nuggets
        nuggets = collect_session_nuggets("nonexistent", memory_db_path)
        assert nuggets == []

    @patch("rlm.core.memory.knowledge_consolidator._call_nano")
    def test_cluster_nuggets_fallback(self, mock_nano):
        """Se nano falha, retorna um único cluster com todos os nuggets."""
        from rlm.core.memory.knowledge_consolidator import cluster_nuggets
        mock_nano.return_value = None
        nuggets = [{"content": f"nugget {i}"} for i in range(3)]
        clusters = cluster_nuggets(nuggets)
        assert len(clusters) == 1
        assert len(clusters[0]["nuggets"]) == 3

    @patch("rlm.core.memory.knowledge_consolidator._call_nano")
    def test_cluster_nuggets_with_nano(self, mock_nano):
        from rlm.core.memory.knowledge_consolidator import cluster_nuggets
        mock_nano.return_value = json.dumps([
            {"topic": "deploy", "nugget_indices": [0, 1, 2]},
            {"topic": "python", "nugget_indices": [3, 4]},
        ])
        nuggets = [{"content": f"nugget {i}"} for i in range(5)]
        clusters = cluster_nuggets(nuggets)
        assert len(clusters) == 2
        assert clusters[0]["topic"] == "deploy"
        assert len(clusters[0]["nuggets"]) == 3

    @patch("rlm.core.memory.knowledge_consolidator._call_nano")
    def test_generate_document_fields_fallback(self, mock_nano):
        from rlm.core.memory.knowledge_consolidator import generate_document_fields
        mock_nano.return_value = None
        nuggets = [{"content": "Race condition no deploy"}, {"content": "Fix com mutex"}]
        fields = generate_document_fields("deploy fix", nuggets)
        assert fields is not None
        assert fields["title"] == "deploy fix"
        assert "Race condition" in fields["summary"]

    @patch("rlm.core.memory.knowledge_consolidator._call_nano")
    def test_generate_document_fields_with_nano(self, mock_nano):
        from rlm.core.memory.knowledge_consolidator import generate_document_fields
        mock_nano.return_value = json.dumps({
            "title": "Deploy Race Condition Fix",
            "summary": "Race condition corrigida com mutex",
            "tags": ["deploy", "race-condition"],
            "domain": "devops",
        })
        nuggets = [{"content": "Race condition"}, {"content": "Fix mutex"}]
        fields = generate_document_fields("deploy", nuggets)
        assert fields["title"] == "Deploy Race Condition Fix"
        assert fields["domain"] == "devops"
        assert "deploy" in fields["tags"]

    def test_build_full_context(self):
        from rlm.core.memory.knowledge_consolidator import build_full_context
        nuggets = [
            {"content": "Nugget 1", "importance_score": 0.8, "timestamp": "2026-03-27 14:00:00"},
            {"content": "Nugget 2", "importance_score": 0.6, "timestamp": "2026-03-27 14:05:00"},
        ]
        ctx = build_full_context("test topic", nuggets, "sess-001")
        assert "sess-001" in ctx
        assert "test topic" in ctx
        assert "Nugget 1" in ctx
        assert "Nugget 2" in ctx
        assert "[0.8]" in ctx

    @patch("rlm.core.memory.knowledge_consolidator._call_nano")
    def test_consolidate_session_end_to_end(self, mock_nano, kb, memory_db_path):
        """Integração: consolida sessão e verifica documento no KB."""
        from rlm.core.memory.knowledge_consolidator import consolidate_session

        # Mock nano: cluster e document generation
        call_count = [0]
        def mock_responses(system, user):
            call_count[0] += 1
            if call_count[0] == 1:
                # Cluster call
                return json.dumps([
                    {"topic": "deploy workflow", "nugget_indices": [0, 1, 2]},
                    {"topic": "project setup", "nugget_indices": [3, 4]},
                ])
            # Document generation calls
            return json.dumps({
                "title": f"Test Document {call_count[0]}",
                "summary": "Test summary for consolidation",
                "tags": ["test", "consolidation"],
                "domain": "devops",
            })

        mock_nano.side_effect = mock_responses

        doc_ids = consolidate_session("sess-test", memory_db_path, kb)
        assert len(doc_ids) >= 1

        # Verifica que documentos foram criados no KB
        for doc_id in doc_ids:
            doc = kb.get_document(doc_id)
            assert doc is not None
            assert doc["status"] == "active"
            assert "sess-test" in doc["source_sessions"]

    @patch("rlm.core.memory.knowledge_consolidator._call_nano")
    def test_consolidate_few_nuggets_skips(self, mock_nano, kb, tmp_path):
        """Sessão com < 2 nuggets não consolida."""
        from rlm.core.memory.knowledge_consolidator import consolidate_session

        db_path = str(tmp_path / "few.db")
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("""
                CREATE TABLE memory_chunks (
                    id TEXT PRIMARY KEY, session_id TEXT, content TEXT,
                    metadata TEXT DEFAULT '{}', timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    embedding TEXT DEFAULT '[]', importance_score REAL DEFAULT 0.5,
                    is_deprecated INTEGER DEFAULT 0
                )
            """)
            conn.execute(
                "INSERT INTO memory_chunks (id, session_id, content) VALUES ('n1', 'sess-few', 'only one')"
            )
            conn.commit()

        doc_ids = consolidate_session("sess-few", db_path, kb)
        assert doc_ids == []
        mock_nano.assert_not_called()


# ---------------------------------------------------------------------------
# Retrieval progressivo (integração KB → prompt)
# ---------------------------------------------------------------------------

class TestRetrievalProgressivo:
    def test_kb_summary_in_results(self, kb):
        """Verifica que search retorna summary mas não full_context."""
        kb.add_document(
            title="Workflow Deploy Fix",
            summary="Race condition corrigida",
            full_context="Contexto muito longo que não deveria aparecer na busca",
            tags=["deploy"],
        )
        results = kb.search_hybrid("deploy workflow")
        assert len(results) >= 1
        assert results[0]["summary"] == "Race condition corrigida"
        assert "full_context" not in results[0]

    def test_full_context_available_via_get(self, kb):
        """Full context disponível sob demanda via get_document."""
        doc_id = kb.add_document(
            title="Test",
            summary="Short",
            full_context="Very detailed context with code and logs",
        )
        doc = kb.get_document(doc_id)
        assert doc["full_context"] == "Very detailed context with code and logs"

    def test_multiple_documents_searchable(self, kb):
        """Cenário: múltiplos documentos de múltiplas sessões."""
        kb.add_document(
            title="Deploy workflow race condition",
            summary="Race condition em parallelism > 2",
            tags=["deploy", "race-condition"],
            source_sessions=["sess-001"],
        )
        kb.add_document(
            title="Database migration PostgreSQL 15",
            summary="Migração de schema com zero downtime",
            tags=["database", "postgresql", "migration"],
            source_sessions=["sess-002"],
        )
        kb.add_document(
            title="API rate limiting implementation",
            summary="Token bucket com Redis",
            tags=["api", "rate-limiting"],
            source_sessions=["sess-003"],
        )

        deploy_results = kb.search_hybrid("deploy workflow quebrou")
        assert len(deploy_results) >= 1
        assert "deploy" in deploy_results[0]["title"].lower()

        db_results = kb.search_hybrid("migração de banco de dados")
        assert len(db_results) >= 1


# ===========================================================================
# INTEGRATION — _retrieve_from_kb, _consolidate_to_kb, KB tools
# ===========================================================================


class TestKBIntegrationRetrieve:
    """Testa _retrieve_from_kb direto no RLMSession."""

    def test_retrieve_from_kb_returns_formatted_block(self, kb):
        """KB com docs relevantes gera bloco CONHECIMENTO PERSISTENTE."""
        kb.add_document(
            title="Deploy Docker",
            summary="Pipeline CI/CD com Docker compose e secrets management.",
            full_context="Detalhes completos do deploy Docker...",
            tags=["deploy", "docker"],
            importance=0.85,
            domain="devops",
            source_sessions=["old-session"],
        )

        # Cria um fake RLMSession com _kb
        class FakeSession:
            _kb = kb
        session = FakeSession()

        # Importa e testa o método diretamente
        from rlm.session import RLMSession
        block = RLMSession._retrieve_from_kb(session, "como fazer deploy docker")
        assert "[CONHECIMENTO PERSISTENTE" in block
        assert "Deploy Docker" in block
        assert "kb_get_full_context" in block

    def test_retrieve_from_kb_empty_when_no_kb(self):
        class FakeSession:
            _kb = None
        session = FakeSession()
        from rlm.session import RLMSession
        block = RLMSession._retrieve_from_kb(session, "anything")
        assert block == ""

    def test_retrieve_from_kb_empty_when_no_matches(self, kb):
        class FakeSession:
            _kb = kb
        session = FakeSession()
        from rlm.session import RLMSession
        block = RLMSession._retrieve_from_kb(session, "xyzzy nonsense foobarbaz")
        assert block == ""


class TestKBTools:
    """Testa as KB tools expostas ao REPL."""

    def test_kb_search_returns_results(self, kb):
        kb.add_document(
            title="Nginx Config",
            summary="Reverse proxy com proxy_pass.",
            full_context="Full config details...",
            tags=["nginx", "proxy"],
            importance=0.72,
            domain="infra",
            source_sessions=["s1"],
        )

        class FakeSession:
            pass
        fake = FakeSession()
        fake.kb = kb

        from rlm.tools.kb_tools import get_kb_tools
        tools = get_kb_tools(fake)
        results = tools["kb_search"]("nginx reverse proxy")
        assert len(results) >= 1
        assert "full_context" not in results[0]  # Progressive: no full context
        assert results[0]["title"] == "Nginx Config"

    def test_kb_get_full_context_returns_full(self, kb):
        doc_id = kb.add_document(
            title="Nginx Config",
            summary="Short summary.",
            full_context="This is the full detailed config...",
            tags=["nginx"],
            importance=0.72,
            domain="infra",
            source_sessions=["s1"],
        )

        class FakeSession:
            pass
        fake = FakeSession()
        fake.kb = kb

        from rlm.tools.kb_tools import get_kb_tools
        tools = get_kb_tools(fake)
        result = tools["kb_get_full_context"](doc_id)
        assert result["full_context"] == "This is the full detailed config..."

    def test_kb_get_full_context_not_found(self, kb):
        class FakeSession:
            pass
        fake = FakeSession()
        fake.kb = kb

        from rlm.tools.kb_tools import get_kb_tools
        tools = get_kb_tools(fake)
        result = tools["kb_get_full_context"]("nonexistent-id")
        assert "error" in result

    def test_kb_status(self, kb):
        kb.add_document(
            title="Test Doc", summary="s", full_context="f",
            tags=["t"], importance=0.5, domain="d", source_sessions=["s1"],
        )

        class FakeSession:
            pass
        fake = FakeSession()
        fake.kb = kb

        from rlm.tools.kb_tools import get_kb_tools
        tools = get_kb_tools(fake)
        status = tools["kb_status"]()
        assert status["available"] is True
        assert status["total_documents"] >= 1

    def test_kb_tools_graceful_when_no_kb(self):
        class FakeSession:
            kb = None

        from rlm.tools.kb_tools import get_kb_tools
        tools = get_kb_tools(FakeSession())
        assert "error" in tools["kb_search"]("test")[0]
        assert "error" in tools["kb_get_full_context"]("id")
        assert tools["kb_status"]()["available"] is False
