"""Local REPL environment with persistent Python namespace.

Executes code in a sandboxed namespace with access to context data,
LLM queries, task management, recursive session support, and checkpoint
save/restore.

This module is the core REPL engine. Supporting concerns are delegated to:

- ``_sandbox.py``       — Security builtins, safe_import, safe_open
- ``_repl_tools.py``    — REPL tool closure factory (task, attach, timeline, etc.)
- ``_runtime_state.py`` — RuntimeStateMixin (events, tasks, operator control)
- ``_checkpoint.py``    — CheckpointMixin (save/load state to disk)
"""

import copy
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, cast

from rlm.core.optimized.fast import LMRequest, send_lm_request, send_lm_request_batched
from rlm.core.engine.runtime_workbench import (
    CoordinationDigest,
    ContextAttachmentStore,
    ExecutionTimeline,
    RecursiveSessionLedger,
    TaskLedger,
)
from rlm.core.types import REPLResult, RLMChatCompletion
from rlm.environments.base_env import (
    RESERVED_TOOL_NAMES,
    NonIsolatedEnv,
    extract_tool_value,
    validate_custom_tools,
)

# --- Extracted modules --------------------------------------------------------
from rlm.environments._sandbox import (
    _BLOCKED_RUNTIME_MODULES,
    _SAFE_BUILTINS,
    _safe_import,
    _safe_open,
)
from rlm.environments._repl_tools import (
    build_scaffold_tools,
    build_interprocess_tools,
    build_critic_fuzz_tool,
    build_mcts_explore_tool,
)
from rlm.environments._runtime_state import RuntimeStateMixin
from rlm.environments._checkpoint import CheckpointMixin
from rlm.core.security.execution_policy import get_model_route_config


