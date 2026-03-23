+++
name = "sqlite"
description = "Query SQLite databases via MCP. Use when: user asks to query, read, insert or analyze data stored in a .db or .sqlite file. NOT for: PostgreSQL, MySQL, or other databases."
tags = ["sqlite", "banco de dados", "sql", "db", "query", "tabela", "database", "consultar dados", ".db", ".sqlite"]
priority = "contextual"

[sif]
signature = "sqlite.query(sql: str, db: str = 'data.db') -> list[dict]"
prompt_hint = "Use para consultar, inspecionar ou analisar dados guardados em arquivos .db ou .sqlite."
short_sig = "sqlite.query(sql,db) [MCP]"
compose = ["notion", "shell", "email"]
examples_min = ["consultar uma tabela SQLite e resumir resultados"]

[runtime]
estimated_cost = 0.35
risk_level = "medium"
side_effects = ["database_read", "database_write"]
postconditions = ["sqlite_query_executed"]
fallback_policy = "inspect_db_via_shell_or_python"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "sqlite sql database query table select insert db file"
example_queries = ["rode uma query SQL", "abra este banco sqlite"]

[mcp]
command = "uvx"
args = ["mcp-server-sqlite", "--db-path", "data.db"]

[requires]
bins = ["uv"]
+++

# SQLite Skill

Access and query SQLite databases directly from the REPL.

## When to Use

✅ **USE when:**
- "Query the users table"
- "How many records are in the database?"
- "Show me orders from last month"
- Analyzing `.db` or `.sqlite` files

❌ **DON'T use when:**
- PostgreSQL, MySQL, or cloud databases → use their specific connectors
- Creating new databases from scratch → prefer Python `sqlite3` stdlib

## REPL Usage

The `sqlite` namespace is auto-injected when this skill activates.

```python
# See available tools
print(sqlite.list_tools())

# Run a SELECT query
results = sqlite.read_query(query="SELECT * FROM users LIMIT 10")
print(results)

# List all tables
tables = sqlite.list_tables()
print(tables)

# Get table schema
schema = sqlite.describe_table(table_name="orders")
print(schema)
```

## Switching Databases

To connect to a different DB file, reload the server:

```python
from rlm.plugins.mcp import load_server
sqlite = load_server("sqlite", "uvx", ["mcp-server-sqlite", "--db-path", "/path/to/other.db"])
results = sqlite.read_query(query="SELECT * FROM products")
```
