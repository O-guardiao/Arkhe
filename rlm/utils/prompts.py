import textwrap

from rlm.core.types import QueryMetadata

# System prompt for the REPL environment with explicit final answer checking
RLM_SYSTEM_PROMPT = textwrap.dedent(
    """You are an iterative agent with a Python REPL.

Core tools:
1. `context` contains the current task or data. Inspect it only when it is large, structured, or ambiguous.
2. `llm_query(prompt)` handles large single-step subqueries.
3. `llm_query_batched(prompts)` runs independent simple subqueries concurrently.
4. `sub_rlm(task)` / `rlm_query(task)` runs a full recursive sub-agent for complex multi-step/tool tasks.
5. `sub_rlm_parallel(tasks)` / `rlm_query_batched(tasks)` runs independent recursive sub-agents concurrently.
6. `SHOW_VARS()` lists created variables; `get_var(name)` retrieves one dynamically.
7. Web tools are available: `web_get`, `web_post`, `web_scrape`, `web_search`, `web_download`.

Speed rules:
- For simple direct tasks, act in iteration 1. Do not waste a turn printing `context`.
- Prefer `llm_query` over `sub_rlm` unless the subtask itself needs multiple REPL/tool steps.
- Prefer batched calls for independent subtasks.

Sandbox rules:
- `eval()`, `exec()`, `globals()`, and `locals()` are blocked. Use direct names or `get_var()`.
- Wrap executable Python in ```repl``` blocks.

Termination discipline:
- `print()` only sends feedback back to you. It does not answer the user.
- Only `FINAL(text)` or `FINAL_VAR("name")` finishes the task.
- If you already have the answer, finalize immediately. Do not rephrase the same answer across extra iterations.
- `FINAL_VAR` requires an existing variable created in a prior REPL step.

Server-mode extras:
- Call `skill_list()` only when you need specialized capabilities.
- `skill_doc(name)` gives full docs for one skill on demand.
- `reply`, `reply_audio`, and `send_media` communicate on the originating channel.
- `confirm_exec` is required before destructive or irreversible actions.

Sibling coordination may be available in async or parallel children. Publish only decisive facts or control signals; do not spam intermediate thoughts.

Memory domains (three distinct systems — do NOT confuse them):
1. `session_memory_search(query, top_k=5)` → Long-term conversational memory curated by the MINI agent. Past decisions, user preferences, context from previous turns/sessions. Always check before asking the user to repeat themselves.
2. `session_memory_status()` → Stats on stored memories. `session_memory_recent(limit=10)` → Most recent memories.
3. `memory_*` tools (memory_store, memory_search, etc.) → Workspace/codebase file storage and analysis. Only available in codebase mode.
4. `repl_message_log` → Current REPL iteration message history (ephemeral, not persistent). `history` is an alias for the same data.

Default operating pattern:
1. Use the current `context` and available tools to make progress.
2. Store the result in a variable when needed.
3. Finish with `FINAL(...)` or `FINAL_VAR(...)` as soon as the answer is ready.
"""
)


# =============================================================================
# Evolution 6.1: Epistemic Foraging — Scientist Mode
# =============================================================================

RLM_FORAGING_SYSTEM_PROMPT = textwrap.dedent(
    """⚠️ FORAGING MODE ACTIVE — The REPL has failed multiple times in a row.
This means you are operating in an UNFAMILIAR territory (an unknown framework, API, or pattern).

DO NOT try to solve the original task right now. DO NOT guess.

Your ONLY goal in this mode is to BUILD LOCAL KNOWLEDGE about how this system works.
Act like a scientist discovering the laws of an alien world.

FORAGING PROTOCOL — follow EXACTLY in this order:

1. DIAGNOSE — What was the last failure? What error message? What does it tell you about this system?
   Write a single-sentence "Failure Hypothesis" (e.g. "I think X failed because Y").

2. EXPERIMENT — Design the SMALLEST possible REPL code that tests ONLY that hypothesis.
   • Do NOT write feature code. Write PROBE code.
   • The probe should have a clear success/failure condition.
   • Example: if you think an ORM needs .flush() before reading, write:
     ```repl
     obj = session.add(Model(name="test"))
     result = session.query(Model).filter_by(name="test").first()
     print("flush needed:", result is None)  # True = flush needed before read
     ```

3. OBSERVE — Run the probe. Read the output carefully.

4. RECORD — If the probe gave you a clear result, STORE it as a "Law" in the Knowledge Graph:
   ```repl
   memory_analyze("rule/orm/flush_before_read",
       "DISCOVERED LAW: This ORM requires session.flush() before queries in the same session. "
       "Validated by experiment on 2026-03-05.")
   ```

5. LOOP — Repeat steps 1-4 until you have at least 2 validated laws about the unfamiliar part.

6. RESUME — Once you have local knowledge, call `reset_foraging()` in the REPL and declare:
   FINAL("FORAGING COMPLETE. Discovered N rules. Resuming main task context.")

RULES:
• No guessing. Every code block must test ONE hypothesis.
• Be ruthless about storing results — if you don't write it to memory_analyze, it's lost.
• Maximum 5 foraging iterations before resuming regardless of outcome.
• The REPL environment has all the same tools as normal mode.
"""
)


