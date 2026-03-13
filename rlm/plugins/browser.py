"""
Browser Plugin — rlm/plugins/browser.py

Tier B Plugin: funções injetadas diretamente no namespace REPL.
Diferente de SKILL.md (contexto documentação), estas funções ficam disponíveis
como variáveis globais do REPL sem qualquer import necessário:

    html = web_get("https://example.com")
    resultados = web_search("python asyncio", max_results=5)
    dados = web_scrape("https://example.com")
    resp = web_post("https://api.example.com/v1/endpoint", json={"key": "val"})
    path = web_download("https://example.com/file.csv", dest="/tmp/file.csv")

Injetado em rlm/core/rlm.py via make_browser_globals().

Dependências:
    - stdlib apenas (urllib, html.parser) → funciona sem extras
    - requests + beautifulsoup4 → funcionalidade completa (auto-detectado)

Fallback gracioso: se requests não estiver instalado, usa urllib.
Se beautifulsoup4 não estiver, usa html.parser stdlib para scraping básico.
"""
from __future__ import annotations

import html as _html_module
import json
import os
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError


# ---------------------------------------------------------------------------
# Sentinel — detectar requests e bs4 em runtime
# ---------------------------------------------------------------------------

def _have_requests() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


def _have_bs4() -> bool:
    try:
        import bs4  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Helpers internos (stdlib)
# ---------------------------------------------------------------------------

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; RLM-Agent/1.0; +https://github.com/mit-rlm)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


class _TextExtractor(HTMLParser):
    """Parser HTML mínimo para extrair texto e links (stdlib fallback)."""

    def __init__(self):
        super().__init__()
        self._texts: list[str] = []
        self._links: list[dict[str, str]] = []
        self._in_script = False
        self._in_style = False
        self._title = ""
        self._in_title = False
        self._current_href: str | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attrs_dict = dict(attrs)
        if tag == "script":
            self._in_script = True
        elif tag == "style":
            self._in_style = True
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and "href" in attrs_dict:
            self._current_href = attrs_dict["href"] or ""
            self._current_link_text = []

    def handle_endtag(self, tag: str):
        if tag == "script":
            self._in_script = False
        elif tag == "style":
            self._in_style = False
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._current_href is not None:
            text = " ".join(self._current_link_text).strip()
            if text:
                self._links.append({"text": text, "href": self._current_href})
            self._current_href = None
            self._current_link_text = []

    def handle_data(self, data: str):
        if self._in_script or self._in_style:
            return
        stripped = data.strip()
        if not stripped:
            return
        if self._in_title:
            self._title += stripped
        if self._current_href is not None:
            self._current_link_text.append(stripped)
        else:
            self._texts.append(stripped)

    @property
    def text(self) -> str:
        return " ".join(self._texts)

    @property
    def title(self) -> str:
        return self._title

    @property
    def links(self) -> list[dict[str, str]]:
        return self._links


