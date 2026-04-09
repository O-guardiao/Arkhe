"""
rlm.utils — General utility package for the RLM engine.

Modules
-------
languages    Language detection and project scanning; DEFAULT_IGNORE_* sets.
             No external dependencies — safe to import anywhere.
rlm_utils    filter_sensitive_keys() for scrubbing credentials from logged kwargs.
             No external dependencies — safe to import anywhere.
token_utils  Model context-limit table + token counting (tiktoken or char-estimate).
             Only optional tiktoken dependency — safe to import anywhere.
code_tools   Sandboxed codebase-analysis helpers (list_files, read_file, search_code,
             file_outline, file_stats, directory_tree, FileInfo, SearchResult).
             Imports from rlm.utils.languages — safe, but large; import lazily if
             you only need a subset.
parsing      REPL trajectory parsing (find_code_blocks, find_final_answer,
             format_iteration). Imports from rlm.core.types — do NOT import this
             module at module-level from inside rlm.core.* to avoid circular imports.
prompts      LLM system/user prompt constants and builders. Re-exports QueryMetadata
             from rlm.core.types. Same circular-import caution as parsing.

Circular-import strategy
------------------------
parsing and prompts both depend on rlm.core.types which in turn is imported by
rlm.core.engine.*. Eagerly importing those here would create a cycle. They are
therefore excluded from this __init__.py; callers should import them directly:

    from rlm.utils.parsing import find_code_blocks
    from rlm.utils.prompts import build_rlm_system_prompt
"""

# ── Leaf modules (no rlm.core dependency) ────────────────────────────────────

from rlm.utils.languages import (
    DEFAULT_IGNORE_DIRS,
    DEFAULT_IGNORE_EXTENSIONS,
    LANGUAGES,
    LanguageInfo,
    detect_language,
    detect_project_languages,
    should_ignore,
)

from rlm.utils.rlm_utils import filter_sensitive_keys

from rlm.utils.token_utils import (
    CHARS_PER_TOKEN_ESTIMATE,
    DEFAULT_CONTEXT_LIMIT,
    MODEL_CONTEXT_LIMITS,
    count_tokens,
    get_context_limit,
)

__all__ = [
    # languages
    "DEFAULT_IGNORE_DIRS",
    "DEFAULT_IGNORE_EXTENSIONS",
    "LANGUAGES",
    "LanguageInfo",
    "detect_language",
    "detect_project_languages",
    "should_ignore",
    # rlm_utils
    "filter_sensitive_keys",
    # token_utils
    "CHARS_PER_TOKEN_ESTIMATE",
    "DEFAULT_CONTEXT_LIMIT",
    "MODEL_CONTEXT_LIMITS",
    "count_tokens",
    "get_context_limit",
]