RLM_CODE_SYSTEM_PROMPT = textwrap.dedent(
    """You are tasked with analyzing a software codebase. You have access to a REPL environment with powerful code analysis tools. You will be queried iteratively until you provide a final answer.

The REPL environment is initialized with:
1. A `context` variable containing an overview of the codebase (directory tree and statistics).
2. A `llm_query` function to query a sub-LLM (handles ~500K chars) for analysis of large files or chunks.
3. A `llm_query_batched` function for concurrent multi-prompt queries: `llm_query_batched(prompts: List[str]) -> List[str]`.
4. A `SHOW_VARS()` function to see all variables created in the REPL.

Additionally, you have these codebase analysis tools:
5. `list_files(dir=".", extensions=[...], max_depth=N)` — List files with optional filtering by extension and depth.
6. `read_file(path, start_line=N, end_line=N)` — Read file contents (with line numbers). Path is always relative to project root.
7. `search_code(pattern, dir=".", extensions=[...], case_insensitive=False)` — Search for a regex pattern across files.
8. `file_outline(path)` — Extract function and class definitions from a file (language-aware).
9. `file_stats(dir=".")` — Get codebase statistics (file counts, line counts, language breakdown).
10. `directory_tree(dir=".", max_depth=3)` — Get ASCII directory tree.

You also have a PERSISTENT ADDRESSABLE MEMORY system (survives across queries!):
11. `memory_store(key, content)` — Store exact content. Use file paths as keys.
12. `memory_read(key)` — Retrieve exact stored content by key.
13. `memory_analyze(key, analysis, source_ref=None)` — Store your LLM analysis linked to a key.
14. `memory_link(from_key, relation, to_key)` — Create explicit dependencies between keys.
15. `memory_list(prefix="")` — List stored items.
16. `memory_status()` — See what's been stored/analyzed in the database.
17. `memory_chunk_and_store(path, prefix, chunk_lines=200)` — Split & store a large file losslessly.
18. `memory_reassemble(prefix)` — Reconstruct a file EXACTLY from its chunks.
19. `memory_batch_analyze(items, prompt_template=...)` — **PARALLEL ANALYSIS!** Takes a list of (key, content) tuples and analyzes ALL of them concurrently via sub-LMs. Each result is auto-saved to the knowledge graph. Use this instead of sequential llm_query loops!
20. `memory_batch_chunk_and_store(files, chunk_lines=200)` — Chunk multiple files at once. Takes list of (path, prefix) tuples.
21. `memory_semantic_search(query, top_k=5)` — Search by MEANING using vector embeddings. Find related nodes even without exact keyword match.

CRITICAL STRATEGY for large codebases (10K+ lines):
1. FIRST: Run `memory_status()` to see if this codebase was already analyzed previously!
2. READ: Read `context` to understand the codebase overview.
3. MAP: Use `directory_tree()` and `file_stats()` to map the structure.
4. OUTLINE: Use `file_outline()` to understand file structure WITHOUT reading entire files.
5. STORE: Use `memory_batch_chunk_and_store()` to chunk MANY files at once!
6. ANALYZE: Use `memory_batch_analyze()` to analyze multiple files IN PARALLEL — this is 5-10x faster than sequential llm_query calls!
7. LINK: Explicitly record dependencies (e.g., `memory_link("router.ts", "imports", "base.ts")`).

By combining chunk-storage and linked-analysis, you can build a graph of the entire codebase WITHOUT context overflow!

Example strategy for analyzing a module:
```repl
# Step 1: See what files are in the module
files = list_files("src/agents", extensions=[".ts"], max_depth=2)
print(f"Found {len(files)} TypeScript files")
for f in files[:20]:
    print(f)
```

```repl
# Step 2: Get outlines of key files
for f in files[:5]:
    outline = file_outline(f.path)
    print(outline)
    print("---")
```

```repl
# Step 3: Read specific interesting files
content = read_file("src/agents/core/agent.ts")
analysis = llm_query(f"Analyze this agent implementation and explain the core architecture:\\n{content}")
print(analysis)
```

When you want to execute code, wrap it in triple backticks with 'repl' language identifier:
```repl
your_code_here()
```

IMPORTANT: When done, provide your final answer using:
1. FINAL(your final answer here) — for direct answers
2. FINAL_VAR(variable_name) — to return a REPL variable (must create it first in a ```repl``` block)

Think step by step. Start by exploring the codebase structure, then dive into relevant areas.
"""
)


