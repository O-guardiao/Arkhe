"""
Wrappers for the RLMMemory system to be injected into the REPL.

Provides sandboxed access to the memory system, ensuring all path
resolutions respect the project root directory.
"""

from __future__ import annotations

import os
from typing import Any

from rlm.tools.memory import RLMMemory


def get_memory_tools(memory: RLMMemory, base_path: str, llm_query_batched_fn=None) -> dict[str, Any]:
    """
    Create a set of memory tools scoped to the base_path.

    Args:
        memory: The RLMMemory instance.
        base_path: Absolute path to the project root (for chunking source files).
        llm_query_batched_fn: Optional reference to llm_query_batched for parallel ops.

    Returns:
        Dict of tool name -> callable, ready for REPL namespace injection.
    """
    base_path = os.path.abspath(base_path)

    def _resolve(relative: str) -> str:
        """Resolve a relative path within the sandbox."""
        resolved = os.path.abspath(os.path.join(base_path, relative))
        if not resolved.startswith(base_path):
            raise PermissionError(f"Access denied: '{relative}' resolves outside project root.")
        return resolved

    def tool_memory_store(key: str, content: str) -> str:
        """Store exact content with a key. Lossless."""
        return memory.store(key, content)

    def tool_memory_read(key: str, start_line: int | None = None, end_line: int | None = None) -> str:
        """Retrieve exact stored content by key. Optionally specify line range."""
        result = memory.read(key, start_line=start_line, end_line=end_line)
        return result if result is not None else f"Error: Key '{key}' not found."

    def tool_memory_chunk_and_store(path: str, prefix: str, chunk_lines: int = 200) -> str:
        """Split a file into chunks and store losslessly. Path is relative to project."""
        try:
            full_path = _resolve(path)
            keys = memory.chunk_and_store(full_path, prefix, chunk_lines)
            if not keys:
                return f"Error: Could not chunk {path}"
            return f"Created {len(keys)} records under prefix '{prefix}'"
        except Exception as e:
            return f"Error: {e}"

    def tool_memory_reassemble(prefix: str) -> str:
        """Reconstruct a file EXACTLY from its chunks."""
        return memory.reassemble(prefix)

    def tool_memory_analyze(
        key: str, analysis: str, source_ref: str | None = None, line_range: tuple[int, int] | None = None
    ) -> str:
        """Store LLM analysis linked to a key and optional source reference."""
        return memory.analyze(key, analysis, source_ref=source_ref, line_range=line_range)

    def tool_memory_link(from_key: str, relation: str, to_key: str) -> str:
        """Create explicit directional relationship between two keys."""
        return memory.link(from_key, relation, to_key)

    def tool_memory_list(prefix: str = "", layer: str = "both") -> list[str]:
        """List stored keys. Layer can be 'raw', 'knowledge', or 'both'."""
        return memory.list_keys(prefix=prefix, layer=layer)

    def tool_memory_search(keyword: str) -> list[dict[str, str]]:
        """Search for a keyword across memory keys and analysis text."""
        return memory.search(keyword)

    def tool_memory_status() -> str:
        """Get an overview of the memory system status."""
        return memory.status()

    # We don't expose get_knowledge or get_links directly as dictionaries
    # to keep the REPL output clean. Searching and listing is preferred.

    # --- EVOLUTION 1: Batch parallel tools ---

    def tool_memory_batch_analyze(
        items: list[tuple[str, str]],
        prompt_template: str = "Analyze this code and summarize its purpose, key classes/functions, and dependencies:\n\n{content}",
    ) -> list[str]:
        """Analyze multiple items IN PARALLEL using llm_query_batched.

        Args:
            items: List of (key, content) tuples to analyze.
            prompt_template: Template with {key} and {content} placeholders.

        Returns:
            List of analysis results (one per item).
        """
        if llm_query_batched_fn is None:
            # Fallback: sequential analysis via memory.analyze with manual summaries
            results = []
            for key, content in items:
                summary = f"[Sequential fallback] Stored raw content for key '{key}' ({len(content)} chars)"
                memory.analyze(key, summary)
                results.append(summary)
            return results

        # Build prompts from template
        prompts = []
        for key, content in items:
            prompt = prompt_template.replace("{key}", str(key)).replace("{content}", str(content))
            prompts.append(prompt)

        # Fire all prompts to sub-LMs concurrently!
        responses = llm_query_batched_fn(prompts)

        # Save each response as a knowledge node
        for (key, _content), analysis in zip(items, responses):
            memory.analyze(key, str(analysis))

        return responses

    def tool_memory_batch_chunk_and_store(
        files: list[tuple[str, str]],
        chunk_lines: int = 200,
    ) -> list[str]:
        """Chunk and store multiple files at once.

        Args:
            files: List of (relative_path, key_prefix) tuples.
            chunk_lines: Lines per chunk.

        Returns:
            List of status messages.
        """
        results = []
        for rel_path, prefix in files:
            try:
                full_path = _resolve(rel_path)
                keys = memory.chunk_and_store(full_path, prefix, chunk_lines)
                if not keys:
                    results.append(f"Error: Could not chunk {rel_path}")
                else:
                    results.append(f"Created {len(keys)} chunks under '{prefix}'")
            except Exception as e:
                results.append(f"Error chunking {rel_path}: {e}")
        return results

    def tool_memory_semantic_search(query: str, top_k: int = 5) -> list[dict]:
        """Semantic search: find memory nodes by MEANING, not exact text.

        Uses vector embeddings for cosine similarity search.
        Requires sentence-transformers to be installed.

        Args:
            query: Natural language query (e.g., "authentication middleware").
            top_k: Number of results.

        Returns:
            List of dicts with key, text preview, and similarity score.
        """
        return memory.semantic_search(query, top_k=top_k)

    return {
        "memory_store": tool_memory_store,
        "memory_read": tool_memory_read,
        "memory_chunk_and_store": tool_memory_chunk_and_store,
        "memory_reassemble": tool_memory_reassemble,
        "memory_analyze": tool_memory_analyze,
        "memory_link": tool_memory_link,
        "memory_list": tool_memory_list,
        "memory_search": tool_memory_search,
        "memory_status": tool_memory_status,
        # Evolution 1: Batch parallel tools
        "memory_batch_analyze": tool_memory_batch_analyze,
        "memory_batch_chunk_and_store": tool_memory_batch_chunk_and_store,
        # Evolution 2: Semantic search
        "memory_semantic_search": tool_memory_semantic_search,
    }
