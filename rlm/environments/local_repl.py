import copy
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any

from rlm.core.fast import LMRequest, send_lm_request, send_lm_request_batched
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


def _safe_import(name: str, *args: object, **kwargs: object) -> object:
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
    return __import__(name, *args, **kwargs)


def _safe_open(path: str, *args: object, **kwargs: object) -> object:
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
        # Pop sibling bus before passing kwargs to super (BaseEnv doesn't know it)
        self._sibling_bus = kwargs.pop("_sibling_bus", None)
        self._sibling_branch_id: int | None = kwargs.pop("_sibling_branch_id", None)
        # Pop parent log queue (set by sub_rlm_async to receive child progress)
        self._parent_log_queue = kwargs.pop("_parent_log_queue", None)
        # Pop cancel event (set by sub_rlm_async so parent can cancel this child)
        self._cancel_event: "threading.Event | None" = kwargs.pop("_cancel_event", None)
        # Lacuna 1: Aceitar memória compartilhada do pai
        self._parent_memory = kwargs.pop("_parent_memory", None)

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

        # Setup globals, locals, and modules in environment.
        self.setup()

        if compaction:
            self._compaction_history: list[Any] = []
            self.locals["history"] = self._compaction_history

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

        # Add helper functions
        self.globals["FINAL_VAR"] = self._final_var
        self.globals["SHOW_VARS"] = self._show_vars
        self.globals["llm_query"] = self._llm_query
        self.globals["llm_query_batched"] = self._llm_query_batched

        # Phase 9.3: Env var shield — safe access to os.environ from REPL
        # Usage in code: env_shield['DB_HOST'] or env_shield.get('REDIS_URL')
        # Sensitive key names (API_KEY, TOKEN, SECRET...) return '[REDACTED:NAME]'
        self.globals["env_shield"] = self._env_shield

        # Evolution 6.1: Expose reset_foraging as REPL tool
        self.globals["reset_foraging"] = self.reset_foraging

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
                return _ce.is_set()

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
        ) -> dict:
            """Run parallel MCTS exploration over candidate code approaches.

            Args:
                approaches: List of Python code strings (one per branch).
                context: Human-readable description of what the code tries to do.
                max_depth: Steps per branch before scoring (default 2).

            Returns:
                Dict with keys: winner_branch, winner_score, final_code,
                                seeded_vars (variables injected from winner into this REPL).

            Cost: 1 REPL execution per approach per depth step. No LLM calls.
            """
            from rlm.core.mcts import MCTSOrchestrator

            orchestrator = MCTSOrchestrator(
                lm_handler_address=self.lm_handler_address,
                max_depth=max_depth,
            )
            branch_code_blocks = [[code] for code in approaches]
            best = orchestrator.run(branch_code_blocks)

            # Seed winner's variables into the current REPL namespace
            seeded = []
            for k, v in best.repl_locals.items():
                if not k.startswith("_"):
                    self.locals[k] = v
                    seeded.append(k)

            return {
                "winner_branch": best.branch_id,
                "winner_score": best.total_score,
                "final_code": best.final_code,
                "seeded_vars": seeded,
                "all_branch_scores": {r.branch_id: r.total_score for r in [best]},
            }

        self.globals["mcts_explore"] = _mcts_explore

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
            return "Error: No LM handler configured"

        try:
            request = LMRequest(prompt=prompt, model=model, depth=self.depth)
            response = send_lm_request(self.lm_handler_address, request)

            if not response.success:
                return f"Error: {response.error}"

            # Track this LLM call
            self._pending_llm_calls.append(
                response.chat_completion,
            )

            return response.chat_completion.response
        except Exception as e:
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
            return ["Error: No LM handler configured"] * len(prompts)

        try:
            responses = send_lm_request_batched(
                self.lm_handler_address, prompts, model=model, depth=self.depth
            )

            results = []
            for response in responses:
                if not response.success:
                    results.append(f"Error: {response.error}")
                else:
                    # Track this LLM call in list of all calls -- we may want to do this hierarchically
                    self._pending_llm_calls.append(response.chat_completion)
                    results.append(response.chat_completion.response)

            return results
        except Exception as e:
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

        # Lacuna 1: Reutilizar memória compartilhada do pai quando disponível
        if self._parent_memory is not None:
            self._memory = self._parent_memory
        else:
            memory_dir = os.path.join(codebase_path, ".rlm_memory")
            self._memory = RLMMemory(memory_dir)

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
            with open(context_path, "w") as f:
                f.write(context_payload)
            self.execute_code(f"with open(r'{context_path}', 'r') as f:\n    {var_name} = f.read()")
        else:
            context_path = os.path.join(self.temp_dir, f"context_{context_index}.json")
            with open(context_path, "w") as f:
                json.dump(context_payload, f)
            self.execute_code(
                f"import json\nwith open(r'{context_path}', 'r') as f:\n    {var_name} = json.load(f)"
            )

        # Always update 'context' to point to the latest context.
        # In persistent mode, subsequent calls add context_1, context_2, etc.
        # The LLM always reads 'context' first, so it must reflect the current prompt.
        self.execute_code(f"context = {var_name}")

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

        # Alias history_0 as 'history' for convenience
        if history_index == 0:
            self.locals["history"] = self.locals[var_name]

        self._history_count = max(self._history_count, history_index + 1)
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
        self.globals["SHOW_VARS"] = self._show_vars
        self.globals["llm_query"] = self._llm_query
        self.globals["llm_query_batched"] = self._llm_query_batched
        # rlm_query / sub_rlm are injected externally by rlm.py; restore from
        # _rlm_scaffold_refs if they were registered (populated by rlm.py).
        for name, fn in getattr(self, "_rlm_scaffold_refs", {}).items():
            self.globals[name] = fn
        # Restore context/history aliases — point to the LATEST context/history
        latest_ctx = f"context_{self._context_count - 1}" if self._context_count > 0 else "context_0"
        if latest_ctx in self.locals:
            self.locals["context"] = self.locals[latest_ctx]
        if "history_0" in self.locals and not self.compaction:
            self.locals["history"] = self.locals["history_0"]
        elif self.compaction and hasattr(self, "_compaction_history"):
            self.locals["history"] = self._compaction_history

    def execute_code(self, code: str) -> REPLResult:
        """Execute code in the persistent namespace and return result."""
        start_time = time.perf_counter()

        # Phase 9.3: Security Sandbox Audit (depth-aware per-instance auditor)
        from rlm.core.security import SecurityViolation
        try:
            self._auditor.audit_code(code)
        except SecurityViolation as e:
            self._repl_failure_count += 1
            return REPLResult(
                stdout="",
                stderr=f"SecurityAuditViolation: {e}",
                locals=self.locals.copy(),
                execution_time=time.perf_counter() - start_time,
                rlm_calls=[]
            )

        # Clear pending LLM calls from previous execution
        self._pending_llm_calls = []

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

        return REPLResult(
            stdout=stdout,
            stderr=stderr,
            locals=self.locals.copy(),
            execution_time=time.perf_counter() - start_time,
            rlm_calls=self._pending_llm_calls.copy(),
        )

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

        # Re-inject codebase tools if checkpoint was in codebase mode
        codebase_path = state.get("codebase_path")
        if state.get("codebase_mode") and codebase_path and os.path.isdir(codebase_path):
            from rlm.tools.codebase import get_codebase_tools
            from rlm.tools.memory import RLMMemory
            from rlm.tools.memory_tools import get_memory_tools

            # Re-inject codebase tools
            tools = get_codebase_tools(codebase_path)
            for name, func in tools.items():
                self.globals[name] = func

            # Re-initialize memory
            memory_dir = os.path.join(codebase_path, ".rlm_memory")
            self._memory = RLMMemory(memory_dir)
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
