"""Checkpoint save/load mixin for LocalREPL.

Provides state serialization to disk and restoration from checkpoint files,
enabling sleep/wake patterns for REPL sessions.

Extracted from local_repl.py during responsibility separation refactoring.
"""

from __future__ import annotations

import json
import os
from typing import Any


class CheckpointMixin:
    """Save and restore REPL state to/from disk.

    Assumes the concrete class sets the following attributes:

    - ``_context_count``, ``_history_count``
    - ``_task_ledger``, ``_context_attachments``, ``_execution_timeline``
    - ``_recursive_session``, ``_coordination_digest``
    - ``_runtime_control_state``
    - ``locals``, ``globals``
    - ``depth``, ``_originating_channel``
    - ``_llm_query_batched`` (method)
    """

    def save_checkpoint(self, checkpoint_path: str) -> str:
        """Serialize the REPL state to disk for later restoration.

        Saves:
        - All REPL local variables (serializable ones)
        - Context and history counts
        - Codebase mode flag and path
        - Memory directory reference

        Args:
            checkpoint_path: Absolute path to save the checkpoint JSON.

        Returns:
            Status message.
        """
        import pickle
        import base64

        state = {
            "version": "1.0",
            "context_count": self._context_count,
            "history_count": self._history_count,
            "codebase_mode": getattr(self, "_codebase_mode", False),
            "codebase_path": getattr(self, "_codebase_path", None),
            "runtime_workbench": {
                "tasks": self._task_ledger.snapshot(),
                "attachments": self._context_attachments.snapshot(),
                "timeline": self._execution_timeline.snapshot(),
                "recursive_session": self._recursive_session.snapshot(),
                "coordination": self._coordination_digest.snapshot(),
                "controls": self.get_runtime_control_state(),
            },
            "locals_serialized": {},
            "locals_skipped": [],
        }

        # Serialize locals — skip non-serializable objects (functions, etc.)
        for key, value in self.locals.items():
            if key.startswith("_"):
                continue
            try:
                # SECURITY NOTE: pickle is used for local checkpoint files only.
                # Do not load checkpoint files from untrusted sources.
                encoded = base64.b64encode(pickle.dumps(value)).decode("ascii")
                state["locals_serialized"][key] = {
                    "type": type(value).__name__,
                    "data": encoded,
                }
            except (pickle.PicklingError, TypeError, AttributeError):
                state["locals_skipped"].append(key)

        os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
        with open(checkpoint_path, "w") as f:
            json.dump(state, f, indent=2)

        saved = len(state["locals_serialized"])
        skipped = len(state["locals_skipped"])
        return f"Checkpoint saved: {saved} variables serialized, {skipped} skipped. Path: {checkpoint_path}"

    def load_checkpoint(self, checkpoint_path: str) -> str:
        """Restore the REPL state from a checkpoint file.

        Re-injects codebase and memory tools if the checkpoint was in codebase mode.

        SECURITY NOTE: Uses ``pickle.loads()`` for variable restoration.
        Only load checkpoint files you trust — pickle can execute arbitrary code.

        Args:
            checkpoint_path: Absolute path to the checkpoint JSON.

        Returns:
            Status message.
        """
        import pickle
        import base64

        if not os.path.exists(checkpoint_path):
            return f"Error: Checkpoint not found at {checkpoint_path}"

        with open(checkpoint_path, "r") as f:
            state = json.load(f)

        # Restore counts
        self._context_count = state.get("context_count", 0)
        self._history_count = state.get("history_count", 0)

        # Restore serialized locals
        restored = 0
        for key, info in state.get("locals_serialized", {}).items():
            try:
                value = pickle.loads(base64.b64decode(info["data"]))
                self.locals[key] = value
                restored += 1
            except Exception:
                pass

        runtime_workbench = state.get("runtime_workbench", {})
        self._task_ledger.restore(runtime_workbench.get("tasks"))
        self._context_attachments.restore(runtime_workbench.get("attachments"))
        self._execution_timeline.restore(runtime_workbench.get("timeline"))
        self._recursive_session.restore(runtime_workbench.get("recursive_session"))
        self._coordination_digest.restore(runtime_workbench.get("coordination"))
        self._runtime_control_state = dict(runtime_workbench.get("controls") or self._runtime_control_state)

        # Re-inject codebase tools if checkpoint was in codebase mode
        codebase_path = state.get("codebase_path")
        if state.get("codebase_mode") and codebase_path and os.path.isdir(codebase_path):
            from rlm.tools.codebase import get_codebase_tools
            from rlm.tools.memory import RLMMemory
            from rlm.tools.memory_tools import get_memory_tools
            from rlm.core.engine.runtime_workbench import AgentContext

            # Re-inject codebase tools
            tools = get_codebase_tools(codebase_path)
            for name, func in tools.items():
                self.globals[name] = func

            # Re-initialize memory (preserving agent context from current instance)
            memory_dir = os.path.join(codebase_path, ".rlm_memory")
            self._memory = RLMMemory(memory_dir, scope_name=os.path.basename(os.path.abspath(codebase_path)))
            self._memory._agent_context = AgentContext(depth=self.depth, role="root", channel=self._originating_channel)
            memory_tools = get_memory_tools(self._memory, codebase_path, llm_query_batched_fn=self._llm_query_batched)
            for name, func in memory_tools.items():
                self.globals[name] = func

            self._codebase_mode = True
            self._codebase_path = codebase_path

        skipped = state.get("locals_skipped", [])
        return f"Checkpoint restored: {restored} variables loaded, {len(skipped)} skipped ({skipped})"
