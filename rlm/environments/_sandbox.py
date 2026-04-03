"""Security sandbox for REPL execution.

Provides sandboxed builtins, safe_import, and safe_open to restrict
code executed inside LocalREPL from accessing dangerous system resources.

Extracted from local_repl.py during responsibility separation refactoring.
"""

from __future__ import annotations

import os
from typing import Any


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
_SAFE_BUILTINS: dict[str, object] = {
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
    "Warning": Warning,
    # Blocked
    "input": None,
    "eval": None,
    "exec": None,
    "compile": None,
    "globals": None,
    "locals": None,
}
