"""Tests for LocalREPL persistence features.

These tests verify LocalREPL's multi-context and multi-history capabilities
which support the persistent=True mode in RLM for multi-turn conversations.
"""

import asyncio
from types import SimpleNamespace
from pathlib import Path

from rlm.core.sibling_bus import SiblingBus
from rlm.environments.local_repl import LocalREPL


class TestLocalREPLMultiContext:
    """Tests for multi-context support in persistent mode."""

    def test_add_context_versioning(self):
        """Test that add_context creates versioned variables."""
        repl = LocalREPL()
        repl.add_context("First", 0)
        repl.add_context("Second", 1)
        assert repl.locals["context_0"] == "First"
        assert repl.locals["context_1"] == "Second"
        assert repl.locals["context"] == "Second"  # context aliases latest
        assert repl.get_context_count() == 2
        repl.cleanup()

    def test_update_handler_address(self):
        """Test handler address can be updated."""
        repl = LocalREPL(lm_handler_address=("127.0.0.1", 5000))
        repl.update_handler_address(("127.0.0.1", 6000))
        assert repl.lm_handler_address == ("127.0.0.1", 6000)
        repl.cleanup()

    def test_add_context_auto_increment(self):
        """Test that add_context auto-increments when no index provided."""
        repl = LocalREPL()
        idx1 = repl.add_context("First")
        idx2 = repl.add_context("Second")
        assert idx1 == 0
        assert idx2 == 1
        assert repl.locals["context_0"] == "First"
        assert repl.locals["context_1"] == "Second"
        assert repl.get_context_count() == 2
        repl.cleanup()

    def test_contexts_accessible_in_code(self):
        """Test that multiple contexts can be accessed in code execution."""
        repl = LocalREPL()
        repl.add_context("Document A content")
        repl.add_context("Document B content")

        result = repl.execute_code("combined = f'{context_0} + {context_1}'")
        assert result.stderr == ""
        assert repl.locals["combined"] == "Document A content + Document B content"
        repl.cleanup()

    def test_context_alias_points_to_latest(self):
        """Test that 'context' aliases the latest context (not context_0)."""
        repl = LocalREPL()
        repl.add_context("First")
        repl.add_context("Second")
        repl.add_context("Third")

        result = repl.execute_code("is_latest = context == context_2")
        assert result.stderr == ""
        assert repl.locals["is_latest"] is True
        repl.cleanup()


class TestLocalREPLHistory:
    """Tests for message history storage in LocalREPL for persistent sessions."""

    def test_add_history_basic(self):
        """Test that add_history stores message history correctly."""
        repl = LocalREPL()

        history = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        index = repl.add_history(history)

        assert index == 0
        assert "history_0" in repl.locals
        assert "history" in repl.locals  # alias
        assert repl.locals["history_0"] == history
        assert repl.locals["history"] == history
        assert repl.get_history_count() == 1

        repl.cleanup()

    def test_add_multiple_histories(self):
        """Test adding multiple conversation histories."""
        repl = LocalREPL()

        history1 = [{"role": "user", "content": "First conversation"}]
        history2 = [{"role": "user", "content": "Second conversation"}]

        repl.add_history(history1)
        repl.add_history(history2)

        assert repl.get_history_count() == 2
        assert repl.locals["history_0"] == history1
        assert repl.locals["history_1"] == history2
        assert repl.locals["history"] == history1  # alias stays on first

        repl.cleanup()

    def test_history_accessible_via_code(self):
        """Test that stored history is accessible via code execution."""
        repl = LocalREPL()

        history = [{"role": "user", "content": "Test message"}]
        repl.add_history(history)

        result = repl.execute_code("msg = history[0]['content']")
        assert result.stderr == ""
        assert repl.locals["msg"] == "Test message"

        repl.cleanup()

    def test_history_is_copy(self):
        """Test that stored history is a copy, not a reference."""
        repl = LocalREPL()

        history = [{"role": "user", "content": "Original"}]
        repl.add_history(history)

        history[0]["content"] = "Modified"

        assert repl.locals["history_0"][0]["content"] == "Original"

        repl.cleanup()

    def test_can_iterate_histories_in_code(self):
        """Test iterating through multiple histories in code."""
        repl = LocalREPL()

        repl.add_history([{"role": "user", "content": "Query 1"}])
        repl.add_history([{"role": "user", "content": "Query 2"}])
        repl.add_history([{"role": "user", "content": "Query 3"}])

        code = """
all_contents = [
    history_0[0]['content'],
    history_1[0]['content'],
    history_2[0]['content'],
]
"""
        result = repl.execute_code(code)
        assert result.stderr == ""
        assert repl.locals["all_contents"] == ["Query 1", "Query 2", "Query 3"]

        repl.cleanup()


