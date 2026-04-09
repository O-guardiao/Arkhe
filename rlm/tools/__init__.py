# RLM Tools — Pluggable tool sets for REPL environments.
#
# Each module exposes a factory that returns a flat dict[str, callable].
# Consumers inject the dict into the REPL/agent namespace with:
#
#   for name, fn in get_X_tools(...).items():
#       repl.globals[name] = fn
#
# Public API
# ----------
#
# Memory (workspace-scoped, 2-layer: raw + knowledge graph):
#   RLMMemory                     — core class (memory.py)
#   get_memory_tools(memory, base_path, llm_query_batched_fn?)
#       → memory_store, memory_read, memory_chunk_and_store,
#         memory_reassemble, memory_analyze, memory_link,
#         memory_list, memory_search, memory_status,
#         memory_batch_analyze, memory_batch_chunk_and_store,
#         memory_semantic_search   (alias → hybrid search)
#
# Session memory (conversational, MultiVectorMemory-backed):
#   get_session_memory_tools(rlm_session)
#       → session_memory_search, session_memory_status, session_memory_recent
#
# Knowledge base (cross-session, global):
#   get_kb_tools(rlm_session)
#       → kb_search, kb_get_full_context, kb_status
#
# Vault / Obsidian bridge:
#   get_vault_tools(rlm_session)   — returns {} when no bridge is configured
#       → vault_search, vault_read, vault_check_corrections, vault_moc
#
# Codebase analysis (sandboxed file operations):
#   get_codebase_tools(base_path)
#       → list_files, read_file, search_code,
#         file_outline, file_stats, directory_tree
#
# Agent introspection / self-awareness:
#   get_introspection_tools(rlm_session)
#       → rlm_introspect, sif_usage, prompt_overview
#
# Adversarial critic / fuzzing (not REPL-injectable directly):
#   run_critic_fuzzer(candidate_code, context, llm_query_fn, execute_fn, ...)
#   get_critic_tools(llm_query_fn, execute_fn, ...)   — REPL-injectable wrapper
#   FuzzRound, CriticReport                           — result dataclasses
#
# Orphaned / internal (do not import directly):
#   embeddings.py → EmbeddingIndex  — superseded by MultiVectorMemory hybrid
#                                     search; kept for reference only.

from rlm.tools.memory import RLMMemory
from rlm.tools.memory_tools import get_memory_tools
from rlm.tools.session_memory_tools import get_session_memory_tools
from rlm.tools.kb_tools import get_kb_tools
from rlm.tools.vault_tools import get_vault_tools
from rlm.tools.codebase import get_codebase_tools
from rlm.tools.introspection_tools import get_introspection_tools
from rlm.tools.critic import (
    FuzzRound,
    CriticReport,
    run_critic_fuzzer,
    get_critic_tools,
)

__all__ = [
    # Core class
    "RLMMemory",
    # Tool factories
    "get_memory_tools",
    "get_session_memory_tools",
    "get_kb_tools",
    "get_vault_tools",
    "get_codebase_tools",
    "get_introspection_tools",
    "get_critic_tools",
    # Critic helper
    "run_critic_fuzzer",
    # Critic result types
    "FuzzRound",
    "CriticReport",
]

