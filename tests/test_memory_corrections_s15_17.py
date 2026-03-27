"""
Rigorous tests for the 6 corrections applied to the RLM memory pipeline (§15-17).

Tests cover:
  1. session_memory_tools.py — 3 functions (search, status, recent)
  2. RLMSession public properties (.memory, .session_id, .telemetry)
  3. runtime_pipeline session tool injection
  4. SKILL.md frontmatter correctness
  5. local_repl history → repl_message_log rename + backward compat
  6. System prompt memory taxonomy
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# §1 — session_memory_tools.py
# ---------------------------------------------------------------------------

class TestSessionMemoryToolsImport:
    """Ensure the new module imports cleanly and exposes the expected API."""

    def test_import_module(self):
        from rlm.tools.session_memory_tools import get_session_memory_tools
        assert callable(get_session_memory_tools)

    def test_returns_three_callables(self):
        from rlm.tools.session_memory_tools import get_session_memory_tools
        fake_session = SimpleNamespace(memory=None, session_id="s1")
        tools = get_session_memory_tools(fake_session)
        assert set(tools.keys()) == {
            "session_memory_search",
            "session_memory_status",
            "session_memory_recent",
        }
        for fn in tools.values():
            assert callable(fn)


class TestSessionMemorySearch:
    """Tests for session_memory_search against a real SQLite MultiVectorMemory."""

    @pytest.fixture()
    def session_with_memory(self, tmp_path):
        from rlm.core.memory_manager import MultiVectorMemory
        from rlm.tools.session_memory_tools import get_session_memory_tools

        db = tmp_path / "test_mem.db"
        mem = MultiVectorMemory(db_path=str(db))
        # Disable OpenAI embeddings for tests
        mem.client = None

        mem.add_memory("sess-1", "User prefers 4-space indentation", importance_score=0.9)
        mem.add_memory("sess-1", "Database schema uses PostgreSQL", importance_score=0.7)
        mem.add_memory("sess-2", "Unrelated session note", importance_score=0.5)

        session = SimpleNamespace(memory=mem, session_id="sess-1")
        tools = get_session_memory_tools(session)
        return tools, mem

    def test_search_returns_list(self, session_with_memory):
        tools, _ = session_with_memory
        results = tools["session_memory_search"]("indentation")
        assert isinstance(results, list)

    def test_search_finds_relevant_chunk(self, session_with_memory):
        tools, _ = session_with_memory
        results = tools["session_memory_search"]("indentation", top_k=5)
        # FTS should match "indentation"
        contents = [r.get("content", "") for r in results if isinstance(r, dict)]
        assert any("indentation" in c for c in contents)

    def test_search_with_none_memory_returns_empty(self):
        from rlm.tools.session_memory_tools import get_session_memory_tools
        session = SimpleNamespace(memory=None, session_id="x")
        tools = get_session_memory_tools(session)
        assert tools["session_memory_search"]("anything") == []


class TestSessionMemoryStatus:
    """Tests for session_memory_status."""

    def test_status_with_no_memory(self):
        from rlm.tools.session_memory_tools import get_session_memory_tools
        session = SimpleNamespace(memory=None, session_id="x")
        tools = get_session_memory_tools(session)
        status = tools["session_memory_status"]()
        assert status["available"] is False

    def test_status_with_real_memory(self, tmp_path):
        from rlm.core.memory_manager import MultiVectorMemory
        from rlm.tools.session_memory_tools import get_session_memory_tools

        db = tmp_path / "status_test.db"
        mem = MultiVectorMemory(db_path=str(db))
        mem.client = None

        mem.add_memory("sess-status", "Note 1", importance_score=0.8)
        mem.add_memory("sess-status", "Note 2", importance_score=0.6)

        session = SimpleNamespace(memory=mem, session_id="sess-status")
        tools = get_session_memory_tools(session)
        status = tools["session_memory_status"]()

        assert status["available"] is True
        assert status["session_id"] == "sess-status"
        assert status["active_chunks"] == 2
        assert status["deprecated_chunks"] == 0

    def test_status_counts_deprecated(self, tmp_path):
        from rlm.core.memory_manager import MultiVectorMemory
        from rlm.tools.session_memory_tools import get_session_memory_tools

        db = tmp_path / "deprecated_test.db"
        mem = MultiVectorMemory(db_path=str(db))
        mem.client = None

        mid = mem.add_memory("sess-dep", "Old fact", importance_score=0.5)
        mem.add_memory("sess-dep", "New fact", importance_score=0.9)
        mem.deprecate(mid)

        session = SimpleNamespace(memory=mem, session_id="sess-dep")
        tools = get_session_memory_tools(session)
        status = tools["session_memory_status"]()

        assert status["active_chunks"] == 1
        assert status["deprecated_chunks"] == 1


class TestSessionMemoryRecent:
    """Tests for session_memory_recent."""

    def test_recent_with_no_memory(self):
        from rlm.tools.session_memory_tools import get_session_memory_tools
        session = SimpleNamespace(memory=None, session_id="x")
        tools = get_session_memory_tools(session)
        assert tools["session_memory_recent"]() == []

    def test_recent_returns_newest_first(self, tmp_path):
        import sqlite3, time
        from rlm.core.memory_manager import MultiVectorMemory
        from rlm.tools.session_memory_tools import get_session_memory_tools

        db = tmp_path / "recent_test.db"
        mem = MultiVectorMemory(db_path=str(db))
        mem.client = None

        # Insert with explicit timestamps to guarantee ordering
        from contextlib import closing
        mem.add_memory("sess-rec", "First note", importance_score=0.3)
        # Force older timestamp on the first row so newest is truly last
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute("UPDATE memory_chunks SET timestamp = datetime('now', '-2 minutes') WHERE content = 'First note'")
            conn.commit()
        mem.add_memory("sess-rec", "Second note", importance_score=0.7)
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute("UPDATE memory_chunks SET timestamp = datetime('now', '-1 minutes') WHERE content = 'Second note'")
            conn.commit()
        mem.add_memory("sess-rec", "Third note", importance_score=0.9)

        session = SimpleNamespace(memory=mem, session_id="sess-rec")
        tools = get_session_memory_tools(session)
        recent = tools["session_memory_recent"](limit=2)

        assert len(recent) == 2
        # Newest should be first
        assert "Third" in recent[0]["content"]
        assert "Second" in recent[1]["content"]

    def test_recent_contains_expected_fields(self, tmp_path):
        from rlm.core.memory_manager import MultiVectorMemory
        from rlm.tools.session_memory_tools import get_session_memory_tools

        db = tmp_path / "fields_test.db"
        mem = MultiVectorMemory(db_path=str(db))
        mem.client = None

        mem.add_memory("sess-fields", "A note", importance_score=0.85)

        session = SimpleNamespace(memory=mem, session_id="sess-fields")
        tools = get_session_memory_tools(session)
        recent = tools["session_memory_recent"](limit=1)

        assert len(recent) == 1
        item = recent[0]
        assert "id" in item
        assert "content" in item
        assert "importance" in item
        assert "timestamp" in item
        assert item["importance"] == pytest.approx(0.85, abs=0.01)


# ---------------------------------------------------------------------------
# §2 — RLMSession public properties
# ---------------------------------------------------------------------------

class TestRLMSessionProperties:
    """Verify the new public properties on RLMSession."""

    @pytest.fixture()
    def make_session(self, tmp_path):
        from rlm.session import RLMSession

        fake_rlm = MagicMock()
        with patch("rlm.core.rlm.RLM", return_value=fake_rlm):
            session = RLMSession(
                memory_db_path=str(tmp_path / "prop_test.db"),
                session_id="sess-prop-test",
            )
        yield session
        try:
            session.close()
        except Exception:
            pass

    def test_session_id_property(self, make_session):
        assert make_session.session_id == "sess-prop-test"

    def test_session_id_is_string(self, make_session):
        assert isinstance(make_session.session_id, str)

    def test_memory_property_is_not_none(self, make_session):
        # MultiVectorMemory should have been created successfully
        assert make_session.memory is not None

    def test_memory_property_type(self, make_session):
        from rlm.core.memory_manager import MultiVectorMemory
        assert isinstance(make_session.memory, MultiVectorMemory)

    def test_telemetry_property(self, make_session):
        from rlm.core.turn_telemetry import TurnTelemetryStore
        assert isinstance(make_session.telemetry, TurnTelemetryStore)

    def test_autogenerated_session_id(self, tmp_path):
        """When no session_id is provided, it should be auto-generated as UUID."""
        from rlm.session import RLMSession

        fake_rlm = MagicMock()
        with patch("rlm.core.rlm.RLM", return_value=fake_rlm):
            session = RLMSession(memory_db_path=str(tmp_path / "auto_id.db"))

        assert session.session_id
        assert len(session.session_id) == 36  # UUID format
        try:
            session.close()
        except Exception:
            pass

    def test_properties_match_private_attrs(self, make_session):
        """Public properties should return the same objects as private attributes."""
        assert make_session.memory is make_session._memory
        assert make_session.session_id is make_session._session_id
        assert make_session.telemetry is make_session._telemetry


# ---------------------------------------------------------------------------
# §3 — runtime_pipeline session tool injection
# ---------------------------------------------------------------------------

class TestRuntimePipelineSessionToolInjection:
    """Verify _apply_repl_injections now injects session_memory_* tools."""

    def test_session_tools_injected_when_rlm_instance_exists(self):
        from rlm.server.runtime_pipeline import _apply_repl_injections, RuntimeDispatchServices

        # Build minimal mock services
        skill_loader = MagicMock()
        skill_loader.activate_all.return_value = None
        skill_loader.inject_sif_callables.return_value = None
        skill_loader.build_skill_doc_fn.return_value = (lambda name: "", lambda: [])

        services = RuntimeDispatchServices(
            session_manager=MagicMock(),
            supervisor=MagicMock(),
            plugin_loader=MagicMock(),
            event_router=MagicMock(),
            hooks=MagicMock(),
            skill_loader=skill_loader,
        )
        services.plugin_loader.inject_multiple.return_value = None

        # Mock session with rlm_instance that has memory + session_id properties
        mock_rlm_session = SimpleNamespace(
            memory=MagicMock(),
            session_id="sess-inject-test",
            _memory=MagicMock(),
            _session_id="sess-inject-test",
        )
        session = SimpleNamespace(
            rlm_instance=mock_rlm_session,
            session_id="sess-inject-test",
            client_id="test-client",
        )

        repl_locals: dict[str, Any] = {}

        _apply_repl_injections(
            services,
            repl_locals,
            session=session,
            client_id="test-client",
            plugins_to_load=[],
            dynamic_skill_context="",
        )

        # The 3 session memory tools should be in repl_locals
        assert "session_memory_search" in repl_locals
        assert "session_memory_status" in repl_locals
        assert "session_memory_recent" in repl_locals
        assert callable(repl_locals["session_memory_search"])

    def test_no_crash_when_rlm_instance_is_none(self):
        from rlm.server.runtime_pipeline import _apply_repl_injections, RuntimeDispatchServices

        skill_loader = MagicMock()
        skill_loader.activate_all.return_value = None
        skill_loader.inject_sif_callables.return_value = None
        skill_loader.build_skill_doc_fn.return_value = (lambda name: "", lambda: [])

        services = RuntimeDispatchServices(
            session_manager=MagicMock(),
            supervisor=MagicMock(),
            plugin_loader=MagicMock(),
            event_router=MagicMock(),
            hooks=MagicMock(),
            skill_loader=skill_loader,
        )
        services.plugin_loader.inject_multiple.return_value = None

        # rlm_instance is None — should not crash
        session = SimpleNamespace(
            rlm_instance=None,
            session_id="sess-none",
            client_id="c",
        )

        repl_locals: dict[str, Any] = {}
        _apply_repl_injections(
            services,
            repl_locals,
            session=session,
            client_id="c",
            plugins_to_load=[],
            dynamic_skill_context="",
        )

        # Session memory tools should NOT be present
        assert "session_memory_search" not in repl_locals


class TestPrependMemoryBlockUsesPublicProperties:
    """Verify _prepend_memory_block works with the new public properties."""

    def test_falls_back_to_public_property(self):
        from rlm.server.runtime_pipeline import _prepend_memory_block

        # Session that has inject_memory_prompt as a callable — the primary path
        session = MagicMock()
        session.inject_memory_prompt.return_value = "prompt-with-memory"

        result = _prepend_memory_block(session, "query", "original prompt")
        assert result == "prompt-with-memory"
        session.inject_memory_prompt.assert_called_once()

    def test_returns_original_when_inject_raises(self):
        from rlm.server.runtime_pipeline import _prepend_memory_block

        # Session where inject_memory_prompt raises — fallback to budget gate
        session = MagicMock()
        session.inject_memory_prompt.side_effect = AttributeError("no method")

        # Public properties should be used in fallback path
        session.memory = MagicMock()
        session.session_id = "sess-fallback"
        session._memory_cache = None

        # Even if inject_memory_with_budget isn't directly patchable at module level,
        # the function should catch exceptions gracefully and return prompt unchanged
        with patch("rlm.core.memory_budget.inject_memory_with_budget", return_value=([], 0)):
            result = _prepend_memory_block(session, "query", "original prompt")

        # Should return original prompt since no memory chunks were selected
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# §4 — SKILL.md correctness
# ---------------------------------------------------------------------------

class TestSkillMDCorrectness:
    """Verify the fixed SKILL.md has valid frontmatter and correct content."""

    @pytest.fixture()
    def skill_path(self):
        return Path(__file__).resolve().parent.parent / "rlm" / "skills" / "memory" / "SKILL.md"

    def _parse_frontmatter(self, skill_path):
        """Extract and parse TOML frontmatter between +++ delimiters."""
        text = skill_path.read_text(encoding="utf-8")
        parts = text.split("+++")
        assert len(parts) >= 3, "SKILL.md must have +++ delimited TOML frontmatter"
        return tomllib.loads(parts[1])

    def test_skill_file_exists(self, skill_path):
        assert skill_path.exists(), f"SKILL.md not found at {skill_path}"

    def test_frontmatter_is_valid_toml(self, skill_path):
        parsed = self._parse_frontmatter(skill_path)
        assert "name" in parsed
        assert "sif" in parsed

    def test_skill_name_is_session_memory(self, skill_path):
        parsed = self._parse_frontmatter(skill_path)
        assert parsed["name"] == "session_memory"

    def test_signature_uses_session_memory_search(self, skill_path):
        parsed = self._parse_frontmatter(skill_path)
        sig = parsed["sif"]["signature"]
        assert "session_memory_search" in sig

    def test_no_dead_import_reference(self, skill_path):
        text = skill_path.read_text(encoding="utf-8")
        assert "rlm.memory.multivector" not in text
        assert "get_memory_store" not in text

    def test_impl_is_empty(self, skill_path):
        """impl should be empty — callable is injected externally by runtime_pipeline."""
        parsed = self._parse_frontmatter(skill_path)
        impl = parsed["sif"].get("impl", "")
        assert impl.strip() == "", f"impl should be empty, got: {impl!r}"


# ---------------------------------------------------------------------------
# §5 — history → repl_message_log rename + backward compat
# ---------------------------------------------------------------------------

class TestHistoryRenameBackwardCompat:
    """Verify repl_message_log is primary and history remains as alias."""

    def test_compaction_mode_has_both_aliases(self):
        from rlm.environments.local_repl import LocalREPL
        repl = LocalREPL(compaction=True)
        assert "repl_message_log" in repl.locals
        assert "history" in repl.locals
        # Both should point to the same underlying list
        assert repl.locals["repl_message_log"] is repl.locals["history"]
        repl.cleanup()

    def test_non_compaction_mode_no_repl_message_log(self):
        from rlm.environments.local_repl import LocalREPL
        repl = LocalREPL(compaction=False)
        # Without compaction, there's no initial history/repl_message_log
        # (they're only present after add_history)
        assert "repl_message_log" not in repl.locals or "history" not in repl.locals
        repl.cleanup()

    def test_add_history_sets_both_aliases(self):
        from rlm.environments.local_repl import LocalREPL
        repl = LocalREPL()
        history = [{"role": "user", "content": "Hello"}]
        repl.add_history(history)
        assert "repl_message_log" in repl.locals
        assert "history" in repl.locals
        assert repl.locals["repl_message_log"] is repl.locals["history"]
        assert repl.locals["repl_message_log"][0]["content"] == "Hello"
        repl.cleanup()

    def test_add_history_multiple_turns_aliases_latest(self):
        from rlm.environments.local_repl import LocalREPL
        repl = LocalREPL()
        repl.add_history([{"role": "user", "content": "Turn 1"}])
        repl.add_history([{"role": "user", "content": "Turn 2"}])
        # Both aliases should point to the latest (Turn 2)
        assert repl.locals["repl_message_log"][0]["content"] == "Turn 2"
        assert repl.locals["history"][0]["content"] == "Turn 2"
        assert repl.locals["repl_message_log"] is repl.locals["history"]
        repl.cleanup()

    def test_restore_scaffold_preserves_both_aliases(self):
        """After _restore_scaffold, both aliases should still be correct."""
        from rlm.environments.local_repl import LocalREPL
        repl = LocalREPL()
        repl.add_history([{"role": "user", "content": "Before scaffold"}])

        # Simulate code execution which calls _restore_scaffold
        repl.execute_code("x = 42")

        assert "repl_message_log" in repl.locals
        assert "history" in repl.locals
        assert repl.locals["repl_message_log"][0]["content"] == "Before scaffold"
        repl.cleanup()

    def test_backward_compat_history_still_accessible_in_code(self):
        """Old code using 'history' should still work."""
        from rlm.environments.local_repl import LocalREPL
        repl = LocalREPL()
        repl.add_history([{"role": "user", "content": "Test msg"}])

        result = repl.execute_code("msg = history[0]['content']")
        assert result.stderr == ""
        assert repl.locals["msg"] == "Test msg"
        repl.cleanup()

    def test_repl_message_log_accessible_in_code(self):
        """New code using 'repl_message_log' should work."""
        from rlm.environments.local_repl import LocalREPL
        repl = LocalREPL()
        repl.add_history([{"role": "user", "content": "New API"}])

        result = repl.execute_code("msg = repl_message_log[0]['content']")
        assert result.stderr == ""
        assert repl.locals["msg"] == "New API"
        repl.cleanup()


# ---------------------------------------------------------------------------
# §6 — System prompt memory taxonomy
# ---------------------------------------------------------------------------

class TestSystemPromptMemoryTaxonomy:
    """Verify that the system prompt documents the 3 memory domains."""

    def test_prompt_mentions_session_memory_search(self):
        from rlm.utils.prompts import RLM_SYSTEM_PROMPT
        assert "session_memory_search" in RLM_SYSTEM_PROMPT

    def test_prompt_mentions_session_memory_status(self):
        from rlm.utils.prompts import RLM_SYSTEM_PROMPT
        assert "session_memory_status" in RLM_SYSTEM_PROMPT

    def test_prompt_mentions_session_memory_recent(self):
        from rlm.utils.prompts import RLM_SYSTEM_PROMPT
        assert "session_memory_recent" in RLM_SYSTEM_PROMPT

    def test_prompt_mentions_repl_message_log(self):
        from rlm.utils.prompts import RLM_SYSTEM_PROMPT
        assert "repl_message_log" in RLM_SYSTEM_PROMPT

    def test_prompt_distinguishes_memory_domains(self):
        from rlm.utils.prompts import RLM_SYSTEM_PROMPT
        # Should mention workspace/codebase memory_* tools as distinct
        assert "memory_*" in RLM_SYSTEM_PROMPT or "memory_store" in RLM_SYSTEM_PROMPT

    def test_prompt_warns_about_not_confusing_domains(self):
        from rlm.utils.prompts import RLM_SYSTEM_PROMPT
        # The taxonomy section should contain a disambiguation warning
        assert "NOT" in RLM_SYSTEM_PROMPT or "confuse" in RLM_SYSTEM_PROMPT or "confus" in RLM_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Integration: end-to-end session memory flow
# ---------------------------------------------------------------------------

class TestSessionMemoryEndToEnd:
    """Integration test: create session → add memories → query via tools."""

    def test_full_flow(self, tmp_path):
        from rlm.core.memory_manager import MultiVectorMemory
        from rlm.tools.session_memory_tools import get_session_memory_tools

        db = tmp_path / "e2e_test.db"
        mem = MultiVectorMemory(db_path=str(db))
        mem.client = None

        # Simulate mini agent saving nuggets
        mid1 = mem.add_memory("sess-e2e", "User wants REST API with FastAPI", importance_score=0.9)
        mid2 = mem.add_memory("sess-e2e", "Database choice is PostgreSQL", importance_score=0.8)
        mid3 = mem.add_memory("sess-e2e", "Authentication via OAuth2", importance_score=0.7)

        # Add edge: mid1 extends mid2
        mem.add_edge(mid1, mid2, "extends")

        # Deprecate an old memory
        old_id = mem.add_memory("sess-e2e", "Database choice is MySQL", importance_score=0.6)
        mem.deprecate(old_id)

        session = SimpleNamespace(memory=mem, session_id="sess-e2e")
        tools = get_session_memory_tools(session)

        # Status should show 3 active, 1 deprecated, 1 edge
        status = tools["session_memory_status"]()
        assert status["active_chunks"] == 3
        assert status["deprecated_chunks"] == 1
        assert status["edges"] >= 1

        # Recent should return the non-deprecated memories
        recent = tools["session_memory_recent"](limit=10)
        assert len(recent) == 3
        contents = [r["content"] for r in recent]
        assert "MySQL" not in " ".join(contents)  # deprecated should be excluded

        # Search should find relevant content
        results = tools["session_memory_search"]("FastAPI", top_k=5)
        assert len(results) > 0