def _urllib_get(url: str, headers: dict | None = None, timeout: int = 20) -> tuple[int, str]:
    """GET via stdlib urllib. Retorna (status_code, body_text)."""
    merged = {**_DEFAULT_HEADERS, **(headers or {})}
    req = urllib.request.Request(url, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = "utf-8"
            ct = resp.headers.get_content_charset()
            if ct:
                charset = ct
            return resp.status, resp.read().decode(charset, errors="replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except URLError as e:
        raise ConnectionError(f"web_get failed: {e.reason}") from e


# ---------------------------------------------------------------------------
# Phase 9.3 (Gap B): External content sanitizer
# ---------------------------------------------------------------------------

def _sanitize_external_content(text: str, source: str = "") -> str:
    """
    Scans external web content for prompt injection patterns before it reaches the LLM.

    Strategy (non-blocking — always returns something useful):
    - Clean content : returned unchanged (zero overhead on the happy path).
    - HIGH severity : attack phrases STRIPPED and replaced with [INJEÇÃO REMOVIDA:label].
                      Surrounding content is preserved so legitimate info survives.
    - MEDIUM / LOW  : warning prefix prepended + phrases stripped.

    Lazy import of security module: if security.py is unavailable (e.g., stripped
    deployment) the function silently fails open and returns the original text.
    This guarantees the browser plugin never breaks even if the security layer changes.
    """
    if not text:
        return text
    try:
        from rlm.core.security import auditor as _sec_auditor
    except Exception:
        return text  # security module unavailable → fail open

    report = _sec_auditor.audit_input(text, session_id=f"web:{source[:60]}")
    if not report.is_suspicious:
        return text  # clean — fast path, no overhead

    if report.threat_level == "high":
        # Return sanitized version only (attack phrases stripped, rest preserved)
        return report.sanitized_text

    # medium / low: prefix warning + strip phrases
    prefix = (
        f"[⚠️ CONTEÚADO EXTERNO — padrões suspeitos: "
        f"{', '.join(report.patterns_found)}]\n"
    )
    return prefix + report.sanitized_text


# ---------------------------------------------------------------------------
# Funções públicas expostas ao REPL
# ---------------------------------------------------------------------------

def web_get(url: str, headers: dict | None = None, timeout: int = 20) -> str:
    """
    Faz um GET HTTP e retorna o corpo como string.

    Args:
        url:     URL completa (ex: "https://example.com/page")
        headers: Dict com headers HTTP adicionais (ex: {"Authorization": "Bearer TOKEN"})
        timeout: Timeout em segundos (padrão 20)

    Returns:
        Corpo da resposta como string (HTML, JSON, texto).

    Raises:
        ConnectionError: Se a requisição falhar (DNS, timeout, etc.)
        RuntimeError:    Se status HTTP >= 400

    Exemplos:
        html = web_get("https://example.com")
        data = web_get("https://api.example.com/v1/items",
                       headers={"Authorization": "Bearer mytoken"})
    """
    if _have_requests():
        import requests
        r = requests.get(url, headers={**_DEFAULT_HEADERS, **(headers or {})},
                         timeout=timeout)
        r.raise_for_status()
        return _sanitize_external_content(r.text, source=url)  # Phase 9.3
    else:
        status, body = _urllib_get(url, headers=headers, timeout=timeout)
        if status >= 400:
            raise RuntimeError(f"HTTP {status}: {body[:200]}")
        return _sanitize_external_content(body, source=url)  # Phase 9.3


def web_post(
    url: str,
    data: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    timeout: int = 20,
) -> dict | str:
    """
    Faz um POST HTTP.

    Args:
        url:       URL completa
        data:      Form data (application/x-www-form-urlencoded)
        json_body: JSON body (application/json) — use este OU data
        headers:   Headers HTTP adicionais
        timeout:   Timeout em segundos (padrão 20)

    Returns:
        Dict se resposta for JSON, str caso contrário.

    Exemplos:
        resp = web_post("https://api.example.com/v1/create",
                        json_body={"name": "test", "value": 42})
        resp = web_post("https://form.example.com/submit",
                        data={"field1": "value1"})
    """
    merged_headers = {**_DEFAULT_HEADERS, **(headers or {})}

    if _have_requests():
        import requests
        if json_body is not None:
            r = requests.post(url, json=json_body, headers=merged_headers, timeout=timeout)
        else:
            r = requests.post(url, data=data, headers=merged_headers, timeout=timeout)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return r.text
    else:
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            merged_headers["Content-Type"] = "application/json"
        elif data is not None:
            body_bytes = urllib.parse.urlencode(data).encode("utf-8")
            merged_headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body_bytes = b""
        req = urllib.request.Request(url, data=body_bytes, headers=merged_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return body


def web_scrape(url: str, timeout: int = 20) -> dict[str, Any]:
    """
    Extrai conteúdo estruturado de uma página HTML.

    Args:
        url:     URL da página
        timeout: Timeout em segundos (padrão 20)

    Returns:
        Dict com:
        - title (str): título da página
        - text  (str): texto visível (sem tags, scripts, estilos)
        - links (list[dict]): lista de {"text": str, "href": str}

    Exemplos:
        dados = web_scrape("https://news.ycombinator.com")
        print(dados["title"])
        for link in dados["links"][:10]:
            print(link["text"], link["href"])
    """
    html_body = web_get(url, timeout=timeout)

    if _have_bs4():
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_body, "html.parser")

        # Remove scripts e styles
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title = soup.find("title")
        title_text = title.get_text(strip=True) if title else ""

        # Texto limpo
        body_tag = soup.find("body") or soup
        text = " ".join(body_tag.get_text(" ", strip=True).split())

        # Links
        links = [
            {"text": a.get_text(strip=True), "href": a.get("href", "")}
            for a in soup.find_all("a", href=True)
            if a.get_text(strip=True)
        ]
        return {"title": title_text, "text": _sanitize_external_content(text[:5000], source=url), "links": links[:50]}  # Phase 9.3
    else:
        parser = _TextExtractor()
        parser.feed(html_body)
        return {
            "title": parser.title,
            "text": _sanitize_external_content(parser.text[:5000], source=url),  # Phase 9.3
            "links": parser.links[:50],
        }


def web_search(query: str, max_results: int = 8) -> list[dict[str, str]]:
    """
    Pesquisa na web via DuckDuckGo Instant Answer API (sem API key).

    Args:
        query:       Texto de pesquisa
        max_results: Máximo de resultados (padrão 8)

    Returns:
        Lista de dicts: {"title": str, "url": str, "snippet": str}

    Nota:
        DuckDuckGo IA retorna resultados de RelatedTopics — pode ter poucos
        resultados para queries muito específicas. Para resultados Google completos,
        configure SERPAPI_KEY e use serpapi_search() da skill web_search.

    Exemplos:
        resultados = web_search("python asyncio tutorial", max_results=5)
        for r in resultados:
            print(r["title"])
            print(r["url"])
            print(r["snippet"])
            print()
    """
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
    }
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(params)

    try:
        _, body = _urllib_get(url, timeout=15)
        data = json.loads(body)
    except Exception as e:
        return [{"title": "Erro na pesquisa", "url": "", "snippet": str(e)}]

    results: list[dict[str, str]] = []

    # Resultado "Abstract" principal
    if data.get("AbstractText") and data.get("AbstractURL"):
        results.append({
            "title": data.get("Heading", query),
            "url": data["AbstractURL"],
            "snippet": data["AbstractText"][:300],
        })

    # RelatedTopics
    for topic in data.get("RelatedTopics", []):
        if len(results) >= max_results:
            break
        # Pode ser grupo de tópicos (tem "Topics" key) ou item direto
        if "Topics" in topic:
            for sub in topic["Topics"]:
                if len(results) >= max_results:
                    break
                if "Text" in sub and "FirstURL" in sub:
                    results.append({
                        "title": sub["Text"][:100],
                        "url": sub["FirstURL"],
                        "snippet": sub["Text"][:300],
                    })
        elif "Text" in topic and "FirstURL" in topic:
            results.append({
                "title": topic["Text"][:100],
                "url": topic["FirstURL"],
                "snippet": topic["Text"][:300],
            })

    # Se DuckDuckGo não retornou nada, tenta scrape da página de resultados
    if not results:
        try:
            resultados_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            dados = web_scrape(resultados_url, timeout=15)
            # Filtra links de resultado
            links_resultado = [
                l for l in dados["links"]
                if "duckduckgo.com" not in l["href"]
                and l["href"].startswith("http")
                and l["text"].strip()
            ][:max_results]
            for l in links_resultado:
                results.append({"title": l["text"], "url": l["href"], "snippet": ""})
        except Exception:
            pass

    # Phase 9.3 (Gap B): Sanitize snippets before returning external search results to LLM
    results = [
        {**r, "snippet": _sanitize_external_content(r.get("snippet", ""), source="web_search")}
        for r in results
    ]
    return results[:max_results]


def web_download(url: str, dest: str, chunk_size: int = 8192, timeout: int = 60) -> str:
    """
    Baixa um arquivo de uma URL para disco.

    Args:
        url:        URL do arquivo
        dest:       Caminho de destino (ex: "/tmp/arquivo.csv")
        chunk_size: Tamanho do chunk em bytes (padrão 8192)
        timeout:    Timeout em segundos (padrão 60)

    Returns:
        Caminho absoluto do arquivo baixado.

    Exemplos:
        path = web_download("https://example.com/data.csv", "/tmp/data.csv")
        import pandas as pd
        df = pd.read_csv(path)
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

    if _have_requests():
        import requests
        with requests.get(url, headers=_DEFAULT_HEADERS, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
    else:
        req = urllib.request.Request(url, headers=_DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)

    return os.path.abspath(dest)


# ---------------------------------------------------------------------------
# Factory pública — injeta globals no REPL
# ---------------------------------------------------------------------------

def make_browser_globals() -> dict[str, Any]:
    """
    Retorna dict de funções para injetar em environment.globals.

    Chamado em rlm/core/rlm.py durante completion():
        if hasattr(environment, "globals"):
            environment.globals.update(make_browser_globals())

    Funções disponíveis no REPL após injeção:
        web_get(url, headers=None, timeout=20) → str
        web_post(url, data=None, json_body=None, headers=None, timeout=20) → dict|str
        web_scrape(url, timeout=20) → dict
        web_search(query, max_results=8) → list[dict]
        web_download(url, dest, timeout=60) → str
    """
    return {
        "web_get": web_get,
        "web_post": web_post,
        "web_scrape": web_scrape,
        "web_search": web_search,
        "web_download": web_download,
    }


# ---------------------------------------------------------------------------
# Plugin Manifest
# ---------------------------------------------------------------------------

try:
    from rlm.plugins import PluginManifest
except ImportError:
    from dataclasses import dataclass as _dc, field as _field

    @_dc
    class PluginManifest:  # type: ignore
        name: str = ""
        version: str = ""
        description: str = ""
        functions: list = _field(default_factory=list)
        author: str = ""
        requires: list = _field(default_factory=list)


MANIFEST = PluginManifest(
    name="browser",
    version="1.0.0",
    description=(
        "HTTP/web browser globals: web_get, web_post, web_scrape, web_search, web_download. "
        "Injected directly into REPL namespace — no import needed. "
        "Stdlib-only fallback when requests/bs4 unavailable."
    ),
    functions=["web_get", "web_post", "web_scrape", "web_search", "web_download"],
    author="RLM Engine",
    requires=[],  # stdlib only; requests+bs4 optional upgrade
)
