"""
Security Sandbox Audit — Phase 9.3 (enhanced)

Inspired by OpenClaw's src/security/audit.ts.
Provides runtime static analysis (AST) and dynamic path checks to prevent
malicious code execution by the LLM (e.g., executing rm -rf, or deleting system files).

Enhancements over original:
- __import__() call bypass detection (AST-level)
- Extended blocked_modules: ctypes, cffi, mmap, winreg, gc, signal
- audit_input(): prompt injection / jailbreak detection on user input
- EnvVarShield: dict-like wrapper that redacts sensitive env vars in the REPL
- Depth-aware scanning: sub-RLMs (depth > 1) get stricter rules
"""

import ast
import os
import platform
import re
from dataclasses import dataclass, field
from typing import Any

from rlm.core.structured_log import get_logger

log = get_logger("security")


class SecurityViolation(Exception):
    """Raised when the REPL code violates sandbox constraints."""
    pass


@dataclass
class InputThreatReport:
    """Result of auditing user input for prompt injection."""
    is_suspicious: bool = False
    threat_level: str = "clean"         # clean | low | medium | high
    patterns_found: list[str] = field(default_factory=list)
    sanitized_text: str = ""


# ---------------------------------------------------------------------------
# Prompt Injection Patterns
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, threat_level, label)
    (r"ignore\s+(previous|all|above|prior)\s+instructions?", "high",   "instruction_override"),
    (r"forget\s+(everything|all|instructions?|context)",     "high",   "context_wipe"),
    (r"new\s+system\s+prompt\s*:",                           "high",   "system_prompt_injection"),
    (r"\[\s*system\s*\]",                                    "high",   "system_tag_injection"),
    (r"<\s*system\s*>",                                      "high",   "system_tag_html"),
    (r"you\s+are\s+now\s+(a|an|the|DAN)",                   "high",   "persona_override"),
    (r"do\s+anything\s+now",                                 "high",   "DAN_jailbreak"),
    (r"developer\s+mode\s+(enabled|on|activated)",           "high",   "devmode_jailbreak"),
    (r"jailbreak",                                           "medium", "jailbreak_keyword"),
    (r"(print|reveal|show|leak|output)\s+(your\s+)?(system\s+)?prompt", "medium", "prompt_leak_attempt"),
    (r"(print|reveal|show|output)\s+(your\s+)?(api\s*key|secret|token|password)", "high", "credential_extraction"),
    (r"__import__\s*\(",                                     "high",   "repl_bypass_attempt"),
    (r"os\.system\s*\(",                                     "high",   "os_call_in_input"),
    (r"subprocess\.",                                        "high",   "subprocess_in_input"),
    (r"\x00",                                                "high",   "null_byte_injection"),
    (r"base64\.b64decode",                                   "medium", "encoded_payload"),
    (r"eval\s*\(",                                           "medium", "eval_in_input"),
    (r"exec\s*\(",                                           "medium", "exec_in_input"),
    (r"<!--.*?hack.*?-->",                                   "low",    "html_comment_hack"),
    (r"(act as|pretend (you are|to be)|roleplay as)",        "low",    "roleplay_override"),
]

_COMPILED_PATTERNS = [
    (re.compile(p, re.IGNORECASE | re.DOTALL), level, label)
    for p, level, label in _INJECTION_PATTERNS
]


# ---------------------------------------------------------------------------
# EnvVarShield
# ---------------------------------------------------------------------------

class EnvVarShield:
    """
    Dict-like wrapper around os.environ that redacts sensitive env vars in the REPL.

    Injected as `env_shield` into the REPL globals. The LLM can read any non-sensitive
    env var freely. Access to sensitive keys (API_KEY, TOKEN, SECRET, etc.) returns
    '[REDACTED:KEY_NAME]' and logs a warning.

    Usage in REPL:
        import os
        # Direct os.environ still works (guarded by audit_code for sensitive patterns)
        # env_shield always gives safe access:
        db_host = env_shield['DB_HOST']      # returns real value
        api = env_shield['OPENAI_API_KEY']   # returns '[REDACTED:OPENAI_API_KEY]'
    """

    _SENSITIVE_FRAGMENTS = frozenset({
        "KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "PRIV",
        "CERT", "CREDENTIAL", "PRIVATE", "AUTH", "APIKEY",
        "BEARER", "WEBHOOK", "SIGNING",
    })

    def __init__(self):
        self._log = log

    def _is_sensitive(self, key: str) -> bool:
        key_upper = key.upper()
        return any(frag in key_upper for frag in self._SENSITIVE_FRAGMENTS)

    def __getitem__(self, key: str) -> str:
        if self._is_sensitive(key):
            self._log.warn(f"REPL tried to read sensitive env var '{key}' via env_shield — redacted.")
            return f"[REDACTED:{key}]"
        return os.environ[key]

    def get(self, key: str, default: Any = None) -> Any:
        if self._is_sensitive(key):
            self._log.warn(f"REPL tried to read sensitive env var '{key}' via env_shield — redacted.")
            return f"[REDACTED:{key}]"
        return os.environ.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in os.environ

    def keys(self):
        return os.environ.keys()

    def items(self):
        """Returns items with sensitive values redacted (safe for logging/display)."""
        for k, v in os.environ.items():
            yield k, f"[REDACTED:{k}]" if self._is_sensitive(k) else v

    def __repr__(self) -> str:
        redacted_count = sum(1 for k in os.environ if self._is_sensitive(k))
        return f"<EnvVarShield: {len(os.environ)} vars, {redacted_count} redacted>"