class TestLocalREPLPersistentState:
    """Tests for state persistence across multiple operations in a single REPL instance."""

    def test_variables_persist_with_contexts(self):
        """Variables and contexts should coexist."""
        repl = LocalREPL()

        repl.add_context("My context data")
        repl.execute_code("summary = context.upper()")
        assert repl.locals["summary"] == "MY CONTEXT DATA"

        repl.add_context("New context")

        assert repl.locals["summary"] == "MY CONTEXT DATA"
        assert repl.locals["context_1"] == "New context"

        repl.cleanup()

    def test_variables_persist_with_histories(self):
        """Variables and histories should coexist."""
        repl = LocalREPL()

        repl.add_history([{"role": "user", "content": "Hello"}])
        repl.execute_code("extracted = history[0]['content']")
        assert repl.locals["extracted"] == "Hello"

        repl.add_history([{"role": "user", "content": "World"}])

        assert repl.locals["extracted"] == "Hello"
        assert repl.locals["history_1"][0]["content"] == "World"

        repl.cleanup()

    def test_full_persistent_session_simulation(self):
        """Simulate a multi-turn persistent session."""
        repl = LocalREPL()

        repl.add_context("Document: Sales were $1000")
        repl.execute_code("sales = 1000")

        repl.add_context("Document: Costs were $400")
        result = repl.execute_code("profit = sales - 400")
        assert result.stderr == ""
        assert repl.locals["profit"] == 600

        repl.add_history(
            [
                {"role": "user", "content": "What were the sales?"},
                {"role": "assistant", "content": "Sales were $1000"},
            ]
        )

        code = """
summary = f"Sales: {context_0}, Costs: {context_1}, Profit: {profit}"
prev_question = history_0[0]['content']
"""
        result = repl.execute_code(code)
        assert result.stderr == ""
        assert "Profit: 600" in repl.locals["summary"]
        assert repl.locals["prev_question"] == "What were the sales?"

        assert repl.get_context_count() == 2
        assert repl.get_history_count() == 1

        repl.cleanup()


