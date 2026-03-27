from __future__ import annotations

import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rlm.core.supervisor import RLMSupervisor
from rlm.server.runtime_pipeline import _fire_post_turn_memory, _prepend_memory_block
from rlm.tools.memory import RLMMemory


class TestRLMMemoryLayerIsolation:
    def test_raw_and_knowledge_layers_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = RLMMemory(memory_dir=tmpdir, enable_embeddings=False)
            mem.db.get_embedding = lambda text: []

            mem.store("src/app.py", "print('raw-layer')\n")
            mem.analyze("src/app.py", "This file prints from the raw layer.")

            assert mem.read("src/app.py") == "print('raw-layer')\n"
            knowledge = mem.get_knowledge("src/app.py")
            assert knowledge is not None
            assert knowledge["analysis"] == "This file prints from the raw layer."

    def test_links_persist_on_knowledge_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = RLMMemory(memory_dir=tmpdir, enable_embeddings=False)
            mem.db.get_embedding = lambda text: []

            mem.analyze("router.py", "Routes requests.")
            msg = mem.link("router.py", "depends_on", "service.py")

            assert "Created link" in msg
            assert mem.get_links("router.py") == [{"relation": "depends_on", "target": "service.py"}]

    def test_search_and_list_return_public_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = RLMMemory(memory_dir=tmpdir, enable_embeddings=False)
            mem.db.get_embedding = lambda text: []

            mem.store("docs/spec.md", "Persistent memory specification")
            mem.analyze("docs/spec.md", "Knowledge entry for the same public key")

            listed = mem.list_keys()
            assert listed == ["docs/spec.md"]

            results = mem.search("specification")
            assert results
            assert all(result["key"] == "docs/spec.md" for result in results)

    def test_workspace_scope_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = RLMMemory(memory_dir=tmpdir, enable_embeddings=False, scope_name="repo-main")

            assert mem.scope_kind == "workspace"
            assert mem.session_id == "workspace::repo-main"
            assert "Scope: workspace::repo-main" in mem.status()


class TestRuntimePipelineMemoryDelegation:
    def test_prepend_memory_block_prefers_session_contract(self):
        session = MagicMock()
        session.inject_memory_prompt.return_value = "prompt-with-memory"

        result = _prepend_memory_block(session, "hello", "prompt")

        assert result == "prompt-with-memory"
        session.inject_memory_prompt.assert_called_once_with("prompt", "hello", available_tokens=2500)

    def test_fire_post_turn_memory_prefers_session_contract(self):
        session = MagicMock()

        _fire_post_turn_memory(session, "hello", "world")

        session.schedule_post_turn_memory.assert_called_once_with("hello", "world")


class TestRLMSessionMemoryLifecycle:
    def test_close_releases_hot_cache(self):
        from rlm.core.memory_hot_cache import get_or_create_cache, registry_size, _registry
        from rlm.session import RLMSession

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_rlm = MagicMock()
            with patch("rlm.core.rlm.RLM", return_value=fake_rlm):
                session = RLMSession(memory_db_path=f"{tmpdir}/memory.db", session_id="sess-close")

            cache = get_or_create_cache("sess-close")
            cache.chunks = [{"content": "stale"}]
            assert "sess-close" in _registry
            size_before = registry_size()

            session.close()

            # Contrato: close() remove ESTA sessão do registry e anula o cache local
            assert session._memory_cache is None
            assert "sess-close" not in _registry
            assert registry_size() == size_before - 1

    def test_close_multi_session_isolamento(self):
        """Fechar sessão A não afeta sessão B — contrato multi-tenant."""
        from rlm.core.memory_hot_cache import get_or_create_cache, evict_cache, _registry
        from rlm.session import RLMSession

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_rlm = MagicMock()
            with patch("rlm.core.rlm.RLM", return_value=fake_rlm):
                session_a = RLMSession(memory_db_path=f"{tmpdir}/a.db", session_id="sess-multi-a")
                session_b = RLMSession(memory_db_path=f"{tmpdir}/b.db", session_id="sess-multi-b")

            # Ambas registradas
            assert "sess-multi-a" in _registry
            assert "sess-multi-b" in _registry

            cache_b = get_or_create_cache("sess-multi-b")
            cache_b.chunks = [{"content": "b-important"}]

            # Fechar A não toca B
            session_a.close()
            assert "sess-multi-a" not in _registry
            assert "sess-multi-b" in _registry
            assert session_b._memory_cache.read_sync() == [{"content": "b-important"}]

            # Cleanup
            session_b.close()
            assert "sess-multi-b" not in _registry

    def test_reset_invalidates_hot_cache(self):
        from rlm.session import RLMSession

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_rlm = MagicMock()
            with patch("rlm.core.rlm.RLM", return_value=fake_rlm):
                session = RLMSession(memory_db_path=f"{tmpdir}/memory.db", session_id="sess-reset")

            session._memory_cache.chunks = [{"content": "cached"}]
            session._memory_cache.last_updated = 123.0

            session.reset()

            assert session._memory_cache.read_sync() == []
            assert session._memory_cache.last_updated == 0.0


class TestSupervisorTelemetryDelegation:
    def test_supervisor_uses_session_telemetry_contract(self):
        completion = SimpleNamespace(response="ok", usage_summary=None)
        rlm_session = MagicMock()
        rlm_session.max_iterations = 4
        rlm_session.completion.return_value = completion
        rlm_session.start_turn_telemetry.return_value = "turn-1"

        session = SimpleNamespace(
            session_id="sess-telemetry",
            status="idle",
            total_tokens_used=0,
            total_completions=0,
            last_error="",
            rlm_instance=rlm_session,
        )

        result = RLMSupervisor().execute(session, "hello", root_prompt="hello")

        assert result.status == "completed"
        rlm_session.start_turn_telemetry.assert_called_once_with("hello")
        rlm_session.finish_turn_telemetry.assert_called_once_with(
            "turn-1",
            completion=completion,
            compaction_triggered=False,
        )