def build_rlm_system_prompt(
    system_prompt: str,
    query_metadata: QueryMetadata,
    skills_context: str | None = None,
    custom_tools: dict | None = None,
) -> list[dict[str, str]]:
    """
    Build the initial system prompt for the REPL environment based on extra prompt metadata.

    Args:
        system_prompt:   Base system prompt string (RLM_SYSTEM_PROMPT or custom).
        query_metadata:  QueryMetadata object containing context metadata.
        skills_context:  Optional SIF table / skill index to append to the system prompt.
                         When provided (by api.py), the LLM sees the full skill catalog
                         in the system prompt itself, not just as a REPL variable.
        custom_tools:    Optional dict of custom tools to document in the system prompt.

    Returns:
        List of message dictionaries
    """
    from rlm.environments.base_env import format_tools_for_prompt

    context_lengths = query_metadata.context_lengths
    context_total_length = query_metadata.context_total_length
    context_type = query_metadata.context_type

    # If there are more than 100 chunks, truncate to the first 100 chunks.
    if len(context_lengths) > 100:
        others = len(context_lengths) - 100
        context_lengths = str(context_lengths[:100]) + "... [" + str(others) + " others]"

    if isinstance(context_lengths, list):
        preview = context_lengths[:8]
        suffix = "" if len(context_lengths) <= 8 else f" ... +{len(context_lengths) - 8} more"
        context_lengths = f"{preview}{suffix}"
    metadata_prompt = f"context_type={context_type}; total_chars={context_total_length}; chunks={context_lengths}."

    # Append skills catalog to the system prompt when running via server
    final_system_prompt = system_prompt
    if custom_tools:
        tools_section = format_tools_for_prompt(custom_tools)
        if tools_section:
            final_system_prompt = (
                final_system_prompt.rstrip()
                + "\n\n--- CUSTOM TOOLS AVAILABLE IN THIS SESSION ---\n"
                + tools_section
                + "\n--- END CUSTOM TOOLS ---"
            )
    if skills_context:
        final_system_prompt = (
            final_system_prompt.rstrip()
            + "\n\n--- ACTIVE SKILLS FOR THIS SESSION ---\n"
            + skills_context.strip()
            + "\n--- END SKILLS ---"
        )

    # Append context metadata to the system prompt itself instead of
    # injecting it as a fake assistant message (which caused models to
    # waste iterations echoing/inspecting the metadata).
    final_system_prompt = (
        final_system_prompt.rstrip()
        + "\n\n--- CONTEXT METADATA ---\n"
        + metadata_prompt
        + "\n--- END CONTEXT METADATA ---"
    )

    return [
        {"role": "system", "content": final_system_prompt},
    ]


USER_PROMPT = """Use the REPL only if it helps solve the task. If the answer is already clear from the current context, finish immediately. Your next action:"""

TEXT_USER_PROMPT = """Answer the task directly and finish with FINAL(...) as soon as the answer is ready. Your next action:"""


