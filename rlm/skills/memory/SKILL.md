+++
name = "memory"
description = "Search the RLM persistent memory for relevant past context, decisions, or user preferences. Use when: user references something discussed before, asks 'what did we decide about X', or when context from a previous session would improve the response."
tags = ["memória", "lembrar", "recordar", "memorizar", "contexto anterior", "sessão anterior", "já falamos", "decidimos", "histórico"]
priority = "contextual"

[requires]
# Sem MCP — acessa MultiVectorMemory diretamente via Python
bins = []

[sif]
signature = "memory_search(query: str, top_k: int = 5) -> list[str]"
prompt_hint = "Use quando a resposta depende de algo já decidido antes, preferências do usuário ou contexto persistente."
short_sig = "memory_search(q,k=5)→[str]"
compose = ["sqlite", "web_search"]
examples_min = ["recuperar decisões anteriores ou preferências do usuário"]
impl = """
def memory_search(query, top_k=5):
    try:
        from rlm.memory.multivector import get_memory_store
        store = get_memory_store()
        results = store.search(query, top_k=top_k)
        return [r.text if hasattr(r, 'text') else str(r) for r in results]
    except Exception as e:
        return [f"memory_search error: {e}"]
"""

[runtime]
estimated_cost = 0.2
risk_level = "low"
side_effects = ["memory_read"]
postconditions = ["relevant_memory_retrieved"]
fallback_policy = "continue_without_memory"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "memory recall history prior decisions preferences persistent context"
example_queries = ["o que decidimos antes sobre isso", "quais são minhas preferências salvas"]
+++

# Memory Skill

Access the RLM long-term memory store — no MCP server needed, pure Python.

## When to Use

✅ **USE when:**
- "What did we decide about the database schema?"
- "Remind me of my preferences for code style"
- User references previous sessions implicitly
- You need context beyond the current conversation window

❌ **DON'T use when:**
- Information is already in the current conversation
- Real-time data is needed → use web search
- Current session context is sufficient

## REPL Usage

```python
from rlm.core.memory_manager import MultiVectorMemory

mem = MultiVectorMemory()

# Basic hybrid search (FTS + vector + temporal decay)
results = mem.search_hybrid(
    "database schema decisions",
    limit=5,
    session_id=session_id,  # available in REPL context
    temporal_decay=True,
    half_life_days=30,
)
for r in results:
    print(f"[{r['timestamp']}] {r['content']}")

# Search with query expansion (LLM generates variants)
results = mem.search_with_query_expansion(
    "code style preferences",
    llm_fn=lambda p: llm_query(p),  # llm_query is a REPL built-in
    limit=5,
)

# Store a new memory
mem.add_memory(
    session_id=session_id,
    content="User prefers 4-space indentation and type hints everywhere",
    metadata={"category": "preferences", "confidence": "high"},
)
```
