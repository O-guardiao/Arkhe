from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

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
        from rlm.core.memory_hot_cache import get_or_create_cache, registry_size
        from rlm.session import RLMSession

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_rlm = MagicMock()
            with patch("rlm.core.rlm.RLM", return_value=fake_rlm):
                session = RLMSession(memory_db_path=f"{tmpdir}/memory.db", session_id="sess-close")

            cache = get_or_create_cache("sess-close")
            cache.chunks = [{"content": "stale"}]
            assert registry_size() >= 1

            session.close()

            assert session._memory_cache is None
            assert registry_size() == 0

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