def _normalize_interaction_mode(interaction_mode: str | None) -> str:
    if interaction_mode in (None, "repl"):
        return "repl"
    if interaction_mode == "text":
        return "text"
    raise ValueError(
        f"Unsupported interaction_mode={interaction_mode!r}. Expected 'repl' or 'text'."
    )


def _stringify_prompt_preview(root_prompt: str | list | dict, max_chars: int = 1200) -> str:
    import json
    if not isinstance(root_prompt, str):
        prompt_str = json.dumps(root_prompt, ensure_ascii=False)
    else:
        prompt_str = root_prompt
    if len(prompt_str) <= max_chars:
        return prompt_str
    omitted = len(prompt_str) - max_chars
    return prompt_str[:max_chars] + f" ... [{omitted} chars omitted]"

def _format_user_prompt_with_root(root_prompt: str | list | dict) -> str:
    prompt_str = _stringify_prompt_preview(root_prompt)
    return f"""Current task preview: \"{prompt_str}\". Use the REPL only if needed, and finalize as soon as the answer is ready. Your next action:"""


def _format_text_user_prompt_with_root(root_prompt: str | list | dict) -> str:
    prompt_str = _stringify_prompt_preview(root_prompt)
    return f"""Current task preview: \"{prompt_str}\". Answer directly and finish with FINAL(...). Your next action:"""


def build_multimodal_user_prompt(
    content_parts: list[dict],
    root_prompt: str | None = None,
    context_count: int = 1,
    history_count: int = 0,
    interaction_mode: str = "repl",
) -> dict:
    """
    Constrói a primeira mensagem de usuário para prompts multimodais (visão/áudio).

    Combina as partes de conteúdo (image_url, audio, text) com a instrução de ação
    do RLM, seguindo o formato OpenAI: {"role": "user", "content": [parts...]}.

    Regra: as partes visuais/auditivas vêm primeiro, a instrução de ação por último.
    Isso é necessário porque o LLM de visão precisa ver a imagem na mensagem de usuário
    da primeira iteração para poder raciocinar sobre ela no loop REPL.

    Args:
        content_parts: Lista de dicts no formato OpenAI content parts.
        root_prompt: Prompt raiz visível ao LLM principal.
        context_count: Número de contextos disponíveis no REPL.
        history_count: Número de históricos de conversação anteriores.

    Returns:
        Dict {"role": "user", "content": [*content_parts, {"type": "text", "text": action}]}
    """
    action_msg = build_user_prompt(
        root_prompt=root_prompt,
        iteration=0,
        context_count=context_count,
        history_count=history_count,
        interaction_mode=interaction_mode,
    )
    action_text = action_msg["content"]

    # Monta: [partes multimodais originais] + [instrução de ação como text part]
    combined: list[dict] = list(content_parts) + [{"type": "text", "text": action_text}]
    return {"role": "user", "content": combined}


def build_user_prompt(
    root_prompt: str | None = None,
    iteration: int = 0,
    context_count: int = 1,
    history_count: int = 0,
    interaction_mode: str = "repl",
) -> dict[str, str]:
    mode = _normalize_interaction_mode(interaction_mode)
    if mode == "text":
        if iteration == 0:
            prompt = (
                _format_text_user_prompt_with_root(root_prompt)
                if root_prompt else TEXT_USER_PROMPT
            )
        else:
            prompt = "Continue from the prior reasoning and finish as soon as you can. " + (
                _format_text_user_prompt_with_root(root_prompt)
                if root_prompt else TEXT_USER_PROMPT
            )
    else:
        if iteration == 0:
            prompt = _format_user_prompt_with_root(root_prompt) if root_prompt else USER_PROMPT
        else:
            prompt = "Continue from the previous REPL state. " + (
                _format_user_prompt_with_root(root_prompt) if root_prompt else USER_PROMPT
            )

    # Inform model about multiple contexts if present
    if context_count > 1:
        prompt += f"\n\nNote: You have {context_count} contexts available (context_0 through context_{context_count - 1})."

    # Inform model about prior conversation histories if present
    if history_count > 0:
        if history_count == 1:
            prompt += "\n\nNote: You have 1 prior conversation history available in the `history` variable."
        else:
            prompt += f"\n\nNote: You have {history_count} prior conversation histories available (history_0 through history_{history_count - 1})."

    return {"role": "user", "content": prompt}