class LocalREPL(RuntimeStateMixin, CheckpointMixin, NonIsolatedEnv):
    """
    Local REPL environment with persistent Python namespace.
    Executes code in a sandboxed namespace with access to context data.
    """

    def __init__(
        self,
        lm_handler_address: tuple[str, int] | None = None,
        context_payload: dict | list | str | None = None,
        setup_code: str | None = None,
        persistent: bool = False,
        depth: int = 1,
        custom_tools: dict[str, Any] | None = None,
        custom_sub_tools: dict[str, Any] | None = None,
        compaction: bool = False,
        **kwargs,
    ):
        self._event_bus = kwargs.pop("event_bus", None)
        # Pop sibling bus before passing kwargs to super (BaseEnv doesn't know it)
        self._sibling_bus = kwargs.pop("_sibling_bus", None)
        self._sibling_branch_id: int | None = kwargs.pop("_sibling_branch_id", None)
        # Pop parent log queue (set by sub_rlm_async to receive child progress)
        self._parent_log_queue = kwargs.pop("_parent_log_queue", None)
        # Pop cancel event (set by sub_rlm_async so parent can cancel this child)
        self._cancel_event: "threading.Event | None" = kwargs.pop("_cancel_event", None)
        # Lacuna 1: Aceitar memória compartilhada do pai
        self._parent_memory = kwargs.pop("_parent_memory", None)
        # Multichannel: canal de origem (e.g. "telegram:123", "tui:default")
        self._originating_channel: str | None = kwargs.pop("_originating_channel", None)

        super().__init__(persistent=persistent, depth=depth, **kwargs)

        self.lm_handler_address = lm_handler_address
        self.original_cwd = os.getcwd()
        self.temp_dir = tempfile.mkdtemp(prefix=f"repl_env_{uuid.uuid4()}_")
        self._lock = threading.Lock()
        self._context_count: int = 0
        self._history_count: int = 0

        # Custom tools: functions/values available in the REPL
        self.custom_tools = custom_tools or {}
        # Sub-tools: inherited from custom_tools if not specified
        self.custom_sub_tools = (
            custom_sub_tools if custom_sub_tools is not None else self.custom_tools
        )
        # Validate custom tools don't override reserved names
        validate_custom_tools(self.custom_tools)

        self.compaction = compaction

        # Multi-model routing: resolve worker model for llm_query/llm_query_batched
        self._worker_model: str | None = get_model_route_config().worker_model

        # Phase 9.3: Depth-aware security auditor (sub-RLMs get stricter rules)
        from rlm.core.security import REPLAuditor, env_var_shield as _env_shield
        self._auditor = REPLAuditor(depth=depth)
        self._env_shield = _env_shield

        # Evolution 6.1: Epistemic Foraging — REPL failure tracking
        self._repl_failure_count: int = 0   # Consecutive failures (resets on success)
        self.foraging_threshold: int = 3    # Failures before entering Foraging Mode
        self._task_ledger = TaskLedger()
        self._context_attachments = ContextAttachmentStore()
        self._execution_timeline = ExecutionTimeline(on_record=self._publish_timeline_event)
        self._recursive_session = RecursiveSessionLedger()
        self._coordination_digest = CoordinationDigest()
        self._mcts_archives: dict[str, Any] = {}
        self._active_recursive_strategy: dict[str, Any] | None = None
        self._runtime_control_state: dict[str, Any] = {
            "paused": False,
            "pause_reason": "",
            "focused_branch_id": None,
            "fixed_winner_branch_id": None,
            "branch_priorities": {},
            "last_checkpoint_path": None,
            "last_checkpoint_at": None,
            "last_operator_note": "",
        }

        # Setup globals, locals, and modules in environment.
        self.setup()

        if self._sibling_bus is not None:
            self.attach_sibling_bus(self._sibling_bus, branch_id=self._sibling_branch_id)

        if compaction:
            self._compaction_history: list[Any] = []
            self.locals["repl_message_log"] = self._compaction_history
            self.locals["history"] = self._compaction_history  # backward compat alias

        # Load context if provided
        if context_payload is not None:
            self.load_context(context_payload)

        # Run setup code if provided
        if setup_code:
            self.execute_code(setup_code)

    def setup(self):
        """Setup the environment."""
        # Create sandboxed globals
        self.globals: dict[str, Any] = {
            "__builtins__": _SAFE_BUILTINS.copy(),
            "__name__": "__main__",
        }
        self.locals: dict[str, Any] = {}

        # Track LLM calls made during code execution
        self._pending_llm_calls: list[RLMChatCompletion] = []

        # Storage for FINAL(value) called from within REPL code blocks
        self._pending_final_value: str | None = None

        # Add helper functions
        self.globals["FINAL_VAR"] = self._final_var
        self.globals["FINAL"] = self._final
        self.globals["SHOW_VARS"] = self._show_vars
        self.globals["get_var"] = self._get_var
        self.globals["llm_query"] = self._llm_query
        self.globals["llm_query_batched"] = self._llm_query_batched

        # Phase 9.3: Env var shield — safe access to os.environ from REPL
        self.globals["env_shield"] = self._env_shield

        # Evolution 6.1: Expose reset_foraging as REPL tool
        self.globals["reset_foraging"] = self.reset_foraging

        # Build scaffold tool closures (task, attach, timeline, recursive session)
        self._runtime_scaffold_refs = build_scaffold_tools(self)
        self.globals.update(self._runtime_scaffold_refs)

        # Custom tools: inject into globals (callables) or locals (values)
        for name, entry in self.custom_tools.items():
            value = extract_tool_value(entry)
            if callable(value):
                self.globals[name] = value
            else:
                self.locals[name] = value

        # Sibling bus: injected during sub_rlm_parallel() for P2P communication
        # between sibling agents. Provides sibling_publish / sibling_subscribe /
        # sibling_peek / sibling_topics in the REPL namespace.
        if getattr(self, "_sibling_bus", None) is not None:
            fns = self._sibling_bus.make_repl_functions(
                sender_id=getattr(self, "_sibling_branch_id", None)
            )
            self.globals.update(fns)

        # Inter-process tools (parent_log, check_cancel) — conditional
        ipc_tools = build_interprocess_tools(self)
        self.globals.update(ipc_tools)

        # Evolution 6.2: Expose critic_fuzz as REPL tool
        self.globals["critic_fuzz"] = build_critic_fuzz_tool(self)

        # Evolution 6.3: Expose mcts_explore as REPL tool
        self.globals["mcts_explore"] = build_mcts_explore_tool(self)

    def _publish_timeline_event(self, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit("timeline_event", payload)
        except Exception:
            pass

    def is_cancel_requested(self) -> bool:
        return bool(self._cancel_event and self._cancel_event.is_set())

    def _final(self, value: Any) -> str:
        """Store the evaluated value of FINAL(expr) called from within REPL code.

        When the model writes ``FINAL(answer)`` inside a repl block, Python
        evaluates ``answer`` (the variable) before passing it here, so we
        receive the actual string value — not the literal name.  We persist
        it in ``_pending_final_value`` so that ``find_final_answer`` can
        retrieve it and terminate the loop with the correct content.
        """
        from rlm.plugins.channel_registry import sanitize_text_payload
        self._pending_final_value = sanitize_text_payload(str(value))
        return self._pending_final_value

    def get_pending_final(self) -> str | None:
        """Return and clear the stored FINAL() value set by REPL code execution."""
        val = self._pending_final_value
        self._pending_final_value = None
        return val

    def _final_var(self, variable_name: str) -> str:
        """Return the value of a variable as a final answer."""
        variable_name = variable_name.strip().strip("\"'")
        if variable_name in self.locals:
            return str(self.locals[variable_name])

        # Provide helpful error message with available variables
        available = [k for k in self.locals.keys() if not k.startswith("_")]
        if available:
            return (
                f"Error: Variable '{variable_name}' not found. "
                f"Available variables: {available}. "
                f"You must create and assign a variable BEFORE calling FINAL_VAR on it."
            )
        return (
            f"Error: Variable '{variable_name}' not found. "
            f"No variables have been created yet. "
            f"You must create and assign a variable in a REPL block BEFORE calling FINAL_VAR on it."
        )

    def _get_var(self, name: str) -> Any:
        """Safely retrieve a variable by name from the REPL namespace."""
        if name in self.locals:
            return self.locals[name]
        if name in self.globals:
            return self.globals[name]
        raise NameError(f"Variable '{name}' not found. Use SHOW_VARS() to see available variables.")

    def _show_vars(self) -> str:
        """Show all available variables in the REPL environment."""
        available = {k: type(v).__name__ for k, v in self.locals.items() if not k.startswith("_")}
        if not available:
            return "No variables created yet. Use ```repl``` blocks to create variables."
        return f"Available variables: {available}"

    def _llm_query(self, prompt: str, model: str | None = None) -> str:
        """Query the LM via socket connection to the handler.

        Args:
            prompt: The prompt to send to the LM.
            model: Optional model name to use (if handler has multiple clients).
                   Defaults to worker_model from route config.
        """
        effective_model = model or self._worker_model
        if not self.lm_handler_address:
            self.record_runtime_event(
                "llm_query.called",
                {"model": effective_model, "prompt_chars": len(prompt), "ok": False},
            )
            return "Error: No LM handler configured"

        try:
            request = LMRequest(prompt=prompt, model=effective_model, depth=self.depth)
            response = send_lm_request(self.lm_handler_address, request)

            if not response.success:
                self.record_runtime_event(
                    "llm_query.called",
                    {
                        "model": effective_model,
                        "prompt_chars": len(prompt),
                        "ok": False,
                        "error": response.error,
                    },
                )
                return f"Error: {response.error}"

            # Track this LLM call
            chat_completion = response.chat_completion
            if chat_completion is None:
                self.record_runtime_event(
                    "llm_query.called",
                    {
                        "model": effective_model,
                        "prompt_chars": len(prompt),
                        "ok": False,
                        "error": "missing chat_completion payload",
                    },
                )
                return "Error: LM response missing chat completion"

            self._pending_llm_calls.append(chat_completion)

            self.record_runtime_event(
                "llm_query.called",
                {
                    "model": effective_model or chat_completion.root_model,
                    "prompt_chars": len(prompt),
                    "ok": True,
                },
            )

            return chat_completion.response
        except Exception as e:
            self.record_runtime_event(
                "llm_query.called",
                {
                    "model": effective_model,
                    "prompt_chars": len(prompt),
                    "ok": False,
                    "error": str(e),
                },
            )
            return f"Error: LM query failed - {e}"

    def _llm_query_batched(self, prompts: list[str], model: str | None = None) -> list[str]:
        """Query the LM with multiple prompts concurrently.

        Args:
            prompts: List of prompts to send to the LM.
            model: Optional model name to use (if handler has multiple clients).
                   Falls back to ``self._worker_model`` when *None*.

        Returns:
            List of responses in the same order as input prompts.
        """
        effective_model = model or self._worker_model

        if not self.lm_handler_address:
            self.record_runtime_event(
                "llm_query_batched.called",
                {"model": effective_model, "prompt_count": len(prompts), "ok": False},
            )
            return ["Error: No LM handler configured"] * len(prompts)

        try:
            batched_prompts = cast(list[str | dict[str, Any]], list(prompts))
            responses = send_lm_request_batched(
                self.lm_handler_address, batched_prompts, model=effective_model, depth=self.depth
            )

            results = []
            for response in responses:
                if not response.success:
                    results.append(f"Error: {response.error}")
                else:
                    chat_completion = response.chat_completion
                    if chat_completion is None:
                        results.append("Error: missing chat_completion payload")
                        continue
                    # Track this LLM call in list of all calls -- we may want to do this hierarchically
                    self._pending_llm_calls.append(chat_completion)
                    results.append(chat_completion.response)

            self.record_runtime_event(
                "llm_query_batched.called",
                {
                    "model": effective_model,
                    "prompt_count": len(prompts),
                    "ok": all(not r.startswith("Error:") for r in results),
                },
            )

            return results
        except Exception as e:
            self.record_runtime_event(
                "llm_query_batched.called",
                {
                    "model": effective_model,
                    "prompt_count": len(prompts),
                    "ok": False,
                    "error": str(e),
                },
            )
            return [f"Error: LM query failed - {e}"] * len(prompts)

    def load_context(self, context_payload: dict | list | str):
        """Load context into the environment as context_0 (and 'context' alias).

        If context_payload is a directory path, activates codebase analysis mode:
        - Injects code analysis tools (list_files, read_file, search_code, etc.)
        - Generates an initial overview (stats + tree) as the context variable.
        """
        # Codebase mode: detect directory paths
        if isinstance(context_payload, str) and os.path.isdir(context_payload):
            self._load_codebase_context(context_payload)
        else:
            self.add_context(context_payload, 0)

    def _is_transient_turn_local(self, key: str, value: Any) -> bool:
        """Identify scratch locals that should not leak across turns."""
        if key in {"f", "fh", "file_handle", "handle"}:
            return True

        if isinstance(value, io.IOBase):
            return True

        transient_exact = {
            "answer",
            "answers",
            "final_answer",
            "prompt",
            "prompts",
            "response",
            "responses",
            "result",
            "results",
            "query",
            "queries",
            "output",
            "outputs",
        }
        if key in transient_exact:
            return True

        transient_prefixes = (
            "prompt_",
            "response_",
            "result_",
            "answer_",
            "output_",
            "query_",
            "tmp_",
            "temp_",
        )
        return key.startswith(transient_prefixes)

    def reset_turn_state(self) -> None:
        """Remove transient model-created locals while preserving tools and session scaffolding."""
        preserve_exact = {
            "context",
            "history",
            "reply",
            "reply_audio",
            "send_media",
            "skill_doc",
            "skill_list",
            "confirm_exec",
            "request_handoff",
            "handoff_roles",
        }
        preserve_exact.update(
            name
            for name, entry in self.custom_tools.items()
            if not callable(extract_tool_value(entry))
        )

        preserved: dict[str, Any] = {}
        for key, value in self.locals.items():
            if key in preserve_exact:
                preserved[key] = value
                continue
            if key.startswith(("context_", "history_", "__rlm")):
                preserved[key] = value
                continue
            if callable(value):
                preserved[key] = value
                continue
            if self._is_transient_turn_local(key, value):
                continue

            preserved[key] = value

        self.locals = preserved
        self._restore_scaffold()

    def _load_codebase_context(self, codebase_path: str):
        """
        Activate codebase analysis mode.

        Injects code analysis tools into the REPL and generates an initial
        overview as the context variable.
        """
        from rlm.tools.codebase import get_codebase_tools
        from rlm.utils.code_tools import directory_tree, file_stats
        from rlm.tools.memory import RLMMemory
        from rlm.tools.memory_tools import get_memory_tools
        from rlm.core.engine.runtime_workbench import AgentContext

        # Lacuna 1: Reutilizar memória compartilhada do pai quando disponível
        # P1: Shallow-copy para isolar agent_context sem criar novo db
        if self._parent_memory is not None:
            self._memory = copy.copy(self._parent_memory)
            self._memory._agent_context = AgentContext(
                depth=self.depth,
                branch_id=self._sibling_branch_id,
                parent_session_id=getattr(self._parent_memory, "session_id", None),
                role="child_parallel" if self._sibling_branch_id is not None else "child_serial",
                channel=self._originating_channel,
            )
        else:
            memory_dir = os.path.join(codebase_path, ".rlm_memory")
            self._memory = RLMMemory(memory_dir, scope_name=os.path.basename(os.path.abspath(codebase_path)))
            self._memory._agent_context = AgentContext(depth=self.depth, role="root", channel=self._originating_channel)

        # Inject sandboxed codebase tools into globals
        tools = get_codebase_tools(codebase_path)
        for name, func in tools.items():
            self.globals[name] = func
            
        # Inject memory tools
        memory_tools = get_memory_tools(self._memory, codebase_path, llm_query_batched_fn=self._llm_query_batched)
        for name, func in memory_tools.items():
            self.globals[name] = func

        # Generate initial overview as context
        stats = file_stats(codebase_path)
        tree = directory_tree(codebase_path, max_depth=2, show_files=False)
        overview = f"Codebase Analysis Mode\n{'=' * 40}\nProject: {codebase_path}\n\n{stats}\n\n{tree}"

        # Store as context_0 and 'context' alias
        self.locals["context"] = overview
        self.locals["context_0"] = overview
        self._context_count = 1

        # Flag codebase mode
        self._codebase_mode = True
        self._codebase_path = codebase_path
        self.record_runtime_event(
            "context.codebase_loaded",
            {"path": codebase_path},
        )

    def add_context(
        self, context_payload: dict | list | str, context_index: int | None = None
    ) -> int:
        """
        Add a context with versioned variable name.

        Args:
            context_payload: The context data to add
            context_index: Optional explicit index. If None, auto-increments.

        Returns:
            The context index used.
        """
        if context_index is None:
            context_index = self._context_count

        var_name = f"context_{context_index}"

        # Update count BEFORE execute_code calls so _restore_scaffold
        # knows which context is the latest during nested exec.
        self._context_count = max(self._context_count, context_index + 1)

        if isinstance(context_payload, str):
            context_path = os.path.join(self.temp_dir, f"context_{context_index}.txt")
            with open(context_path, "w", encoding="utf-8") as f:
                f.write(context_payload)
            self.execute_code(f"with open(r'{context_path}', 'r', encoding='utf-8') as f:\n    {var_name} = f.read()")
        else:
            context_path = os.path.join(self.temp_dir, f"context_{context_index}.json")
            with open(context_path, "w", encoding="utf-8") as f:
                json.dump(context_payload, f)
            self.execute_code(
                f"import json\nwith open(r'{context_path}', 'r', encoding='utf-8') as f:\n    {var_name} = json.load(f)"
            )

        # Always update 'context' to point to the latest context.
        self.execute_code(f"context = {var_name}")

        self.record_runtime_event(
            "context.added",
            {
                "context_index": context_index,
                "var_name": var_name,
                "context_type": type(context_payload).__name__,
            },
        )

        return context_index

    def update_handler_address(self, address: tuple[str, int]) -> None:
        """Update the LM handler address for a new completion call."""
        self.lm_handler_address = address

    def get_context_count(self) -> int:
        """Return the number of contexts loaded."""
        return self._context_count

    def add_history(
        self, message_history: list[dict[str, Any]], history_index: int | None = None
    ) -> int:
        """
        Store a conversation's message history as a versioned variable.

        Args:
            message_history: The list of message dicts from a completion call
            history_index: Optional explicit index. If None, auto-increments.

        Returns:
            The history index used.
        """
        if history_index is None:
            history_index = self._history_count

        var_name = f"history_{history_index}"

        # Store deep copy to avoid reference issues with nested dicts
        self.locals[var_name] = copy.deepcopy(message_history)

        # Alias latest history as 'repl_message_log' (primary) + 'history' (backward compat)
        self.locals["repl_message_log"] = self.locals[var_name]
        self.locals["history"] = self.locals[var_name]

        self._history_count = max(self._history_count, history_index + 1)
        self.record_runtime_event(
            "history.added",
            {
                "history_index": history_index,
                "message_count": len(message_history),
            },
        )
        return history_index

    def get_history_count(self) -> int:
        """Return the number of conversation histories stored."""
        return self._history_count

    @contextmanager
    def _capture_output(self):
        """Thread-safe context manager to capture stdout/stderr."""
        with self._lock:
            old_stdout, old_stderr = sys.stdout, sys.stderr
            stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
            try:
                sys.stdout, sys.stderr = stdout_buf, stderr_buf
                yield stdout_buf, stderr_buf
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr

    @contextmanager
    def _temp_cwd(self):
        """Temporarily change to temp directory for execution."""
        old_cwd = os.getcwd()
        try:
            os.chdir(self.temp_dir)
            yield
        finally:
            os.chdir(old_cwd)

    def _restore_scaffold(self) -> None:
        """Restore scaffold names after execution so overwrites don't persist.

        Protects against model code doing e.g. `context = "x"` or `llm_query = None`
        which would corrupt the REPL namespace across iterations.
        """
        self.globals["FINAL_VAR"] = self._final_var
        self.globals["FINAL"] = self._final
        self.globals["SHOW_VARS"] = self._show_vars
        self.globals["llm_query"] = self._llm_query
        self.globals["llm_query_batched"] = self._llm_query_batched
        # rlm_query / sub_rlm are injected externally by rlm.py; restore from
        # _rlm_scaffold_refs if they were registered (populated by rlm.py).
        for name, fn in getattr(self, "_rlm_scaffold_refs", {}).items():
            self.globals[name] = fn
        for name, fn in getattr(self, "_runtime_scaffold_refs", {}).items():
            self.globals[name] = fn
        # Restore context/history aliases — point to the LATEST context/history
        latest_ctx = f"context_{self._context_count - 1}" if self._context_count > 0 else "context_0"
        if latest_ctx in self.locals:
            self.locals["context"] = self.locals[latest_ctx]
        latest_hist = f"history_{self._history_count - 1}" if self._history_count > 0 else "history_0"
        if latest_hist in self.locals and not self.compaction:
            self.locals["repl_message_log"] = self.locals[latest_hist]
            self.locals["history"] = self.locals[latest_hist]  # backward compat
        elif self.compaction and hasattr(self, "_compaction_history"):
            self.locals["repl_message_log"] = self._compaction_history
            self.locals["history"] = self._compaction_history  # backward compat

    def execute_code(self, code: str) -> REPLResult:
        """Execute code in the persistent namespace and return result."""
        start_time = time.perf_counter()

        # Phase 9.3: Security Sandbox Audit (depth-aware per-instance auditor)
        from rlm.core.security import SecurityViolation
        try:
            self._auditor.audit_code(code)
        except SecurityViolation as e:
            self._repl_failure_count += 1
            self.record_runtime_event(
                "repl.error",
                {"error": f"SecurityAuditViolation: {e}"},
            )
            return REPLResult(
                stdout="",
                stderr=f"SecurityAuditViolation: {e}",
                locals=self.locals.copy(),
                execution_time=time.perf_counter() - start_time,
                rlm_calls=[]
            )

        # Clear pending LLM calls from previous execution
        self._pending_llm_calls = []
        self.record_runtime_event(
            "repl.started",
            {"code_chars": len(code)},
        )

        # Sanitize backslash line-continuation mistakes the model often makes:
        #   1) `code \ # comment`  → backslash BEFORE inline comment: SyntaxError
        #      Fix: replace `\ <spaces>#...` with just ` #...`
        #   2) `code \   ` (backslash + trailing spaces)  → SyntaxError
        #      Fix: strip trailing spaces (rstrip leaves `\` at end = valid continuation)
        #   3) comment line ending with `\` → unneeded line continuation in comment
        #      Fix: remove the trailing `\`
        sanitized_lines = []
        for line in code.split("\n"):
            # Pattern 1: backslash + spaces before # comment → remove the backslash
            line = re.sub(r'\\ +(#)', r' \1', line)
            # Pattern 2+3: strip trailing whitespace; if result ends with \
            #              and the line is a comment line, remove the trailing \
            stripped = line.rstrip()
            if stripped.endswith("\\") and '#' in stripped:
                stripped = stripped[:-1].rstrip()
            sanitized_lines.append(stripped)
        code = "\n".join(sanitized_lines)

        _hard_exception = False
        with self._capture_output() as (stdout_buf, stderr_buf), self._temp_cwd():
            try:
                combined = {**self.globals, **self.locals}
                exec(code, combined, combined)

                # Update locals with new variables
                for key, value in combined.items():
                    if key not in self.globals and not key.startswith("_"):
                        self.locals[key] = value

                # Restore scaffold — prevent model from permanently overwriting built-ins
                self._restore_scaffold()

                stdout = stdout_buf.getvalue()
                stderr = stderr_buf.getvalue()

                # Evolution 6.1: Reset failure counter on success
                if not stderr or all(line.strip() == "" for line in stderr.strip().splitlines()):
                    self._repl_failure_count = 0

            except Exception as e:
                _hard_exception = True
                stdout = stdout_buf.getvalue()
                stderr = stderr_buf.getvalue() + f"\n{type(e).__name__}: {e}"

                # Evolution 6.1: Increment failure counter on unhandled exception
                self._repl_failure_count += 1

        # Also count stderr with tracebacks as soft failures —
        # but only when no hard exception already incremented the counter.
        if not _hard_exception and stderr and ("Error" in stderr or "Traceback" in stderr or "Exception" in stderr):
            self._repl_failure_count += 1

        result = REPLResult(
            stdout=stdout,
            stderr=stderr,
            locals=self.locals.copy(),
            execution_time=time.perf_counter() - start_time,
            rlm_calls=self._pending_llm_calls.copy(),
        )
        self.record_runtime_event(
            "repl.executed" if not stderr else "repl.error",
            {
                "stdout_chars": len(stdout),
                "stderr_chars": len(stderr),
                "llm_call_count": len(result.rlm_calls),
                "execution_time": result.execution_time,
            },
        )
        return result

    def is_in_foraging_mode(self) -> bool:
        """True when consecutive REPL failures have crossed the foraging threshold."""
        return self._repl_failure_count >= self.foraging_threshold

    def reset_foraging(self) -> None:
        """Manually reset the foraging mode (e.g. after a successful hypothesis)."""
        self._repl_failure_count = 0

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False

    def extract_artifacts(self) -> dict[str, Any]:
        """Extrai variáveis REPL criadas durante a execução do agente.

        Retorna todos os locals não-privados do namespace REPL — incluindo
        callables (funções, lambdas) e valores (strings, dicts, listas, etc.)
        criados pelo agente durante sua execução.

        Usado por ``sub_rlm(..., return_artifacts=True)`` para colher artefatos
        computacionais do filho antes do seu cleanup, permitindo que o pai
        reutilize essas funções/dados em chamadas subsequentes.

        Filtra automaticamente:
        - Variáveis privadas (começam com ``_``)
        - Variáveis ``context_N`` / ``history_N`` (dados de entrada, não artefatos)

        Returns:
            Dict ``{nome: valor}`` de todos os locals não-privados.

        Exemplo de artefatos típicos:
            - ``parse_log``: função criada pelo filho para parsear um formato
            - ``schema``: dict com schema inferido de um CSV
            - ``model``: estimador sklearn treinado
            - ``normalized_data``: lista de dicts processados

        Como usar no pai (via SubRLMArtifactResult)::

            result = sub_rlm("Cria parse_log() p/ logs nginx", return_artifacts=True)
            # result.callables() → {"parse_log": <function>}
            # Reutiliza em todas as chamadas seguintes:
            rlm.custom_tools = result.as_custom_tools()
        """
        # Prefixes de vars de entrada que não são artefatos produzidos
        _input_prefixes = ("context_", "history_")
        return {
            k: v
            for k, v in self.locals.items()
            if not k.startswith("_")
            and not any(k.startswith(p) for p in _input_prefixes)
            and k not in ("context", "history")
        }

    def cleanup(self):
        """Clean up temp directory and reset state."""
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass
        if hasattr(self, "globals"):
            self.globals.clear()
        if hasattr(self, "locals"):
            self.locals.clear()

    def __del__(self):
        self.cleanup()