# ---------------------------------------------------------------------------
# REPLAuditor
# ---------------------------------------------------------------------------

class REPLAuditor:
    """
    Audits Python code execution and path accesses in the REPL.

    The security model is OBSERVATIONAL + BLOCKING for specific patterns,
    not restrictive (does not block general imports like numpy, pandas, etc.).

    What is blocked:
    - Import of truly dangerous stdlib modules (subprocess, ctypes, pty...)
    - __import__('blocked_module') call bypass
    - os.system / os.popen / shutil.rmtree direct calls
    - Access to sensitive filesystem paths (~/.ssh, ~/.aws, etc.)

    What is NOT blocked (intentional):
    - os import (monitored, specific methods blocked)
    - open() — file I/O is a legitimate RLM task
    - Network activity via SIF tools (shell(), web_search() etc.) — controlled interface
    - General Python stdlib (json, re, math, collections, itertools...)
    """

    def __init__(self, depth: int = 1):
        self.depth = depth          # Execution depth: 1=top-level, 2+=sub-RLM

        # Paths strictly forbidden for the LLM to access (read or write)
        home = os.path.expanduser("~")
        self.blocked_prefixes = [
            os.path.join(home, ".ssh"),
            os.path.join(home, ".aws"),
            os.path.join(home, ".docker"),
            os.path.join(home, ".config", "gcloud"),
        ]

        # Windows specifics
        if platform.system() == "Windows":
            self.blocked_prefixes.extend([
                "C:\\Windows\\System32",
                "C:\\Windows\\System",
                "C:\\ProgramData\\Microsoft",
            ])

        # Normalize blocked paths to absolute
        self.blocked_prefixes = [os.path.abspath(p).lower() for p in self.blocked_prefixes]

        # Modules strictly blocked from being imported
        # Note: 'os' is intentionally NOT here — we block specific os.* methods instead.
        # This allows 'import os; os.path.join(...)' which is legitimate.
        self.blocked_modules: set[str] = {
            "subprocess",
            "pty",
            "socket",
            "urllib",
            "requests",
            "http",
            "ctypes",       # native code execution
            "cffi",         # native code execution
            "mmap",         # memory mapping (potential exploit aid)
            "gc",           # garbage collector manipulation
            "signal",       # signal handlers (can kill/freeze process)
            "importlib",    # dynamic import bypass vector
            "winreg",       # Windows registry access
        }

        # Extra modules blocked only for sub-RLM calls (depth > 1)
        # Sub-RLMs are generated by the LLM itself, not directly by the user
        self._sub_rlm_extra_blocked: set[str] = {
            "threading",    # sub-agents shouldn't spawn threads
            "multiprocessing",
            "concurrent",
        }

    def audit_code(self, code: str) -> None:
        """
        Parses code via AST to catch malicious imports or system calls before execution.
        Does NOT execute the code — pure static analysis (safe).
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Let the actual exec() throw the SyntaxError
            return

        # At depth > 1, apply stricter module restrictions
        effective_blocked = self.blocked_modules.copy()
        if self.depth > 1:
            effective_blocked |= self._sub_rlm_extra_blocked

        for node in ast.walk(tree):
            # 1. Check Imports (import X / import X as Y)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_module = alias.name.split('.')[0]
                    if root_module in effective_blocked:
                        self._flag_violation(
                            f"Import of module '{root_module}' is blocked by Security Sandbox."
                        )

            # 2. Check from-imports (from X import Y)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_module = node.module.split('.')[0]
                    if root_module in effective_blocked:
                        self._flag_violation(
                            f"Import from '{root_module}' is blocked by Security Sandbox."
                        )

            # 3. Check dangerous Callables
            elif isinstance(node, ast.Call):
                # 3a. __import__('blocked_module') — bypass attempt
                # AST: Call(func=Name(id='__import__'), args=[Constant(value='...')])
                func = node.func
                if isinstance(func, ast.Name) and func.id == "__import__":
                    if node.args:
                        first_arg = node.args[0]
                        module_name = None
                        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                            module_name = first_arg.value.split('.')[0]
                        elif isinstance(first_arg, ast.Str):  # Python 3.7 compat
                            module_name = first_arg.s.split('.')[0]
                        if module_name and module_name in effective_blocked:
                            self._flag_violation(
                                f"__import__('{module_name}') is blocked. "
                                f"Use the provided SIF tools instead of bypassing the module restrictions."
                            )
                        elif module_name is None:
                            # Dynamic arg (variable, concatenation, f-string, etc.).
                            # Static imports are always preferred; __import__ with a
                            # non-literal arg is almost exclusively a sandbox escape attempt.
                            self._flag_violation(
                                "__import__() with a non-literal argument is blocked. "
                                "Use static 'import' statements instead."
                            )

                # 3b. os.system / os.popen / os.exec* / os.remove etc.
                elif isinstance(func, ast.Attribute):
                    obj_id = getattr(func.value, 'id', None)

                    if obj_id == 'os':
                        _BLOCKED_OS_METHODS = {
                            "system", "popen", "remove", "unlink", "rmdir",
                            "removedirs", "chmod", "chown", "kill", "execv",
                            "execve", "spawnl", "spawnle", "spawnlp",
                        }
                        if func.attr in _BLOCKED_OS_METHODS:
                            self._flag_violation(
                                f"os.{func.attr}() is blocked. "
                                f"Use shell() SIF tool for shell commands, or ask for specific RLM tools."
                            )

                    elif obj_id == 'shutil':
                        if func.attr in ("rmtree", "chown", "disk_usage"):
                            self._flag_violation(f"shutil.{func.attr}() is blocked.")

                    elif obj_id == 'importlib' and func.attr == 'import_module':
                        # importlib.import_module('subprocess') bypasses import blocking.
                        self._flag_violation(
                            "importlib.import_module() is blocked. "
                            "Use static 'import' statements instead."
                        )

    def audit_input(self, text: str, session_id: str = "") -> InputThreatReport:
        """
        Scans user-provided text for prompt injection / jailbreak patterns.

        This runs BEFORE the input reaches the LLM, on the raw user text.
        Does NOT block execution — returns a report so the caller decides.
        At threat_level='high', the gateway can choose to reject or sanitize.

        Args:
            text:       Raw user input string.
            session_id: Session ID for log correlation.

        Returns:
            InputThreatReport with is_suspicious, threat_level, patterns_found.
        """
        if not text:
            return InputThreatReport(sanitized_text=text)

        found: list[str] = []
        max_level = "clean"
        _level_order = {"clean": 0, "low": 1, "medium": 2, "high": 3}

        for compiled_re, level, label in _COMPILED_PATTERNS:
            if compiled_re.search(text):
                found.append(label)
                if _level_order[level] > _level_order[max_level]:
                    max_level = level

        if found:
            sid_tag = f"[session={session_id}] " if session_id else ""
            log.warn(
                f"{sid_tag}Input threat detected — level={max_level} "
                f"patterns={found}"
            )

        # Phase 9.3 (Gap A/B): Build sanitized version — strip HIGH+MEDIUM injection phrases
        # so callers (memory_manager, browser) can return safe text instead of blocking.
        sanitized = text
        if found:
            for compiled_re, level, label in _COMPILED_PATTERNS:
                if level in ("high", "medium"):
                    sanitized = compiled_re.sub(
                        f"[INJEÇÃO REMOVIDA:{label}]", sanitized
                    )

        return InputThreatReport(
            is_suspicious=bool(found),
            threat_level=max_level,
            patterns_found=found,
            sanitized_text=sanitized,
        )

    def check_path_access(self, path: str) -> None:
        """Called dynamically by file manipulation tools to block sensitive areas."""
        abs_path = os.path.abspath(path).lower()

        for blocked in self.blocked_prefixes:
            if abs_path.startswith(blocked):
                self._flag_violation(f"Path access denied to sensitive directory: {path}")

    def _flag_violation(self, message: str):
        log.error(f"Security Alert: {message}")
        raise SecurityViolation(message)


# ---------------------------------------------------------------------------
# Global singletons
# ---------------------------------------------------------------------------

auditor = REPLAuditor(depth=1)
env_var_shield = EnvVarShield()

