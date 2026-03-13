+++
name = "filesystem"
description = "Read, write, list and search files on the local filesystem via MCP. Use when: user asks to read a file, list directory contents, search files, or write/create files on disk. NOT for: downloading URLs (use requests), database files (use sqlite skill), or binary files."
tags = ["arquivo", "pasta", "diretório", "ler arquivo", "salvar arquivo", "criar arquivo", "apagar", "listar arquivos", "filesystem", "sistema de arquivos"]
priority = "contextual"

[mcp]
command = "npx.cmd"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]

[requires]
bins = ["node"]

[sif]
signature = "fs_read(path: str) -> str"
prompt_hint = "Use para ler, listar, criar ou localizar arquivos e pastas no disco local via MCP."
short_sig = "fs_read(path)→str"
compose = ["sqlite", "shell"]
examples_min = ["ler um arquivo local e listar uma pasta"]
impl = """
def fs_read(path):
    return open(path, encoding='utf-8', errors='replace').read()

def fs_write(path, content):
    open(path, 'w', encoding='utf-8').write(content)
    return f"Written {len(content)} bytes to {path}"

def fs_ls(path="."):
    import os
    return os.listdir(path)
"""

[runtime]
estimated_cost = 0.25
risk_level = "medium"
side_effects = ["filesystem_read", "filesystem_write"]
postconditions = ["filesystem_state_inspected_or_updated"]
fallback_policy = "use_shell_for_manual_inspection"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "filesystem file folder directory read write list search disk"
example_queries = ["abra este arquivo", "liste os arquivos desta pasta"]
+++

# Filesystem Skill

Read and write local files via MCP — safer and more structured than raw `open()`.

## When to Use

✅ **USE when:**
- "Read the contents of config.json"
- "List all Python files in src/"
- "Find files containing 'TODO'"
- "Write results to output.txt"

❌ **DON'T use when:**
- Remote files or URLs → use `requests` in REPL
- SQL databases → use `sqlite` skill
- Git operations → use `subprocess`

## REPL Usage

```python
# List directory
contents = fs.list_directory(path=".")
print(contents)

# Read a file
text = fs.read_file(path="config.json")
print(text)

# Write a file
fs.write_file(path="output.txt", content="Results:\n" + data)

# Search for text in files
matches = fs.search_files(path="src", pattern="TODO")
print(matches)

# Get file info
info = fs.get_file_info(path="data.csv")
print(info)
```

## Security Note

The server is scoped to the working directory (`.` by default).
To change scope, reload with a different base path:

```python
from rlm.plugins.mcp import load_server
fs = load_server("filesystem", "npx.cmd", ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/workspace"])
```
