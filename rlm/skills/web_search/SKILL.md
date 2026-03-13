+++
name = "web_search"
description = "Search the web using DuckDuckGo (no API key) or SerpAPI/Brave Search (with key). Returns titles, URLs, and snippets. Use when: user asks to search for something online, find current information, research a topic, or look up recent events. NOT for: fetching a specific known URL (use browser skill), internal document search."
tags = ["pesquisar", "buscar", "pesquisa", "notícias", "informação", "google", "busca", "internet", "pesquisa online"]
priority = "always"

[sif]
signature = "web_search(query: str, max_results: int = 5) -> list[dict]"
prompt_hint = "Use para descobrir informação atual na internet, encontrar fontes ou pesquisar tema ainda aberto."
short_sig = "web_search(q,n=5)\u2192[{}]"
compose = ["browser", "playwright", "notion", "email"]
examples_min = ["pesquisar um tema atual e retornar fontes com resumo"]
codex = "lambda q,n=5: [r.get('Text','') for r in __import__('json').loads(__import__('urllib.request',fromlist=['x']).urlopen('https://api.duckduckgo.com/?q='+__import__('urllib.parse',fromlist=['x']).quote(q)+'&format=json',timeout=8).read()).get('RelatedTopics',[])[:n] if isinstance(r,dict) and r.get('Text')]"
impl = """
def web_search(query, max_results=5):
    import urllib.request, urllib.parse, json
    q = urllib.parse.quote_plus(query)
    url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&no_redirect=1"
    try:
        req = urllib.request.urlopen(url, timeout=10)
        data = json.loads(req.read())
    except Exception:
        return [{"error": "DuckDuckGo indispon\u00edvel", "query": query}]
    results = []
    for r in data.get("RelatedTopics", [])[:max_results]:
        if isinstance(r, dict) and "Text" in r:
            results.append({
                "title": r.get("Text", "")[:120],
                "url": r.get("FirstURL", ""),
                "snippet": r.get("Text", ""),
            })
    if not results and data.get("Abstract"):
        results.append({
            "title": data.get("Heading", query),
            "url": data.get("AbstractURL", ""),
            "snippet": data.get("Abstract", ""),
        })
    return results
"""

[requires]
bins = []

[runtime]
estimated_cost = 0.2
risk_level = "low"
side_effects = ["http_request"]
postconditions = ["search_results_returned"]
fallback_policy = "try_browser_or_reply_without_live_search"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "web search internet news research current information sources"
example_queries = ["pesquise isso na internet", "encontre fontes atuais sobre este tema"]
+++

# Web Search Skill

Pesquisa na internet sem precisar de API key (DuckDuckGo) ou com chave (SerpAPI, Brave).

## Quando usar

✅ **USE quando:**
- "Pesquisa sobre X"
- "Últimas notícias sobre Y"
- "Encontra o site oficial de Z"
- "O que é [termo técnico]?"
- Investigação de tópicos sem URL específica

❌ **NÃO use quando:**
- URL já é conhecida → use `browser` skill / `web_get(url)`
- Pesquisa em documentos locais → use `filesystem` skill

## Função injetada no REPL (disponível diretamente)

```python
# Sem API key — DuckDuckGo via scraping
resultados = web_search("python asyncio tutorial", max_results=5)

# Cada resultado:
# {"title": str, "url": str, "snippet": str}

for r in resultados:
    print(r["title"], r["url"])
    print(r["snippet"])
    print()

# Pegar conteúdo do primeiro resultado
primeiro_url = resultados[0]["url"]
conteudo = web_get(primeiro_url)
```

## DuckDuckGo manual (sem API key)

```python
import requests

def ddg_search(query: str, max_results: int = 10) -> list[dict]:
    """DuckDuckGo Instant Answer API — gratuito, sem key."""
    params = {"q": query, "format": "json", "no_html": 1}
    r = requests.get("https://api.duckduckgo.com/", params=params, timeout=15)
    data = r.json()
    
    results = []
    # RelatedTopics contém os resultados principais
    for topic in data.get("RelatedTopics", [])[:max_results]:
        if "Text" in topic and "FirstURL" in topic:
            results.append({
                "title": topic["Text"][:100],
                "url": topic["FirstURL"],
                "snippet": topic["Text"],
            })
    return results

resultados = ddg_search("machine learning papers 2025")
FINAL_VAR("resultados")
```

## SerpAPI (com API key — resultados Google reais)

```python
import requests, os

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

def serpapi_search(query: str, num: int = 10) -> list[dict]:
    params = {
        "engine": "google",
        "q": query,
        "num": num,
        "api_key": SERPAPI_KEY,
    }
    r = requests.get("https://serpapi.com/search", params=params, timeout=20)
    data = r.json()
    return [
        {"title": r["title"], "url": r["link"], "snippet": r.get("snippet", "")}
        for r in data.get("organic_results", [])
    ]

resultados = serpapi_search("RLM recursive language model MIT 2025")
FINAL_VAR("resultados")
```

## Brave Search API (com API key — privado, sem tracking)

```python
import requests, os

BRAVE_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "")

def brave_search(query: str, count: int = 10) -> list[dict]:
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_KEY}
    params = {"q": query, "count": count}
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                      headers=headers, params=params, timeout=15)
    data = r.json()
    return [
        {"title": w["title"], "url": w["url"], "snippet": w.get("description", "")}
        for w in data.get("web", {}).get("results", [])
    ]

resultados = brave_search("latest AI research 2026")
FINAL_VAR("resultados")
```

## Variáveis de ambiente

- `SERPAPI_KEY` — chave SerpAPI (opcional, plano gratuito 100 req/mês)
- `BRAVE_SEARCH_API_KEY` — chave Brave Search (opcional, plano gratuito 2000 req/mês)
