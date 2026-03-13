"""
Code analysis tools for the RLM REPL environment.

Provides file listing, reading, searching, and outlining functions
that can be injected into the REPL namespace for codebase analysis.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from rlm.utils.languages import (
    DEFAULT_IGNORE_DIRS,
    DEFAULT_IGNORE_EXTENSIONS,
    LanguageInfo,
    detect_language,
    detect_project_languages,
    should_ignore,
)


# =============================================================================
# Data Types
# =============================================================================

@dataclass
class FileInfo:
    """Information about a single file."""

    path: str
    name: str
    extension: str
    size_bytes: int
    line_count: int
    language: str | None = None

    def __str__(self) -> str:
        lang = self.language or "unknown"
        return f"{self.path} ({lang}, {self.line_count} lines, {self.size_bytes}B)"


@dataclass
class SearchResult:
    """A single search match within a file."""

    file: str
    line_number: int
    line_content: str
    match: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line_number}: {self.line_content.strip()}"


# =============================================================================
# Core Tools
# =============================================================================

def list_files(
    directory: str,
    extensions: list[str] | None = None,
    ignore_patterns: list[str] | None = None,
    max_depth: int | None = None,
    max_results: int = 500,
) -> list[FileInfo]:
    """
    List files in a directory with optional filtering.

    Args:
        directory: Root directory to search.
        extensions: Optional list of extensions to include (e.g. [".ts", ".py"]).
        ignore_patterns: Additional directory names to ignore.
        max_depth: Maximum directory depth to traverse.
        max_results: Maximum number of results to return.

    Returns:
        List of FileInfo objects sorted by path.
    """
    results: list[FileInfo] = []
    base_depth = directory.rstrip(os.sep).count(os.sep)
    extra_ignore = set(ignore_patterns) if ignore_patterns else set()

    for root, dirs, files in os.walk(directory):
        # Check depth
        current_depth = root.count(os.sep) - base_depth
        if max_depth is not None and current_depth >= max_depth:
            dirs.clear()
            continue

        # Filter directories
        dirs[:] = sorted([
            d for d in dirs
            if d not in DEFAULT_IGNORE_DIRS and d not in extra_ignore
        ])

        for f in sorted(files):
            if len(results) >= max_results:
                return results

            ext = Path(f).suffix.lower()
            if ext in DEFAULT_IGNORE_EXTENSIONS:
                continue
            if extensions and ext not in extensions:
                continue

            filepath = os.path.join(root, f)
            rel_path = os.path.relpath(filepath, directory)

            try:
                size = os.path.getsize(filepath)
                line_count = _count_lines(filepath)
                lang_info = detect_language(filepath)
                lang_name = lang_info.name if lang_info else None
            except (OSError, PermissionError):
                continue

            results.append(FileInfo(
                path=rel_path.replace("\\", "/"),
                name=f,
                extension=ext,
                size_bytes=size,
                line_count=line_count,
                language=lang_name,
            ))

    return results


def read_file(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int = 100_000,
) -> str:
    """
    Read file contents, optionally limited to a line range.

    Args:
        path: Path to the file.
        start_line: 1-indexed start line (inclusive).
        end_line: 1-indexed end line (inclusive).
        max_chars: Maximum characters to return (default 100K).

    Returns:
        File contents as a string with line numbers.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, PermissionError) as e:
        return f"Error reading {path}: {e}"

    total_lines = len(lines)

    if start_line is not None:
        start_line = max(1, start_line) - 1  # Convert to 0-indexed
    else:
        start_line = 0

    if end_line is not None:
        end_line = min(end_line, total_lines)
    else:
        end_line = total_lines

    selected = lines[start_line:end_line]

    # Build output with line numbers
    output_parts = []
    char_count = 0
    for i, line in enumerate(selected, start=start_line + 1):
        numbered_line = f"{i:>5}: {line.rstrip()}"
        char_count += len(numbered_line) + 1
        if char_count > max_chars:
            output_parts.append(f"  ... [truncated at {max_chars} chars, {total_lines - i} lines remaining]")
            break
        output_parts.append(numbered_line)

    header = f"[{path}] ({total_lines} lines total, showing {start_line + 1}-{end_line})"
    return header + "\n" + "\n".join(output_parts)


def search_code(
    pattern: str,
    directory: str,
    extensions: list[str] | None = None,
    case_insensitive: bool = False,
    max_results: int = 100,
) -> list[SearchResult]:
    """
    Search for a pattern across files in a directory.

    Args:
        pattern: Regex pattern or literal string to search for.
        directory: Root directory to search.
        extensions: Optional list of extensions to filter.
        case_insensitive: Whether to ignore case.
        max_results: Maximum number of results.

    Returns:
        List of SearchResult objects.
    """
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error:
        # Fall back to literal search if regex is invalid
        regex = re.compile(re.escape(pattern), flags)

    results: list[SearchResult] = []

    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]

        for f in sorted(files):
            if len(results) >= max_results:
                return results

            ext = Path(f).suffix.lower()
            if ext in DEFAULT_IGNORE_EXTENSIONS:
                continue
            if extensions and ext not in extensions:
                continue

            filepath = os.path.join(root, f)
            rel_path = os.path.relpath(filepath, directory).replace("\\", "/")

            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                    for line_num, line in enumerate(fh, 1):
                        match = regex.search(line)
                        if match:
                            results.append(SearchResult(
                                file=rel_path,
                                line_number=line_num,
                                line_content=line.rstrip()[:200],
                                match=match.group(0),
                            ))
                            if len(results) >= max_results:
                                return results
            except (OSError, PermissionError, UnicodeDecodeError):
                continue

    return results


