"""
Codebase tools for REPL injection.

Wraps code_tools functions with path sandboxing so the LLM
can only access files within the designated project directory.
"""

from __future__ import annotations

import os
from typing import Any

from rlm.utils.code_tools import (
    FileInfo,
    SearchResult,
    directory_tree,
    file_outline,
    file_stats,
    list_files,
    read_file,
    search_code,
)


def get_codebase_tools(base_path: str) -> dict[str, Any]:
    """
    Create a set of codebase analysis tools scoped to base_path.

    All path arguments are resolved relative to base_path for safety —
    the LLM cannot escape the project directory.

    Args:
        base_path: Absolute path to the project root.

    Returns:
        Dict of tool name -> callable, ready for REPL namespace injection.
    """
    base_path = os.path.abspath(base_path)

    def _resolve(relative: str) -> str:
        """Resolve a relative path within the sandbox."""
        resolved = os.path.abspath(os.path.join(base_path, relative))
        
        # Phase 9.3 Security Sandbox Audit
        from rlm.core.security import auditor, SecurityViolation
        try:
            auditor.check_path_access(resolved)
        except SecurityViolation as e:
            raise PermissionError(str(e))
            
        # Security: ensure we stay within base_path
        if not resolved.startswith(base_path):
            raise PermissionError(
                f"Access denied: '{relative}' resolves outside project root."
            )
        return resolved

    def tool_list_files(
        directory: str = ".",
        extensions: list[str] | None = None,
        max_depth: int | None = None,
        max_results: int = 500,
    ) -> list[FileInfo]:
        """List files in a directory. Paths are relative to project root."""
        return list_files(
            _resolve(directory),
            extensions=extensions,
            max_depth=max_depth,
            max_results=max_results,
        )

    def tool_read_file(
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Read file contents with optional line range. Path is relative to project root."""
        return read_file(_resolve(path), start_line=start_line, end_line=end_line)

    def tool_search_code(
        pattern: str,
        directory: str = ".",
        extensions: list[str] | None = None,
        case_insensitive: bool = False,
        max_results: int = 100,
    ) -> list[SearchResult]:
        """Search for a pattern across files. Directory is relative to project root."""
        return search_code(
            pattern,
            _resolve(directory),
            extensions=extensions,
            case_insensitive=case_insensitive,
            max_results=max_results,
        )

    def tool_file_outline(path: str) -> str:
        """Get function/class outline of a file. Path is relative to project root."""
        return file_outline(_resolve(path))

    def tool_file_stats(directory: str = ".") -> str:
        """Get codebase statistics. Directory is relative to project root."""
        return file_stats(_resolve(directory))

    def tool_directory_tree(
        directory: str = ".",
        max_depth: int = 3,
        show_files: bool = True,
    ) -> str:
        """Get ASCII directory tree. Directory is relative to project root."""
        return directory_tree(
            _resolve(directory),
            max_depth=max_depth,
            show_files=show_files,
        )

    return {
        "list_files": tool_list_files,
        "read_file": tool_read_file,
        "search_code": tool_search_code,
        "file_outline": tool_file_outline,
        "file_stats": tool_file_stats,
        "directory_tree": tool_directory_tree,
    }