class TestLocalREPLRuntimeWorkbench:
    """Tests for task ledger, attachments and timeline persistence."""

    def test_runtime_workbench_apis_are_available(self, tmp_path: Path):
        """Task ledger, attachments and timeline should be callable from REPL code."""
        repl = LocalREPL()
        sample = tmp_path / "sample.txt"
        sample.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

        result = repl.execute_code(
            f'''
task = task_start("Normalize dataset")
attach_text("brief", "Need to normalize the current dataset.")
attach_file(r"{sample}", start_line=2, end_line=3)
timeline_mark("analysis.started", {{"source": "test"}})
current_task = task_current()
attachments = attachment_list()
events = timeline_recent(limit=10)
'''
        )

        assert result.stderr == ""
        assert repl.locals["current_task"]["title"] == "Normalize dataset"
        assert len(repl.locals["attachments"]) == 2
        assert any(entry["event_type"] == "analysis.started" for entry in repl.locals["events"])
        repl.cleanup()

    def test_runtime_workbench_persists_in_checkpoint(self, tmp_path: Path):
        """Task ledger, attachments and timeline should survive checkpoint restore."""
        checkpoint = tmp_path / "runtime-checkpoint.json"

        repl = LocalREPL()
        result = repl.execute_code(
            '''
task = task_start("Review context")
attach_context("payload", {"kind": "json", "items": [1, 2, 3]})
timeline_mark("checkpoint.prepared", {"ok": True})
'''
        )
        assert result.stderr == ""
        save_message = repl.save_checkpoint(str(checkpoint))
        assert "Checkpoint saved" in save_message
        repl.cleanup()

        restored = LocalREPL()
        load_message = restored.load_checkpoint(str(checkpoint))
        snapshot = restored.get_runtime_state_snapshot()

        assert "Checkpoint restored" in load_message
        assert snapshot["tasks"]["current"]["title"] == "Review context"
        assert snapshot["attachments"]["items"][0]["label"] == "payload"
        assert any(
            entry["event_type"] == "checkpoint.prepared"
            for entry in snapshot["timeline"]["entries"]
        )
        restored.cleanup()

    def test_coordination_digest_tracks_sibling_bus_events(self):
        """The runtime snapshot should reflect sibling bus traffic and control signals."""
        bus = SiblingBus()
        repl = LocalREPL()
        repl.attach_sibling_bus(bus, branch_id=7)

        bus.publish("results/ready", {"ok": True}, sender_id=2)
        bus.publish_control("control/stop", {"reason": "winner-found"}, sender_id=3)
        _ = bus.subscribe("results/ready", timeout_s=0.01)
        _ = bus.poll_control("control/stop", receiver_id=7)

        snapshot = repl.get_runtime_state_snapshot()
        coordination = snapshot["coordination"]

        assert coordination["attached"] is True
        assert coordination["branch_id"] == 7
        assert coordination["latest_stats"]["operation_counts"]["publish"] >= 1
        assert coordination["latest_stats"]["operation_counts"]["control_publish"] >= 1
        assert any(event["operation"] == "publish" for event in coordination["events"])
        assert any(event["operation"] == "control_poll_hit" for event in coordination["events"])
        assert any(
            entry["event_type"] == "coordination.bus_event"
            for entry in snapshot["timeline"]["entries"]
        )
        repl.cleanup()

    def test_runtime_snapshot_filters_coordination_and_branch_tasks(self):
        """Runtime snapshot should filter coordination events and preserve branch-task bindings."""
        repl = LocalREPL()
        repl.register_subagent_task(
            mode="parallel",
            title="branch zero",
            branch_id=0,
            metadata={"role": "winner"},
        )
        repl.register_subagent_task(
            mode="parallel",
            title="branch one",
            branch_id=1,
            metadata={"role": "loser"},
        )
        repl._coordination_digest.record_event("publish", topic="result/main", sender_id=0)
        repl._coordination_digest.record_event("control_publish", topic="control/stop", sender_id=0)

        snapshot = repl.get_runtime_state_snapshot(
            coordination_limit=10,
            coordination_operation="control_publish",
            coordination_topic="control/stop",
            coordination_branch_id=0,
        )

        assert len(snapshot["coordination"]["events"]) == 1
        assert snapshot["coordination"]["events"][0]["topic"] == "control/stop"
        assert len(snapshot["coordination"]["branch_tasks"]) == 1
        assert snapshot["coordination"]["branch_tasks"][0]["branch_id"] == 0
        repl.cleanup()

    def test_runtime_endpoint_supports_coordination_filters(self, monkeypatch):
        """Session runtime endpoint should forward coordination filters to the snapshot."""
        from rlm.server import api as api_module

        monkeypatch.setattr(api_module, "_require_admin_api_auth", lambda request: None)

        repl = LocalREPL()
        repl.register_subagent_task(mode="parallel", title="branch zero", branch_id=0)
        repl._coordination_digest.record_event("control_publish", topic="control/stop", sender_id=0)
        session = SimpleNamespace(rlm_instance=SimpleNamespace(_persistent_env=repl))
        session_manager = SimpleNamespace(get_session=lambda session_id: session)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=session_manager)))

        response = asyncio.run(
            api_module.get_session_runtime(
                "session-1",
                request,
                coordination_limit=5,
                coordination_operation="control_publish",
                coordination_topic="control/stop",
                coordination_branch_id=0,
            )
        )

        assert response["runtime"]["coordination"]["filters"]["branch_id"] == 0
        assert response["runtime"]["coordination"]["events"][0]["topic"] == "control/stop"
        repl.cleanup()
