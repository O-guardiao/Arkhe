+++
name = "browser"
description = "Fetch web pages, scrape structured content, submit forms, and follow links using Python requests + BeautifulSoup. Use when: user asks to read a webpage, extract data from HTML, download files from URLs, or interact with web content via HTTP. NOT for: JavaScript-heavy SPAs (use playwright skill), authentication flows requiring browser cookies, or video streaming."
tags = ["browser", "web", "url", "navegar", "acessar site", "http", "html", "scraping", "página web", "baixar", "requests", "beautifulsoup"]
priority = "contextual"

[sif]
signature = "browser.get(url: str, selector: str = '') -> str"
prompt_hint = "Use para ler uma URL específica, extrair HTML/texto ou seguir páginas estáticas sem depender de JavaScript."
short_sig = "browser.get(url,sel)"
compose = ["web_search", "notion", "shell"]
examples_min = ["abrir uma URL e extrair o texto principal"]

[runtime]
estimated_cost = 0.45
risk_level = "low"
side_effects = ["http_request"]
postconditions = ["web_content_fetched"]
fallback_policy = "escalate_to_playwright_if_js_required"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "browser web url html scraping page requests content static site"
example_queries = ["leia esta página web", "extraia o conteúdo desta URL"]

[requires]
# Sem MCP — executa via imports Python no REPL
bins = []
+++

# Browser Skill

HTTP requests e scraping de HTML diretamente no REPL Python.

## Quando usar

✅ **USE quando:**
- "Acessa o site X e extrai o preço do produto"
- "Baixa o conteúdo desta página"
- "Faz um POST neste formulário"
- "Lê o feed RSS de..."
- "Baixa um arquivo de uma URL"

❌ **NÃO use quando:**
- Página usa JS pesado (React/Vue SPA) → use `playwright` skill
- Login com OAuth2 interativo → requer browser com cookies
- Streaming de vídeo → YouTube DL

## Funções injetadas no REPL (Tier B — disponíveis diretamente)

```python
# Estas funções JÁ ESTÃO no namespace REPL — não precisa importar:

# GET simples — retorna texto HTML
html = web_get("https://example.com")

# GET com headers customizados
html = web_get("https://api.example.com/data", headers={"Authorization": "Bearer TOKEN"})

# Busca na web via DuckDuckGo (sem API key)
resultados = web_search("python asyncio tutorial", max_results=5)
# retorna: [{"title": "...", "url": "...", "snippet": "..."}]

# Scrape estruturado — extrai texto limpo + links
dados = web_scrape("https://example.com")
# retorna: {"title": str, "text": str, "links": [{"text": str, "href": str}]}

# POST com JSON body
resp = web_post("https://api.example.com/endpoint", json={"key": "value"})
# retorna: dict (se JSON) ou str

# Download de arquivo para disco
path = web_download("https://example.com/file.csv", dest="/tmp/file.csv")
print(f"Baixado em: {path}")
```

## Uso manual (requests + bs4 no REPL)

```python
import requests
from bs4 import BeautifulSoup

# Fetch de página
r = requests.get("https://example.com", timeout=15)
r.raise_for_status()

soup = BeautifulSoup(r.text, "html.parser")

# Extrair título
title = soup.find("title").text

# Extrair todos os links
links = [(a.text.strip(), a["href"]) for a in soup.find_all("a", href=True)]

# Extrair tabela como lista de dicts
table = soup.find("table")
headers = [th.text.strip() for th in table.find_all("th")]
rows = [
    dict(zip(headers, [td.text.strip() for td in tr.find_all("td")]))
    for tr in table.find_all("tr")[1:]
]

FINAL_VAR("rows")
```

## Autenticação HTTP Basic / Bearer

```python
import requests

# Basic auth
r = requests.get("https://api.example.com/data",
                  auth=("username", "password"), timeout=15)

# Bearer token
headers = {"Authorization": f"Bearer {os.environ['API_TOKEN']}"}
r = requests.get("https://api.example.com/protected", headers=headers, timeout=15)

data = r.json()
FINAL_VAR("data")
```

## JSON API

```python
import requests

resp = requests.get("https://api.github.com/repos/owner/repo", timeout=15)
repo = resp.json()

resultado = {
    "stars": repo["stargazers_count"],
    "forks": repo["forks_count"],
    "description": repo["description"],
}
FINAL_VAR("resultado")
```

## Notas

- `requests` e `beautifulsoup4` devem estar instalados no ambiente Python.
- Para instalar: `pip install requests beautifulsoup4`
- Respeite `robots.txt` e termos de serviço dos sites.
- Timeout padrão recomendado: 15-30s.
