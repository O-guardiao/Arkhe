+++
name = "session_memory"
description = "Search conversational long-term memory curated by the MINI agent. Returns past decisions, preferences, and context from this and previous sessions. NOT workspace/codebase memory — use memory_search for that."
tags = ["memória", "lembrar", "recordar", "memorizar", "contexto anterior", "sessão anterior", "já falamos", "decidimos", "histórico"]
priority = "contextual"

[requires]
# No MCP — callable injected into REPL by runtime_pipeline via session_memory_tools
bins = []

[sif]
signature = "session_memory_search(query: str, top_k: int = 5) -> list[dict]"
prompt_hint = "Use quando a resposta depende de algo já decidido antes, preferências do usuário ou contexto persistente de sessões anteriores."
short_sig = "session_memory_search(q,k=5)→[dict]"
compose = ["sqlite"]
examples_min = ["recuperar decisões anteriores ou preferências do usuário"]
impl = ""

[runtime]
estimated_cost = 0.1
risk_level = "low"
side_effects = ["memory_read"]
postconditions = ["relevant_memory_retrieved"]
fallback_policy = "continue_without_memory"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

# ── Ownership Boundary (ADR-004) ──────────────────────────────────────
# Este arquivo é METADATA do skill loader. A implementação canônica vive em:
#   rlm/tools/memory.py         — function-calling tools para LLM
#   rlm/tools/memory_tools.py   — helpers de session memory
# Este SKILL.md declara a interface SIF exposta ao pipeline. Não contém código.
# Dono canônico do conceito "memory": rlm/tools/memory*.py (L0 core tooling).

[retrieval]
embedding_text = "session memory recall history prior decisions preferences persistent context conversational"
example_queries = ["o que decidimos antes sobre isso", "quais são minhas preferências salvas"]
+++

# Session Memory Skill

Access the RLM conversational long-term memory — curated by the MINI agent (GPT-4.1-nano).
These are session-scoped memories (decisions, preferences, context) stored in MultiVectorMemory.

**NOT the same as workspace/codebase memory** (`memory_*` tools via RLMMemory).

## When to Use

✅ **USE when:**
- "What did we decide about the database schema?"
- "Remind me of my preferences for code style"
- User references previous sessions implicitly
- You need context beyond the current conversation window

❌ **DON'T use when:**
- Information is already in the current conversation
- Real-time data is needed → use web search
- You need codebase/file analysis → use `memory_search` (workspace tools)

## REPL Usage

The tools are injected automatically into the REPL by the runtime pipeline.

```python
# Search past decisions and context  
results = session_memory_search("database schema decisions", top_k=5)
for r in results:
    print(f"[{r.get('timestamp', '?')}] {r.get('content', '')}")

# Check memory store status
status = session_memory_status()
print(f"Active memories: {status.get('active_chunks', 0)}")

# See most recent memories
recent = session_memory_recent(limit=5)
for m in recent:
    print(f"[importance={m['importance']:.1f}] {m['content'][:80]}")
```
