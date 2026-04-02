import copy
import hashlib
import io
import json
import os
import pathlib
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

# =============================================================================
# Safe Builtins
# =============================================================================

# Modules blocked at runtime inside REPL exec().
# Mirrors REPLAuditor.blocked_modules — the runtime guard catches dynamic
# bypass patterns the AST auditor cannot e.g.  __import__("subproc" + "ess").
_BLOCKED_RUNTIME_MODULES: frozenset[str] = frozenset({
    "subprocess", "pty", "socket", "urllib", "requests", "http",
    "ctypes", "cffi", "mmap", "gc", "signal", "importlib", "winreg",
})


def _safe_import(
    name: str,
    globals: dict[str, object] | None = None,
    locals: dict[str, object] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> object:
    """Runtime guard for __import__() inside REPL exec().

    Catches dynamic bypass attempts the AST auditor cannot block, such as
    ``__import__("subproc" + "ess")`` or ``__import__(var)``.
    """
    root = name.split(".")[0] if name else name
    if root in _BLOCKED_RUNTIME_MODULES:
        raise ImportError(
            f"Import of '{root}' is blocked by the RLM Security Sandbox. "
            "Use the provided SIF tools instead."
        )
    return __import__(name, globals, locals, fromlist, level)


def _safe_open(path: str, *args: Any, **kwargs: Any) -> object:
    """Sandboxed ``open()`` that enforces path restrictions before delegation.

    Uses :meth:`REPLAuditor.check_path_access` to block access to sensitive
    directories (``~/.ssh``, ``~/.aws``, ``C:\\Windows\\System32``, etc.)
    and the ``.env`` file in the working directory.
    """
    from rlm.core.security import auditor, SecurityViolation

    str_path = str(path)

    # Block .env files anywhere — they contain secrets
    basename = os.path.basename(str_path).lower()
    if basename == ".env" or basename.startswith(".env."):
        raise PermissionError(
            f"Access to '{basename}' is blocked by the RLM Security Sandbox. "
            "Environment files contain secrets and cannot be read from REPL."
        )

    # Delegate to the auditor's path checker
    auditor.check_path_access(str_path)

    return open(path, *args, **kwargs)


# Safe builtins - blocks dangerous operations like eval/exec/input
_SAFE_BUILTINS = {
    # Core types and functions
    "print": print,
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "bool": bool,
    "type": type,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "range": range,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "any": any,
    "all": all,
    "pow": pow,
    "divmod": divmod,
    "chr": chr,
    "ord": ord,
    "hex": hex,
    "bin": bin,
    "oct": oct,
    "repr": repr,
    "ascii": ascii,
    "format": format,
    "hash": hash,
    "id": id,
    "iter": iter,
    "next": next,
    "slice": slice,
    "callable": callable,
    "hasattr": hasattr,
    "getattr": getattr,
    "setattr": setattr,
    "delattr": delattr,
    "dir": dir,
    "vars": vars,
    "bytes": bytes,
    "bytearray": bytearray,
    "memoryview": memoryview,
    "complex": complex,
    "object": object,
    "super": super,
    "property": property,
    "staticmethod": staticmethod,
    "classmethod": classmethod,
    "__import__": _safe_import,
    "__build_class__": __build_class__,  # Required for class definitions in exec()
    "open": _safe_open,
    # Exceptions
    "Exception": Exception,
    "BaseException": BaseException,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "FileNotFoundError": FileNotFoundError,
    "OSError": OSError,
    "IOError": IOError,
    "RuntimeError": RuntimeError,
    "NameError": NameError,
    "ImportError": ImportError,
    "StopIteration": StopIteration,
    "AssertionError": AssertionError,
    "NotImplementedError": NotImplementedError,
    "ArithmeticError": ArithmeticError,
    "LookupError": LookupError,
    "PermissionError": PermissionError,
    "ZeroDivisionError": ZeroDivisionError,
    "ConnectionError": ConnectionError,
    "TimeoutError": TimeoutError,
    "AssertionError": AssertionError,  # Note: original MIT code spelling
    "AssertionError": AssertionError,  # Common typo alias
    "Warning": Warning,
    # Blocked
    "input": None,
    "eval": None,
    "exec": None,
    "compile": None,
    "globals": None,
    "locals": None,
}


class LocalREPL(NonIsolatedEnv):
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
        # Usage in code: env_shield['DB_HOST'] or env_shield.get('REDIS_URL')
        # Sensitive key names (API_KEY, TOKEN, SECRET...) return '[REDACTED:NAME]'
        self.globals["env_shield"] = self._env_shield

        # Evolution 6.1: Expose reset_foraging as REPL tool
        self.globals["reset_foraging"] = self.reset_foraging

        def task_create(
            title: str,
            parent: int | None = None,
            status: str = "not-started",
            note: str = "",
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            entry = self._task_ledger.create(
                title,
                parent_id=parent,
                status=status,
                note=note,
                metadata=metadata,
            )
            self.record_runtime_event(
                "task.created",
                {
                    "task_id": entry["task_id"],
                    "title": entry["title"],
                    "status": entry["status"],
                    "parent_id": entry["parent_id"],
                },
            )
            return entry

        def task_start(
            title: str,
            parent: int | None = None,
            note: str = "",
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            entry = self._task_ledger.start(title, parent_id=parent, note=note, metadata=metadata)
            self.record_runtime_event(
                "task.started",
                {
                    "task_id": entry["task_id"],
                    "title": entry["title"],
                    "parent_id": entry["parent_id"],
                },
            )
            return entry

        def task_update(
            task_id: int,
            status: str | None = None,
            note: str | None = None,
            title: str | None = None,
            metadata: dict[str, Any] | None = None,
            current: bool | None = None,
        ) -> dict[str, Any]:
            entry = self._task_ledger.update(
                task_id,
                status=status,
                note=note,
                title=title,
                metadata=metadata,
                current=current,
            )
            self.record_runtime_event(
                "task.updated",
                {
                    "task_id": entry["task_id"],
                    "status": entry["status"],
                    "current": self._task_ledger.current(),
                },
            )
            return entry

        def task_list(status: str | None = None) -> list[dict[str, Any]]:
            return self._task_ledger.list(status)

        def task_current() -> dict[str, Any] | None:
            return self._task_ledger.current()

        def task_set_current(task_id: int | None) -> dict[str, Any] | None:
            entry = self._task_ledger.set_current(task_id)
            self.record_runtime_event(
                "task.current_changed",
                {"task_id": task_id, "current": entry},
            )
            return entry

        def attach_text(
            label: str,
            content: str,
            kind: str = "text",
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            attachment = self._context_attachments.add_text(
                label,
                content,
                kind=kind,
                metadata=metadata,
            )
            self.record_runtime_event(
                "attachment.added",
                {
                    "attachment_id": attachment["attachment_id"],
                    "kind": attachment["kind"],
                    "label": attachment["label"],
                },
            )
            return attachment

        def attach_context(
            label: str,
            payload: Any,
            kind: str = "context",
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            attachment = self._context_attachments.add_context(
                label,
                payload,
                kind=kind,
                metadata=metadata,
            )
            self.record_runtime_event(
                "attachment.added",
                {
                    "attachment_id": attachment["attachment_id"],
                    "kind": attachment["kind"],
                    "label": attachment["label"],
                },
            )
            return attachment

        def attach_file(
            path: str,
            label: str | None = None,
            start_line: int | None = None,
            end_line: int | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            str_path = str(path)
            self._auditor.check_path_access(str_path)
            target = pathlib.Path(str_path)
            if not target.exists() or not target.is_file():
                raise FileNotFoundError(f"file not found: {target}")
            with target.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            s = max(1, int(start_line)) - 1 if start_line is not None else 0
            e = min(len(lines), int(end_line)) if end_line is not None else len(lines)
            content = "".join(lines[s:e])
            attachment = self._context_attachments.add_text(
                label or target.name,
                content,
                kind="file",
                metadata={
                    **dict(metadata or {}),
                    "start_line": s + 1,
                    "end_line": e,
                },
                source_ref=str(target),
            )
            self.record_runtime_event(
                "attachment.added",
                {
                    "attachment_id": attachment["attachment_id"],
                    "kind": attachment["kind"],
                    "label": attachment["label"],
                    "source_ref": attachment["source_ref"],
                },
            )
            return attachment

        def attachment_list(
            kind: str | None = None,
            pinned_only: bool | None = None,
        ) -> list[dict[str, Any]]:
            return self._context_attachments.list(
                kind=kind,
                pinned_only=pinned_only,
                include_content=False,
            )

        def attachment_get(attachment_id: str) -> dict[str, Any] | None:
            return self._context_attachments.get(attachment_id, include_content=True)

        def attachment_pin(attachment_id: str, pinned: bool = True) -> dict[str, Any]:
            attachment = self._context_attachments.pin(attachment_id, pinned=pinned)
            self.record_runtime_event(
                "attachment.pinned",
                {
                    "attachment_id": attachment_id,
                    "pinned": pinned,
                },
            )
            return attachment

        def timeline_recent(limit: int = 20, event_type: str | None = None) -> list[dict[str, Any]]:
            return self._execution_timeline.recent(limit=limit, event_type=event_type)

        def timeline_mark(
            event_type: str,
            data: dict[str, Any] | None = None,
            origin: str = "manual",
        ) -> dict[str, Any]:
            return self.record_runtime_event(event_type, data, origin=origin)

        def recursive_message(
            role: str,
            content: str,
            metadata: dict[str, Any] | None = None,
            branch_id: int | None = None,
        ) -> dict[str, Any]:
            return self.record_recursive_message(
                role,
                content,
                metadata=metadata,
                branch_id=branch_id,
            )

        def recursive_messages(
            limit: int = 20,
            role: str | None = None,
        ) -> list[dict[str, Any]]:
            return self.recent_recursive_messages(limit=limit, role=role)

        def recursive_event(
            event_type: str,
            payload: dict[str, Any] | None = None,
            branch_id: int | None = None,
            source: str = "runtime",
            visibility: str = "internal",
            correlation_id: str | None = None,
        ) -> dict[str, Any]:
            return self.emit_recursive_event(
                event_type,
                payload=payload,
                branch_id=branch_id,
                source=source,
                visibility=visibility,
                correlation_id=correlation_id,
            )

        def recursive_events(
            limit: int = 20,
            event_type: str | None = None,
            branch_id: int | None = None,
            source: str | None = None,
        ) -> list[dict[str, Any]]:
            return self.recent_recursive_events(
                limit=limit,
                event_type=event_type,
                branch_id=branch_id,
                source=source,
            )

        def recursive_command(
            command_type: str,
            payload: dict[str, Any] | None = None,
            status: str = "queued",
            branch_id: int | None = None,
        ) -> dict[str, Any]:
            return self.queue_recursive_command(
                command_type,
                payload=payload,
                status=status,
                branch_id=branch_id,
            )

        def recursive_command_update(
            command_id: int,
            status: str,
            outcome: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return self.update_recursive_command(
                command_id,
                status=status,
                outcome=outcome,
            )

        def recursive_commands(
            limit: int = 20,
            status: str | None = None,
        ) -> list[dict[str, Any]]:
            return self.recent_recursive_commands(limit=limit, status=status)

        def recursive_session_state() -> dict[str, Any]:
            return self.get_recursive_session_state()

        def active_recursive_strategy() -> dict[str, Any] | None:
            current = self.get_active_recursive_strategy()
            return dict(current) if current is not None else None

        self._runtime_scaffold_refs = {
            "task_create": task_create,
            "task_start": task_start,
            "task_update": task_update,
            "task_list": task_list,
            "task_current": task_current,
            "task_set_current": task_set_current,
            "attach_text": attach_text,
            "attach_context": attach_context,
            "attach_file": attach_file,
            "attachment_list": attachment_list,
            "attachment_get": attachment_get,
            "attachment_pin": attachment_pin,
            "timeline_recent": timeline_recent,
            "timeline_mark": timeline_mark,
            "recursive_message": recursive_message,
            "recursive_messages": recursive_messages,
            "recursive_event": recursive_event,
            "recursive_events": recursive_events,
            "recursive_command": recursive_command,
            "recursive_command_update": recursive_command_update,
            "recursive_commands": recursive_commands,
            "recursive_session_state": recursive_session_state,
            "active_recursive_strategy": active_recursive_strategy,
        }
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

        # Parent log queue: injected by sub_rlm_async() so the child can publish
        # progress messages that the parent reads via handle.log_poll().
        # The child calls parent_log("msg") in its REPL.
        if getattr(self, "_parent_log_queue", None) is not None:
            _pq = self._parent_log_queue

            def parent_log(msg: str) -> None:
                """Envia uma mensagem de progresso para o pai que iniciou este agente.

                Módulo fire-and-forget: nunca bloqueia. Se a fila estiver cheia,
                a mensagem é descartada silenciosamente (não interrompe a tarefa).

                Args:
                    msg: Texto de progresso (ex: 'processando linha 42 de 1000').

                Uso::
                    parent_log(f"Iniciando análise de {len(dados)} registros")
                    # ... trabalho ...
                    parent_log("Análise concluída, calculando KPIs")
                """
                try:
                    _pq.put_nowait(str(msg))
                except Exception:  # fila cheia ou fechada — não trava o filho
                    pass

            self.globals["parent_log"] = parent_log

        # Cancel event: injected by sub_rlm_async() so the child can check if
        # the parent requested cancellation. The child calls check_cancel() between
        # long steps — returns True when handle.cancel() has been called.
        if getattr(self, "_cancel_event", None) is not None:
            _ce = self._cancel_event

            def check_cancel() -> bool:
                """Verifica se o pai pediu cancelamento desta tarefa.

                Deve ser chamado periodicamente entre etapas longas.
                Retorna True quando o pai chamou ``handle.cancel()``.

                Uso recomendado::

                    if check_cancel():
                        parent_log("cancelado pelo pai, encerrando limpo")
                        FINAL_VAR("cancelado")
                """
                return bool(_ce and _ce.is_set())

            self.globals["check_cancel"] = check_cancel

        # Evolution 6.2: Expose critic_fuzz as REPL tool
        def _critic_fuzz(candidate_code: str, context: str = "", max_rounds: int = 3) -> dict:
            """Run adversarial fuzzing on candidate code.

            Args:
                candidate_code: Python code string to test.
                context: What the code is supposed to do.
                max_rounds: Max Adversary/Engineer rounds (default 3, controls cost).

            Returns:
                Dict with keys: winner, final_code, rounds, discovered_rules.
            """
            from rlm.tools.critic import run_critic_fuzzer
            import dataclasses

            memory_fn = self.globals.get("memory_analyze")
            report = run_critic_fuzzer(
                candidate_code=candidate_code,
                context=context,
                llm_query_fn=self._llm_query,
                execute_fn=self.execute_code,
                max_rounds=max_rounds,
                memory_analyze_fn=memory_fn,
            )
            # Return as plain dict for REPL readability
            return {
                "winner": report.winner,
                "final_code": report.final_code,
                "rounds": len(report.rounds),
                "discovered_rules": report.discovered_rules,
                "round_details": [dataclasses.asdict(r) for r in report.rounds],
            }

        self.globals["critic_fuzz"] = _critic_fuzz

        # Evolution 6.3: Expose mcts_explore as REPL tool
        def _mcts_explore(
            approaches: list[str],
            context: str = "",
            max_depth: int = 2,
            rounds: int = 1,
            archive_key: str | None = None,
            evaluators: list[str] | None = None,
            evaluator_weights: dict[str, float] | None = None,
            evaluator_thresholds: dict[str, float] | None = None,
        ) -> dict:
            """Run parallel MCTS exploration over candidate code approaches.

            Args:
                approaches: List of Python code strings (one per branch).
                context: Human-readable description of what the code tries to do.
                max_depth: Steps per branch before scoring (default 2).
                rounds: If > 1, refine candidates using elite feedback across rounds.
                archive_key: Optional stable key to reuse elites across calls.
                evaluators: Optional names of REPL callables used as problem-grounded evaluators.
                evaluator_weights: Optional per-evaluator weights.
                evaluator_thresholds: Optional per-evaluator prune thresholds.

            Returns:
                Dict with keys: winner_branch, winner_score, final_code,
                                seeded_vars (variables injected from winner into this REPL).

            Cost: 1 REPL execution per approach per depth step. No LLM calls.
            """
            from rlm.core.orchestration.mcts import EvaluationStage, MCTSOrchestrator, ProgramArchive, evolutionary_branch_search

            resolved_evaluators = list(evaluators or [])
            if not resolved_evaluators and callable(self.globals.get("evaluate")):
                resolved_evaluators = ["evaluate"]

            def _invoke_named_evaluator(name: str, snapshot: dict[str, Any]) -> float:
                fn = self.locals.get(name) or self.globals.get(name)
                if not callable(fn):
                    raise ValueError(f"Evaluator '{name}' was not found as a callable in the REPL")
                try:
                    value = fn(snapshot)
                except TypeError:
                    value = fn(
                        snapshot["code"],
                        snapshot["stdout"],
                        snapshot["stderr"],
                        snapshot["locals"],
                    )
                if isinstance(value, dict):
                    if name in value:
                        return float(value[name])
                    if len(value) == 1:
                        return float(next(iter(value.values())))
                    raise ValueError(
                        f"Evaluator '{name}' returned multiple metrics; provide a wrapper that returns one scalar"
                    )
                return float(cast(Any, value))

            stage_specs = []
            for evaluator_name in resolved_evaluators:
                stage_specs.append(
                    EvaluationStage(
                        name=evaluator_name,
                        evaluator=lambda snapshot, stage_name=evaluator_name: _invoke_named_evaluator(stage_name, snapshot),
                        weight=float((evaluator_weights or {}).get(evaluator_name, 1.0)),
                        min_score=(evaluator_thresholds or {}).get(evaluator_name),
                    )
                )

            orchestrator = MCTSOrchestrator(
                lm_handler_address=self.lm_handler_address,
                max_depth=max_depth,
                evaluation_stages=stage_specs,
            )
            archive_store = self._mcts_archives
            resolved_archive_key = archive_key or hashlib.sha1(
                (context or "|".join(approaches)).encode("utf-8", errors="ignore")
            ).hexdigest()[:16]
            archive = archive_store.setdefault(resolved_archive_key, ProgramArchive())

            def _replay_llm(_: str) -> str:
                return "\n---BRANCH---\n".join(approaches)

            if rounds > 1:
                search_result = evolutionary_branch_search(
                    context or "MCTS REPL exploration",
                    len(approaches),
                    _replay_llm,
                    orchestrator,
                    rounds=rounds,
                    elite_count=min(2, len(approaches)),
                    archive=archive,
                )
                best = search_result["best_branch"]
                history = search_result["history"]
            else:
                branch_code_blocks = [[code] for code in approaches]
                best = orchestrator.run(branch_code_blocks)
                archive.update(orchestrator.top_results(include_pruned=False))
                history = []

            # Seed winner's variables into the current REPL namespace
            seeded = []
            for k, v in best.repl_locals.items():
                if not k.startswith("_"):
                    self.locals[k] = v
                    seeded.append(k)

            self.record_runtime_event(
                "mcts.archive.updated",
                {
                    "archive_key": resolved_archive_key,
                    "archive_size": archive.size(),
                    "evaluators": resolved_evaluators,
                    "winner_branch": best.branch_id,
                    "winner_score": best.total_score,
                },
                origin="mcts",
            )

            return {
                "winner_branch": best.branch_id,
                "winner_score": best.total_score,
                "final_code": best.final_code,
                "seeded_vars": seeded,
                "all_branch_scores": {r.branch_id: r.total_score for r in orchestrator.top_results(include_pruned=True)},
                "history": history,
                "archive_key": resolved_archive_key,
                "archive_size": archive.size(),
                "evaluators": resolved_evaluators,
                "winner_metrics": best.aggregated_metrics,
            }

        self.globals["mcts_explore"] = _mcts_explore

    def _publish_timeline_event(self, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit("timeline_event", payload)
        except Exception:
            pass

    def record_runtime_event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        origin: str = "runtime",
    ) -> dict[str, Any]:
        return self._execution_timeline.record(event_type, data, origin=origin)

    def record_recursive_message(
        self,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        branch_id: int | None = None,
    ) -> dict[str, Any]:
        entry = self._recursive_session.add_message(
            role,
            content,
            metadata=metadata,
            branch_id=branch_id,
        )
        self.record_runtime_event(
            "recursive.message.recorded",
            {
                "message_id": entry["message_id"],
                "role": entry["role"],
                "branch_id": entry["branch_id"],
            },
            origin="recursive-session",
        )
        self.emit_recursive_event(
            "user_message_received" if entry["role"] == "user" else "assistant_message_emitted",
            payload={
                "message_id": entry["message_id"],
                "role": entry["role"],
                "metadata": dict(entry.get("metadata") or {}),
            },
            branch_id=entry["branch_id"],
            source=str((entry.get("metadata") or {}).get("source") or "runtime"),
            visibility="chat",
            correlation_id=f"msg:{entry['message_id']}",
        )
        return entry

    def recent_recursive_messages(
        self,
        *,
        limit: int = 20,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._recursive_session.recent_messages(limit=limit, role=role)

    def emit_recursive_event(
        self,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        branch_id: int | None = None,
        source: str = "runtime",
        visibility: str = "internal",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        entry = self._recursive_session.emit_event(
            event_type,
            payload=payload,
            branch_id=branch_id,
            source=source,
            visibility=visibility,
            correlation_id=correlation_id,
        )
        self.record_runtime_event(
            "recursive.event.emitted",
            {
                "event_id": entry["event_id"],
                "event_type": entry["event_type"],
                "branch_id": entry["branch_id"],
                "source": entry["source"],
            },
            origin="recursive-session",
        )
        return entry

    def recent_recursive_events(
        self,
        *,
        limit: int = 20,
        event_type: str | None = None,
        branch_id: int | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._recursive_session.recent_events(
            limit=limit,
            event_type=event_type,
            branch_id=branch_id,
            source=source,
        )

    def queue_recursive_command(
        self,
        command_type: str,
        *,
        payload: dict[str, Any] | None = None,
        status: str = "queued",
        branch_id: int | None = None,
    ) -> dict[str, Any]:
        entry = self._recursive_session.queue_command(
            command_type,
            payload=payload,
            status=status,
            branch_id=branch_id,
        )
        self.record_runtime_event(
            "recursive.command.queued",
            {
                "command_id": entry["command_id"],
                "command_type": entry["command_type"],
                "status": entry["status"],
                "branch_id": entry["branch_id"],
            },
            origin="recursive-session",
        )
        self.emit_recursive_event(
            "command_queued",
            payload={
                "command_id": entry["command_id"],
                "command_type": entry["command_type"],
                "status": entry["status"],
                "payload": dict(entry.get("payload") or {}),
            },
            branch_id=entry["branch_id"],
            source="control",
            visibility="control",
            correlation_id=f"cmd:{entry['command_id']}",
        )
        return entry

    def update_recursive_command(
        self,
        command_id: int,
        *,
        status: str,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = self._recursive_session.update_command(
            command_id,
            status=status,
            outcome=outcome,
        )
        self.record_runtime_event(
            "recursive.command.updated",
            {
                "command_id": entry["command_id"],
                "command_type": entry["command_type"],
                "status": entry["status"],
            },
            origin="recursive-session",
        )
        self.emit_recursive_event(
            "command_updated",
            payload={
                "command_id": entry["command_id"],
                "command_type": entry["command_type"],
                "status": entry["status"],
                "outcome": dict(entry.get("outcome") or {}),
            },
            branch_id=entry["branch_id"],
            source="control",
            visibility="control",
            correlation_id=f"cmd:{entry['command_id']}",
        )
        return entry

    def recent_recursive_commands(
        self,
        *,
        limit: int = 20,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._recursive_session.recent_commands(limit=limit, status=status)

    def get_recursive_session_state(self) -> dict[str, Any]:
        return self._recursive_session.state()

    def current_runtime_task(self) -> dict[str, Any] | None:
        return self._task_ledger.current()

    def current_runtime_task_id(self) -> int | None:
        current = self.current_runtime_task()
        return current.get("task_id") if current is not None else None

    def create_runtime_task(
        self,
        title: str,
        *,
        parent_task_id: int | None = None,
        status: str = "not-started",
        note: str = "",
        metadata: dict[str, Any] | None = None,
        current: bool = False,
    ) -> dict[str, Any]:
        return self._task_ledger.create(
            title,
            parent_id=parent_task_id,
            status=status,
            note=note,
            metadata=metadata,
            current=current,
        )

    def update_runtime_task(
        self,
        task_id: int,
        *,
        title: str | None = None,
        status: str | None = None,
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
        current: bool | None = None,
    ) -> dict[str, Any]:
        return self._task_ledger.update(
            task_id,
            title=title,
            status=status,
            note=note,
            metadata=metadata,
            current=current,
        )

    def register_subagent_task(
        self,
        *,
        mode: str,
        title: str,
        branch_id: int | None = None,
        parent_task_id: int | None = None,
        metadata: dict[str, Any] | None = None,
        current: bool = False,
    ) -> dict[str, Any]:
        resolved_parent = parent_task_id
        if resolved_parent is None:
            resolved_parent = self.current_runtime_task_id()
        task = self.create_runtime_task(
            title,
            parent_task_id=resolved_parent,
            status="in-progress",
            metadata=metadata,
            current=current,
        )
        if branch_id is not None:
            self._coordination_digest.bind_branch_task(
                int(branch_id),
                int(task["task_id"]),
                mode=mode,
                title=task["title"],
                parent_task_id=resolved_parent,
                status=task["status"],
                metadata=metadata,
            )
        return task

    def update_subagent_task(
        self,
        *,
        task_id: int,
        branch_id: int | None = None,
        status: str | None = None,
        note: str | None = None,
        metadata: dict[str, Any] | None = None,
        current: bool | None = None,
    ) -> dict[str, Any]:
        task = self.update_runtime_task(
            task_id,
            status=status,
            note=note,
            metadata=metadata,
            current=current,
        )
        if branch_id is not None:
            self._coordination_digest.update_branch_task(
                int(branch_id),
                status=task["status"],
                metadata=metadata,
            )
        return task

    def set_parallel_summary(
        self,
        *,
        winner_branch_id: int | None,
        cancelled_count: int,
        failed_count: int,
        total_tasks: int,
        strategy: dict[str, Any] | None = None,
        stop_evaluation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._coordination_digest.set_parallel_summary(
            winner_branch_id=winner_branch_id,
            cancelled_count=cancelled_count,
            failed_count=failed_count,
            total_tasks=total_tasks,
            strategy=strategy,
            stop_evaluation=stop_evaluation,
        )

    def set_active_recursive_strategy(
        self,
        strategy: dict[str, Any] | None,
        *,
        origin: str = "runtime",
    ) -> dict[str, Any] | None:
        if strategy is None:
            self._active_recursive_strategy = None
            self.record_runtime_event(
                "strategy.cleared",
                {"origin": origin},
                origin="strategy",
            )
            return None
        self._active_recursive_strategy = dict(strategy)
        self.record_runtime_event(
            "strategy.activated",
            {
                "origin": origin,
                "strategy_name": self._active_recursive_strategy.get("strategy_name")
                or self._active_recursive_strategy.get("name"),
                "coordination_policy": self._active_recursive_strategy.get("coordination_policy"),
                "stop_condition": self._active_recursive_strategy.get("stop_condition"),
                "repl_search_mode": self._active_recursive_strategy.get("repl_search_mode"),
            },
            origin="strategy",
        )
        return dict(self._active_recursive_strategy)

    def get_active_recursive_strategy(self) -> dict[str, Any] | None:
        if self._active_recursive_strategy is None:
            return None
        return dict(self._active_recursive_strategy)

    def clear_active_recursive_strategy(self, *, origin: str = "runtime") -> None:
        self.set_active_recursive_strategy(None, origin=origin)

    def get_runtime_control_state(self) -> dict[str, Any]:
        state = dict(self._runtime_control_state)
        state["branch_priorities"] = dict(state.get("branch_priorities") or {})
        return state

    def _publish_operator_control(
        self,
        signal_type: str,
        payload: dict[str, Any],
        *,
        sender_id: int | None = None,
    ) -> bool:
        publish_control = getattr(self._sibling_bus, "publish_control", None)
        if not callable(publish_control):
            return False
        topic = f"control/{signal_type}"
        publish_control(topic, dict(payload), sender_id=sender_id, signal_type=signal_type)
        return True

    def set_runtime_paused(
        self,
        paused: bool,
        *,
        reason: str = "",
        origin: str = "operator",
    ) -> dict[str, Any]:
        self._runtime_control_state["paused"] = bool(paused)
        self._runtime_control_state["pause_reason"] = str(reason or "")
        event_type = "runtime_paused" if paused else "runtime_resumed"
        self.record_runtime_event(
            f"control.{event_type}",
            {"reason": reason, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            event_type,
            payload={"reason": reason, "origin": origin},
            source="control",
            visibility="control",
        )
        if paused:
            self._publish_operator_control(
                "stop",
                {"reason": reason or "Paused by operator", "origin": origin, "action": "pause_runtime"},
            )
        return self.get_runtime_control_state()

    def set_runtime_focus(
        self,
        branch_id: int,
        *,
        fixed: bool = False,
        reason: str = "",
        origin: str = "operator",
    ) -> dict[str, Any]:
        normalized_branch = int(branch_id)
        self._runtime_control_state["focused_branch_id"] = normalized_branch
        if fixed:
            self._runtime_control_state["fixed_winner_branch_id"] = normalized_branch
        branch_tasks = self._coordination_digest.list_branch_tasks()
        for item in branch_tasks:
            self._coordination_digest.update_branch_task(
                int(item["branch_id"]),
                metadata={
                    "operator_focus": int(item["branch_id"]) == normalized_branch,
                    "operator_fixed_winner": fixed and int(item["branch_id"]) == normalized_branch,
                },
            )
        summary = self._coordination_digest.filtered_snapshot(limit=0).get("latest_parallel_summary") or {}
        self.set_parallel_summary(
            winner_branch_id=normalized_branch if fixed else summary.get("winner_branch_id"),
            cancelled_count=int(summary.get("cancelled_count", 0)),
            failed_count=int(summary.get("failed_count", 0)),
            total_tasks=int(summary.get("total_tasks", len(branch_tasks))),
            strategy=summary.get("strategy"),
            stop_evaluation=summary.get("stop_evaluation"),
        )
        action = "fix_winner_branch" if fixed else "focus_branch"
        self.record_runtime_event(
            f"control.{action}",
            {"branch_id": normalized_branch, "reason": reason, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            action,
            payload={"branch_id": normalized_branch, "reason": reason, "origin": origin},
            branch_id=normalized_branch,
            source="control",
            visibility="control",
        )
        self._publish_operator_control(
            "switch_strategy",
            {
                "action": action,
                "prioritized_branch_id": normalized_branch,
                "reason": reason,
                "origin": origin,
            },
            sender_id=normalized_branch if fixed else None,
        )
        if fixed:
            self._publish_operator_control(
                "stop",
                {"reason": reason or f"Winner branch fixed by operator: {normalized_branch}", "origin": origin, "action": action},
                sender_id=normalized_branch,
            )
        return self.get_runtime_control_state()

    def reprioritize_branch(
        self,
        branch_id: int,
        priority: int,
        *,
        reason: str = "",
        origin: str = "operator",
    ) -> dict[str, Any]:
        normalized_branch = int(branch_id)
        normalized_priority = int(priority)
        priorities = dict(self._runtime_control_state.get("branch_priorities") or {})
        priorities[str(normalized_branch)] = normalized_priority
        self._runtime_control_state["branch_priorities"] = priorities
        self._coordination_digest.update_branch_task(
            normalized_branch,
            metadata={"operator_priority": normalized_priority, "operator_priority_reason": reason},
        )
        strategy = self.get_active_recursive_strategy() or {}
        strategy["operator_branch_priorities"] = dict(priorities)
        self.set_active_recursive_strategy(strategy or None, origin=origin)
        self.record_runtime_event(
            "control.reprioritize_branch",
            {"branch_id": normalized_branch, "priority": normalized_priority, "reason": reason, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            "reprioritize_branch",
            payload={"branch_id": normalized_branch, "priority": normalized_priority, "reason": reason, "origin": origin},
            branch_id=normalized_branch,
            source="control",
            visibility="control",
        )
        self._publish_operator_control(
            "switch_strategy",
            {
                "action": "reprioritize_branch",
                "prioritized_branch_id": normalized_branch,
                "priority": normalized_priority,
                "reason": reason,
                "origin": origin,
                "branch_priorities": priorities,
            },
        )
        return self.get_runtime_control_state()

    def record_operator_note(self, note: str, *, branch_id: int | None = None, origin: str = "operator") -> dict[str, Any]:
        clean = str(note or "").strip()
        self._runtime_control_state["last_operator_note"] = clean
        self.record_runtime_event(
            "control.operator_note",
            {"note": clean, "branch_id": branch_id, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            "operator_note",
            payload={"note": clean, "origin": origin},
            branch_id=branch_id,
            source="control",
            visibility="control",
        )
        return self.get_runtime_control_state()

    def mark_runtime_checkpoint(self, checkpoint_path: str, *, origin: str = "operator") -> dict[str, Any]:
        self._runtime_control_state["last_checkpoint_path"] = str(checkpoint_path)
        self._runtime_control_state["last_checkpoint_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.record_runtime_event(
            "control.checkpoint_created",
            {"checkpoint_path": checkpoint_path, "origin": origin},
            origin="control",
        )
        self.emit_recursive_event(
            "checkpoint_created",
            payload={"checkpoint_path": checkpoint_path, "origin": origin},
            source="control",
            visibility="control",
        )
        return self.get_runtime_control_state()

    def get_runtime_state_snapshot(
        self,
        *,
        coordination_limit: int = 0,
        coordination_operation: str | None = None,
        coordination_topic: str | None = None,
        coordination_branch_id: int | None = None,
    ) -> dict[str, Any]:
        return {
            "tasks": {
                "current": self._task_ledger.current(),
                "items": self._task_ledger.list(),
            },
            "attachments": {
                "items": self._context_attachments.list(include_content=False),
            },
            "timeline": {
                "entries": self._execution_timeline.recent(limit=0),
            },
            "recursive_session": {
                "state": self.get_recursive_session_state(),
                "messages": self.recent_recursive_messages(limit=0),
                "commands": self.recent_recursive_commands(limit=0),
                "events": self.recent_recursive_events(limit=0),
            },
            "coordination": self._coordination_digest.filtered_snapshot(
                limit=coordination_limit,
                operation=coordination_operation,
                topic=coordination_topic,
                branch_id=coordination_branch_id,
            ),
            "controls": self.get_runtime_control_state(),
            "strategy": {
                "active_recursive_strategy": self.get_active_recursive_strategy(),
            },
        }

    def attach_sibling_bus(self, sibling_bus: Any, *, branch_id: int | None = None) -> None:
        self._sibling_bus = sibling_bus
        if branch_id is not None:
            self._sibling_branch_id = branch_id
        self._coordination_digest.attach(branch_id=self._sibling_branch_id)

        add_observer = getattr(sibling_bus, "add_observer", None)
        if callable(add_observer):
            add_observer(self._handle_sibling_bus_event)

        get_stats = getattr(sibling_bus, "get_stats", None)
        if callable(get_stats):
            try:
                stats = get_stats()
                self._coordination_digest.update_stats(
                    stats if isinstance(stats, dict) else None
                )
            except Exception:
                pass

    def _handle_sibling_bus_event(self, payload: dict[str, Any]) -> None:
        event = self._coordination_digest.record_event(
            payload.get("operation", "unknown"),
            topic=payload.get("topic", ""),
            sender_id=payload.get("sender_id"),
            receiver_id=payload.get("receiver_id"),
            payload=payload.get("payload"),
            metadata=payload.get("metadata"),
        )
        self._coordination_digest.update_stats(payload.get("stats"))
        self.record_runtime_event(
            "coordination.bus_event",
            {
                "operation": event["operation"],
                "topic": event["topic"],
                "sender_id": event["sender_id"],
                "receiver_id": event["receiver_id"],
            },
            origin="coordination",
        )

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
        """
        if not self.lm_handler_address:
            self.record_runtime_event(
                "llm_query.called",
                {"model": model, "prompt_chars": len(prompt), "ok": False},
            )
            return "Error: No LM handler configured"

        try:
            request = LMRequest(prompt=prompt, model=model, depth=self.depth)
            response = send_lm_request(self.lm_handler_address, request)

            if not response.success:
                self.record_runtime_event(
                    "llm_query.called",
                    {
                        "model": model,
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
                        "model": model,
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
                    "model": model or chat_completion.root_model,
                    "prompt_chars": len(prompt),
                    "ok": True,
                },
            )

            return chat_completion.response
        except Exception as e:
            self.record_runtime_event(
                "llm_query.called",
                {
                    "model": model,
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

        Returns:
            List of responses in the same order as input prompts.
        """
        if not self.lm_handler_address:
            self.record_runtime_event(
                "llm_query_batched.called",
                {"model": model, "prompt_count": len(prompts), "ok": False},
            )
            return ["Error: No LM handler configured"] * len(prompts)

        try:
            batched_prompts = cast(list[str | dict[str, Any]], list(prompts))
            responses = send_lm_request_batched(
                self.lm_handler_address, batched_prompts, model=model, depth=self.depth
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
                    "model": model,
                    "prompt_count": len(prompts),
                    "ok": all(not r.startswith("Error:") for r in results),
                },
            )

            return results
        except Exception as e:
            self.record_runtime_event(
                "llm_query_batched.called",
                {
                    "model": model,
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
        import copy
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
        # In persistent mode, subsequent calls add context_1, context_2, etc.
        # The LLM always reads 'context' first, so it must reflect the current prompt.
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
        import re as _re_sanitize
        _sanitized_lines = []
        for _line in code.split("\n"):
            # Pattern 1: backslash + spaces before # comment → remove the backslash
            # e.g. "x = 1 \ # note" → "x = 1  # note"
            _line = _re_sanitize.sub(r'\\ +(#)', r' \1', _line)
            # Pattern 2+3: strip trailing whitespace; if result ends with \
            #              and the line is a comment line, remove the trailing \
            _stripped = _line.rstrip()
            if _stripped.endswith("\\") and '#' in _stripped:
                _stripped = _stripped[:-1].rstrip()
            _sanitized_lines.append(_stripped)
        code = "\n".join(_sanitized_lines)

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
                stdout = stdout_buf.getvalue()
                stderr = stderr_buf.getvalue() + f"\n{type(e).__name__}: {e}"

                # Evolution 6.1: Increment failure counter on unhandled exception
                self._repl_failure_count += 1

        # Also count stderr with tracebacks as soft failures
        if stderr and ("Error" in stderr or "Traceback" in stderr or "Exception" in stderr):
            self._repl_failure_count += 1
        else:
            # Soft reset: if stderr is clean, only reset if not already incrementing
            pass

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
    # Evolution 3: State Checkpointing (Sleep/Wake)
    # =========================================================================

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
                # Try pickle -> base64 for complex types
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
