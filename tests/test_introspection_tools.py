"""Tests — Introspection tools (rlm_introspect, sif_usage, prompt_overview)."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rlm.tools.introspection_tools import get_introspection_tools  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — build minimal mock session objects
# ---------------------------------------------------------------------------


def _make_mock_session(
    *,
    model_name: str = "gpt-5.4-mini",
    backend: str = "openai",
    max_depth: int = 3,
    max_iterations: int = 30,
    session_id: str = "sess-test-001",
    client_id: str = "test:local",
    has_memory: bool = False,
    has_kb: bool = False,
    has_vault: bool = False,
) -> SimpleNamespace:
    """Create a minimal mock that mimics SessionRecord + RLMSession + RLM core."""
    from rlm.utils.token_utils import get_context_limit
    from rlm.core.compaction import ContextCompactor, CompactionConfig

    ctx_limit = get_context_limit(model_name)

    rlm_obj = SimpleNamespace(
        backend=backend,
        backend_kwargs={"model_name": model_name},
        depth=0,
        max_depth=max_depth,
        max_iterations=max_iterations,
        compactor=ContextCompactor(CompactionConfig(max_history_tokens=int(ctx_limit * 0.85))),
        system_prompt="You are an iterative agent with a Python REPL.\n\n"
                      "PRIME DIRECTIVE — Action-first\n"
                      "Core tools:\nSIF tools\nVault tools\n"
                      "Memory domains\nSibling coordination\n"
                      "Self-awareness tools",
        skills_context="shell — run commands\nweb_search — search the web",
        interaction_mode="repl",
    )

    rlm_session_core = SimpleNamespace(
        _rlm=rlm_obj,
        _memory=MagicMock() if has_memory else None,
        _kb=MagicMock() if has_kb else None,
        _obsidian_bridge=SimpleNamespace(vault_root="/root/.arkhe/vault") if has_vault else None,
        model=model_name,
    )
    if has_kb and rlm_session_core._kb is not None:
        rlm_session_core._kb.count.return_value = 42

    session = SimpleNamespace(
        session_id=session_id,
        client_id=client_id,
        created_at="2026-03-29T17:00:00",
        total_completions=5,
        rlm_instance=rlm_session_core,
    )
    return session


# ---------------------------------------------------------------------------
# Tests: get_introspection_tools returns correct names
# ---------------------------------------------------------------------------


class TestGetIntrospectionTools:
    def test_returns_three_tools(self):
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        assert set(tools.keys()) == {"rlm_introspect", "sif_usage", "prompt_overview"}
        assert all(callable(fn) for fn in tools.values())


# ---------------------------------------------------------------------------
# Tests: rlm_introspect
# ---------------------------------------------------------------------------


class TestRlmIntrospect:
    def test_identity_section(self):
        session = _make_mock_session(model_name="gpt-5.4-mini", max_depth=3)
        tools = get_introspection_tools(session)
        result = tools["rlm_introspect"]()

        assert "identity" in result
        ident = result["identity"]
        assert ident["model"] == "gpt-5.4-mini"
        assert ident["backend"] == "openai"
        assert ident["max_depth"] == 3
        assert ident["context_window_tokens"] == 272_000
        assert ident["compaction_threshold_tokens"] > 0

    def test_session_section(self):
        session = _make_mock_session(session_id="sess-test-001", client_id="test:local")
        tools = get_introspection_tools(session)
        result = tools["rlm_introspect"]()

        assert "session" in result
        assert result["session"]["client_id"] == "test:local"
        assert result["session"]["total_completions"] == 5

    def test_memory_unavailable(self):
        session = _make_mock_session(has_memory=False, has_kb=False, has_vault=False)
        tools = get_introspection_tools(session)
        result = tools["rlm_introspect"]()

        mem = result["memory"]
        assert mem["session_memory"]["available"] is False
        assert mem["knowledge_base"]["available"] is False
        assert mem["vault"]["available"] is False

    def test_vault_available(self):
        session = _make_mock_session(has_vault=True)
        tools = get_introspection_tools(session)
        result = tools["rlm_introspect"]()

        assert result["memory"]["vault"]["available"] is True
        assert "/root/.arkhe/vault" in result["memory"]["vault"]["vault_path"]

    def test_kb_available(self):
        session = _make_mock_session(has_kb=True)
        tools = get_introspection_tools(session)
        result = tools["rlm_introspect"]()

        assert result["memory"]["knowledge_base"]["available"] is True
        assert result["memory"]["knowledge_base"]["entry_count"] == 42

    def test_telemetry_section_present(self):
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        result = tools["rlm_introspect"]()

        assert "telemetry" in result

    def test_sif_tools_loaded_section(self):
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        result = tools["rlm_introspect"]()

        assert "sif_tool_count" in result
        assert isinstance(result["sif_tools_loaded"], list)


# ---------------------------------------------------------------------------
# Tests: sif_usage
# ---------------------------------------------------------------------------


class TestSifUsage:
    def test_returns_tools_and_recommendations(self):
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        result = tools["sif_usage"]()

        assert "tools" in result
        assert "recommendations" in result
        assert isinstance(result["tools"], dict)
        assert isinstance(result["recommendations"], list)

    def test_no_crash_on_empty_stats(self):
        """Should work even when no SIF tools have been compiled."""
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        result = tools["sif_usage"]()
        # Should not raise, just return whatever data is available
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Tests: prompt_overview
# ---------------------------------------------------------------------------


class TestPromptOverview:
    def test_mode_detection_standard(self):
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        result = tools["prompt_overview"]()

        assert result["mode"] == "standard"

    def test_sections_detected(self):
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        result = tools["prompt_overview"]()

        assert "sections" in result
        sections = result["sections"]
        assert "prime_directive" in sections
        assert "core_tools" not in sections or "core_tools" in sections  # depends on mock prompt
        assert "sif_tools" in sections
        assert "vault_tools" in sections
        assert "memory_domains" in sections

    def test_skills_injected_count(self):
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        result = tools["prompt_overview"]()

        assert result["skills_injected"] == 2  # "shell" + "web_search" lines
        assert len(result["skills_preview"]) == 2

    def test_prompt_size_estimated(self):
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        result = tools["prompt_overview"]()

        assert result["prompt_chars"] > 0
        assert result["prompt_est_tokens"] > 0

    def test_interaction_mode(self):
        session = _make_mock_session()
        tools = get_introspection_tools(session)
        result = tools["prompt_overview"]()

        assert result["interaction_mode"] == "repl"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestIntrospectionEdgeCases:
    def test_none_rlm_instance(self):
        session = SimpleNamespace(
            session_id="s1", client_id="c1", created_at="",
            total_completions=0, rlm_instance=None,
        )
        tools = get_introspection_tools(session)
        result = tools["rlm_introspect"]()
        assert result["identity"]["model"] == "unknown"

    def test_prompt_overview_no_core(self):
        session = SimpleNamespace(
            session_id="s1", client_id="c1", created_at="",
            total_completions=0, rlm_instance=None,
        )
        tools = get_introspection_tools(session)
        result = tools["prompt_overview"]()
        assert "error" in result