def file_outline(path: str) -> str:
    """
    Extract function and class definitions from a file.

    Uses regex-based extraction based on the file's language.
    Returns a formatted outline string.
    """
    lang = detect_language(path)
    if not lang:
        return f"[{path}] Unknown language — cannot extract outline."

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            lines = content.split("\n")
    except (OSError, PermissionError) as e:
        return f"Error reading {path}: {e}"

    items: list[str] = []

    if lang.class_pattern:
        for i, line in enumerate(lines, 1):
            match = re.search(lang.class_pattern, line)
            if match:
                items.append(f"  L{i:>4} [class]    {match.group(1)}")

    if lang.function_pattern:
        for i, line in enumerate(lines, 1):
            # Skip lines inside comments
            stripped = line.lstrip()
            if stripped.startswith(lang.comment_single) if lang.comment_single else False:
                continue
            match = re.search(lang.function_pattern, line)
            if match:
                name = match.group(1)
                # Skip common false positives
                if name in ("if", "for", "while", "switch", "catch", "return", "else"):
                    continue
                items.append(f"  L{i:>4} [function] {name}")

    total_lines = len(lines)
    header = f"[{path}] ({lang.name}, {total_lines} lines)"

    if not items:
        return header + "\n  No functions or classes found."

    return header + "\n" + "\n".join(sorted(items, key=lambda x: int(x.split("L")[1].split()[0])))


def file_stats(directory: str) -> str:
    """
    Generate statistics about a codebase.

    Returns a formatted string with file counts, line counts, and language breakdown.
    """
    total_files = 0
    total_lines = 0
    total_bytes = 0
    lang_files: dict[str, int] = {}
    lang_lines: dict[str, int] = {}

    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]

        for f in files:
            ext = Path(f).suffix.lower()
            if ext in DEFAULT_IGNORE_EXTENSIONS:
                continue

            filepath = os.path.join(root, f)
            try:
                size = os.path.getsize(filepath)
                lines = _count_lines(filepath)
            except (OSError, PermissionError):
                continue

            total_files += 1
            total_lines += lines
            total_bytes += size

            lang = detect_language(filepath)
            name = lang.name if lang else "Other"
            lang_files[name] = lang_files.get(name, 0) + 1
            lang_lines[name] = lang_lines.get(name, 0) + lines

    # Sort by line count
    sorted_langs = sorted(lang_lines.items(), key=lambda x: x[1], reverse=True)

    output = [
        f"📊 Codebase Statistics for: {directory}",
        f"   Total files: {total_files:,}",
        f"   Total lines: {total_lines:,}",
        f"   Total size:  {_human_size(total_bytes)}",
        "",
        "   Language Breakdown:",
    ]

    for lang_name, lines in sorted_langs[:15]:
        files_count = lang_files.get(lang_name, 0)
        pct = (lines / total_lines * 100) if total_lines > 0 else 0
        output.append(f"   {lang_name:>15}: {files_count:>5} files, {lines:>8,} lines ({pct:>5.1f}%)")

    if len(sorted_langs) > 15:
        output.append(f"   ... and {len(sorted_langs) - 15} more languages")

    return "\n".join(output)


def directory_tree(
    directory: str,
    max_depth: int = 3,
    show_files: bool = True,
    max_items: int = 200,
) -> str:
    """
    Generate an ASCII directory tree.

    Args:
        directory: Root directory.
        max_depth: Maximum depth to traverse.
        show_files: Whether to show individual files.
        max_items: Maximum number of items to display.

    Returns:
        ASCII tree representation.
    """
    lines: list[str] = []
    base_name = os.path.basename(directory.rstrip(os.sep)) or directory
    lines.append(f"📁 {base_name}/")
    _item_count = [0]

    def _walk(dir_path: str, prefix: str, depth: int) -> None:
        if depth >= max_depth or _item_count[0] >= max_items:
            return

        try:
            entries = sorted(os.listdir(dir_path))
        except (OSError, PermissionError):
            return

        # Separate dirs and files
        dirs = [e for e in entries if os.path.isdir(os.path.join(dir_path, e)) and e not in DEFAULT_IGNORE_DIRS]
        files = [e for e in entries if os.path.isfile(os.path.join(dir_path, e)) and not should_ignore(dir_path, e)]

        # Show directories first
        all_items = [(d, True) for d in dirs] + ([(f, False) for f in files] if show_files else [])

        for i, (name, is_dir) in enumerate(all_items):
            if _item_count[0] >= max_items:
                lines.append(f"{prefix}└── ... [{len(all_items) - i} more items]")
                return

            is_last = (i == len(all_items) - 1)
            connector = "└── " if is_last else "├── "
            extension = "    " if is_last else "│   "

            if is_dir:
                sub_path = os.path.join(dir_path, name)
                try:
                    child_count = sum(1 for _ in os.listdir(sub_path) if not should_ignore(sub_path, _))
                except (OSError, PermissionError):
                    child_count = 0
                lines.append(f"{prefix}{connector}📁 {name}/ ({child_count} items)")
                _item_count[0] += 1
                _walk(sub_path, prefix + extension, depth + 1)
            else:
                lang = detect_language(name)
                icon = "📄" if not lang else "💻"
                lines.append(f"{prefix}{connector}{icon} {name}")
                _item_count[0] += 1

    _walk(directory, "", 0)
    return "\n".join(lines)


# =============================================================================
# Private Helpers
# =============================================================================

def _count_lines(filepath: str) -> int:
    """Count lines in a file efficiently."""
    try:
        with open(filepath, "rb") as f:
            return sum(1 for _ in f)
    except (OSError, PermissionError):
        return 0


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
