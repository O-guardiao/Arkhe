"""
Language detection and metadata for multi-language code analysis.

Maps file extensions to language metadata (name, comment syntax, etc.)
Used by code_tools.py to provide language-aware analysis.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LanguageInfo:
    """Metadata about a programming language."""

    name: str
    family: str
    extensions: list[str] = field(default_factory=list)
    comment_single: str = "//"
    comment_multi_start: str | None = None
    comment_multi_end: str | None = None
    # Regex patterns for extracting structure (functions, classes, etc.)
    function_pattern: str | None = None
    class_pattern: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "family": self.family,
            "extensions": self.extensions,
            "comment_single": self.comment_single,
        }


# =============================================================================
# Language Registry
# =============================================================================

LANGUAGES: dict[str, LanguageInfo] = {}

def _register(lang: LanguageInfo) -> None:
    for ext in lang.extensions:
        LANGUAGES[ext] = lang


# --- JavaScript / TypeScript family ---
_register(LanguageInfo(
    name="TypeScript", family="javascript", extensions=[".ts", ".tsx"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"(?:export\s+)?(?:async\s+)?function\s+(\w+)",
    class_pattern=r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)",
))
_register(LanguageInfo(
    name="JavaScript", family="javascript", extensions=[".js", ".jsx", ".mjs", ".cjs"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"(?:export\s+)?(?:async\s+)?function\s+(\w+)",
    class_pattern=r"(?:export\s+)?class\s+(\w+)",
))

# --- Python ---
_register(LanguageInfo(
    name="Python", family="python", extensions=[".py", ".pyi", ".pyx"],
    comment_single="#", comment_multi_start='"""', comment_multi_end='"""',
    function_pattern=r"(?:async\s+)?def\s+(\w+)",
    class_pattern=r"class\s+(\w+)",
))

# --- Rust ---
_register(LanguageInfo(
    name="Rust", family="c-like", extensions=[".rs"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)",
    class_pattern=r"(?:pub\s+)?(?:struct|enum|trait|impl)\s+(\w+)",
))

# --- Go ---
_register(LanguageInfo(
    name="Go", family="c-like", extensions=[".go"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)",
    class_pattern=r"type\s+(\w+)\s+(?:struct|interface)",
))

# --- Java / Kotlin ---
_register(LanguageInfo(
    name="Java", family="jvm", extensions=[".java"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"(?:public|private|protected)?\s*(?:static\s+)?(?:\w+\s+)+(\w+)\s*\(",
    class_pattern=r"(?:public\s+)?(?:abstract\s+)?class\s+(\w+)",
))
_register(LanguageInfo(
    name="Kotlin", family="jvm", extensions=[".kt", ".kts"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"fun\s+(\w+)",
    class_pattern=r"(?:data\s+)?class\s+(\w+)",
))

# --- C / C++ ---
_register(LanguageInfo(
    name="C", family="c-like", extensions=[".c", ".h"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*\{",
    class_pattern=r"(?:struct|union|enum)\s+(\w+)",
))
_register(LanguageInfo(
    name="C++", family="c-like", extensions=[".cpp", ".hpp", ".cc", ".cxx", ".hxx"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*(?:const\s*)?\{",
    class_pattern=r"class\s+(\w+)",
))

# --- C# ---
_register(LanguageInfo(
    name="C#", family="dotnet", extensions=[".cs"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"(?:public|private|protected|internal)?\s*(?:static\s+)?(?:async\s+)?(?:\w+\s+)+(\w+)\s*\(",
    class_pattern=r"(?:public\s+)?(?:abstract\s+)?(?:partial\s+)?class\s+(\w+)",
))

# --- Swift ---
_register(LanguageInfo(
    name="Swift", family="apple", extensions=[".swift"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"func\s+(\w+)",
    class_pattern=r"(?:class|struct|enum|protocol)\s+(\w+)",
))

# --- Ruby ---
_register(LanguageInfo(
    name="Ruby", family="scripting", extensions=[".rb"],
    comment_single="#", comment_multi_start="=begin", comment_multi_end="=end",
    function_pattern=r"def\s+(\w+)",
    class_pattern=r"class\s+(\w+)",
))

# --- PHP ---
_register(LanguageInfo(
    name="PHP", family="scripting", extensions=[".php"],
    comment_single="//", comment_multi_start="/*", comment_multi_end="*/",
    function_pattern=r"function\s+(\w+)",
    class_pattern=r"class\s+(\w+)",
))

# --- Shell ---
_register(LanguageInfo(
    name="Shell", family="scripting", extensions=[".sh", ".bash", ".zsh"],
    comment_single="#",
    function_pattern=r"(?:function\s+)?(\w+)\s*\(\)",
))

# --- Data / Config ---
_register(LanguageInfo(name="JSON", family="data", extensions=[".json"], comment_single=""))
_register(LanguageInfo(name="YAML", family="data", extensions=[".yml", ".yaml"], comment_single="#"))
_register(LanguageInfo(name="TOML", family="data", extensions=[".toml"], comment_single="#"))
_register(LanguageInfo(name="XML", family="data", extensions=[".xml"], comment_single=""))
_register(LanguageInfo(name="Markdown", family="docs", extensions=[".md", ".mdx"], comment_single=""))
_register(LanguageInfo(name="HTML", family="web", extensions=[".html", ".htm"], comment_single=""))
_register(LanguageInfo(name="CSS", family="web", extensions=[".css"], comment_single="//"))
_register(LanguageInfo(name="SCSS", family="web", extensions=[".scss", ".sass"], comment_single="//"))
_register(LanguageInfo(name="SQL", family="data", extensions=[".sql"], comment_single="--"))


# =============================================================================
# Default ignore patterns (gitignore-style)
# =============================================================================

DEFAULT_IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "target", "bin", "obj",
    ".idea", ".vscode", ".vs", "coverage", ".pytest_cache",
    ".mypy_cache", ".tox", "egg-info", ".eggs",
}

DEFAULT_IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".o", ".obj", ".exe", ".dll", ".so", ".dylib",
    ".class", ".jar", ".war", ".ear", ".zip", ".tar", ".gz", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".bmp", ".webp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".flv",
    ".woff", ".woff2", ".ttf", ".eot",
    ".lock", ".min.js", ".min.css",
    ".map",
}


# =============================================================================
# Public API
# =============================================================================

def detect_language(filepath: str) -> LanguageInfo | None:
    """Detect the programming language of a file by its extension."""
    ext = Path(filepath).suffix.lower()
    return LANGUAGES.get(ext)


def detect_project_languages(directory: str) -> dict[str, int]:
    """
    Detect all languages used in a project directory.

    Returns:
        Dict mapping language name to file count, sorted by count descending.
    """
    counts: dict[str, int] = {}

    for root, dirs, files in os.walk(directory):
        # Skip ignored directories
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]

        for f in files:
            ext = Path(f).suffix.lower()
            if ext in DEFAULT_IGNORE_EXTENSIONS:
                continue
            lang = LANGUAGES.get(ext)
            if lang:
                counts[lang.name] = counts.get(lang.name, 0) + 1

    # Sort by count descending
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def get_gitignore_patterns(directory: str) -> list[str]:
    """Read .gitignore patterns from a directory if it exists."""
    gitignore_path = os.path.join(directory, ".gitignore")
    if not os.path.isfile(gitignore_path):
        return []

    patterns = []
    try:
        with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except Exception:
        pass
    return patterns


def should_ignore(path: str, name: str) -> bool:
    """Check if a file/directory should be ignored based on default rules."""
    if name in DEFAULT_IGNORE_DIRS:
        return True
    ext = Path(name).suffix.lower()
    if ext in DEFAULT_IGNORE_EXTENSIONS:
        return True
    return False
