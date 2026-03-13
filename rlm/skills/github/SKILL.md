+++
name = "github"
description = "GitHub REST API: issues, PRs, repos, código, releases, Actions CI. Use when: user asks to criar/comentar issues, verificar status de PR, listar repos, buscar código, ver CI runs, listas releases. NOT for: operações git locais (commit/push/pull), clonar repos (use subprocess git clone), repos não-GitHub."
tags = ["github", "git", "pull request", "pr", "commit", "repositório", "issue", "ci", "actions", "pipeline", "código fonte", "release"]
priority = "contextual"

[sif]
signature = "github.issue(repo: str, title: str, body: str = '') -> dict"
prompt_hint = "Use para consultar repositórios, PRs, issues, Actions ou publicar atualização no GitHub via API."
short_sig = "github.issue(repo,title,body)"
compose = ["shell", "slack", "email"]
examples_min = ["criar issue ou consultar PR em um repositório GitHub"]

[runtime]
estimated_cost = 0.8
risk_level = "medium"
side_effects = ["remote_api_read", "remote_api_write"]
preconditions = ["env:GITHUB_TOKEN"]
postconditions = ["github_resource_updated_or_inspected"]
fallback_policy = "use_shell_git_for_local_repo_or_ask_user"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "github repo pull request issue actions workflow release source code"
example_queries = ["veja o status do PR", "crie uma issue no GitHub"]

[requires]
bins = []
+++

# GitHub Skill

GitHub REST API diretamente via `requests` no REPL Python.

## Quando usar

✅ **USE quando:**
- "Cria uma issue no repo X"
- "Lista os PRs abertos de owner/repo"
- "Verifica status do CI no PR #55"
- "Busca código Python que usa asyncio no GitHub"
- "Lista releases do projeto"
- "Comenta no PR #10"

❌ **NÃO use quando:**
- Operações git locais → `subprocess` com `git`
- Repos GitLab/Bitbucket → APIs diferentes
- Clonar repo → `subprocess.run(["git", "clone", url])`

## Setup

```python
import os
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# Gera em: https://github.com/settings/tokens (scopes: repo, read:user)
```

## Client helper (coloca no início do REPL)

```python
import requests, os

class GH:
    BASE = "https://api.github.com"
    
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    
    def get(self, path: str, **params) -> dict | list:
        r = requests.get(f"{self.BASE}{path}", headers=self.headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    
    def post(self, path: str, **body) -> dict:
        r = requests.post(f"{self.BASE}{path}", headers=self.headers, json=body, timeout=15)
        r.raise_for_status()
        return r.json()
    
    def patch(self, path: str, **body) -> dict:
        r = requests.patch(f"{self.BASE}{path}", headers=self.headers, json=body, timeout=15)
        r.raise_for_status()
        return r.json()

gh = GH()
```

## Operações comuns

```python
# Listar issues abertas
issues = gh.get("/repos/owner/repo/issues", state="open", per_page=20)
for i in issues:
    print(f"#{i['number']} {i['title']} — {i['user']['login']}")

# Criar issue
nova = gh.post("/repos/owner/repo/issues",
               title="Bug: X não funciona",
               body="Descrição detalhada...",
               labels=["bug"])
print(f"Issue criada: {nova['html_url']}")

# Listar PRs
prs = gh.get("/repos/owner/repo/pulls", state="open")
for pr in prs:
    print(f"PR #{pr['number']} {pr['title']} — {pr['head']['ref']}")

# Status de checks de um PR
checks = gh.get(f"/repos/owner/repo/commits/{pr['head']['sha']}/check-runs")
for c in checks["check_runs"]:
    print(f"{c['name']}: {c['status']} / {c['conclusion']}")

# Comentar num PR/issue
gh.post("/repos/owner/repo/issues/42/comments",
        body="Análise concluída — LGTM ✅")

# Buscar código
resultados = gh.get("/search/code", q="asyncio language:python repo:owner/repo")
for item in resultados["items"][:5]:
    print(item["path"], item["html_url"])

# Listar releases
releases = gh.get("/repos/owner/repo/releases")
print(f"Última release: {releases[0]['tag_name']} — {releases[0]['published_at']}")

# Info do repo
repo = gh.get("/repos/owner/repo")
print(f"Stars: {repo['stargazers_count']} | Forks: {repo['forks_count']}")
```

## Variáveis de ambiente

- `GITHUB_TOKEN` — token de acesso pessoal (clássico ou fine-grained)
