from __future__ import annotations

import re
from typing import Any


_CODE_BLOCK_PATTERN = re.compile(r"```repl\s*\r?\n(.*?)\r?\n```", re.DOTALL)
_FINAL_VAR_PATTERN = re.compile(r"^\s*FINAL_VAR\((.*?)\)", re.MULTILINE | re.DOTALL)
_FINAL_PATTERN = re.compile(r"^\s*FINAL\((.*)\)\s*$", re.MULTILINE | re.DOTALL)


def find_code_blocks(text: str) -> list[str]:
    """Find REPL code blocks in text wrapped in triple backticks."""
    return [match.group(1).strip() for match in _CODE_BLOCK_PATTERN.finditer(text)]


def _resolve_final_var(environment: Any, variable_name: str) -> str | None:
    execute_code = getattr(environment, "execute_code", None)
    if not callable(execute_code):
        return None

    result = execute_code(f"print(FINAL_VAR({variable_name!r}))")
    stdout = getattr(result, "stdout", "")
    final_answer = str(stdout).strip()
    if final_answer.startswith("Error:") or final_answer == "":
        return None
    return final_answer


def find_final_answer(text: str, environment: Any | None = None) -> str | None:
    """Find FINAL(...) or FINAL_VAR(...) and return the final answer string."""
    if environment is not None:
        get_pending = getattr(environment, "get_pending_final", None)
        if callable(get_pending):
            pending = get_pending()
            if isinstance(pending, str):
                return pending

    match = _FINAL_VAR_PATTERN.search(text)
    if match:
        variable_name = match.group(1).strip().strip('"').strip("'")
        if environment is None:
            return None
        return _resolve_final_var(environment, variable_name)

    match = _FINAL_PATTERN.search(text)
    if match:
        return match.group(1).strip()

    return None