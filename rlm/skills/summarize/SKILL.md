+++
name = "summarize"
description = "Summarize content from URLs, YouTube videos, PDFs, or raw text. Extracts and compresses web pages, transcriptions, and documents into concise summaries. Use when: user asks to resumir URL, extrair texto de site, sintetizar artigo, resumir YouTube, ou condensar documento. NOT for: web search (use web_search skill), local file reading (use filesystem skill)."
tags = ["resumir", "resumo", "sintetizar", "extrair texto", "artigo", "youtube", "url", "webpage", "sumarizar", "summarize", "pdf", "condensar", "digest"]
priority = "contextual"

[sif]
signature = "summarize_url(url: str, max_chars: int = 4000) -> dict"
prompt_hint = "Use para extrair e resumir o conteúdo de uma URL, artigo ou página web antes de analisar ou reportar."
short_sig = "summarize_url(url,max=4000)→{}"
compose = ["web_search", "browser", "notion", "email"]
examples_min = ["extrair e resumir o conteúdo principal de uma URL"]
impl = """
def summarize_url(url, max_chars=4000):
    import urllib.request, re, html as html_mod
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; RLMBot/1.0)'}
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read()
        charset = resp.headers.get_content_charset() or 'utf-8'
        page = raw.decode(charset, errors='replace')
    except Exception as e:
        return {"error": str(e), "url": url}
    title_m = re.search(r'<title[^>]*>(.*?)</title>', page, re.I | re.S)
    title = html_mod.unescape(title_m.group(1).strip()) if title_m else ''
    for tag in ['script', 'style', 'nav', 'footer', 'header', 'aside']:
        page = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', page, flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', page)
    text = html_mod.unescape(text)
    text = re.sub(r'\\s+', ' ', text).strip()
    text = text[:max_chars]
    return {"title": title, "url": url, "text": text, "chars": len(text)}
"""

[requires]
bins = []

[runtime]
estimated_cost = 0.3
risk_level = "low"
side_effects = ["http_request"]
postconditions = ["text_content_extracted"]
fallback_policy = "try_browser_or_reply_without_content"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "summarize extract text url webpage article youtube pdf digest content"
example_queries = ["resuma este artigo", "extraia o texto desta URL", "qual o conteúdo desta página"]
+++

# Summarize Skill

Extrai e resume conteúdo de URLs, artigos, YouTube e documentos.

## Quando usar

✅ **USE quando:**
- "Resuma este artigo: https://..."
- "Qual o conteúdo principal desta página?"
- "Extraia o texto deste site"
- "Me dê um resumo do que diz essa URL"
- "Compile os pontos principais deste link"

❌ **NÃO use quando:**
- Pesquisar na internet → use `web_search` skill
- Navegar interativamente (clique, login, SPA) → use `playwright` skill
- Ler arquivo local → use `filesystem` skill

## Função injetada no REPL

```python
# Extrair e resumir uma URL
resultado = summarize_url("https://arxiv.org/abs/2405.12345", max_chars=3000)
print(resultado["title"])
print(resultado["text"][:500])
```

## YouTube (transcrição manual)

```python
import subprocess, json

def youtube_transcript(video_id: str) -> str:
    """Extrai legenda/transcrição de um vídeo YouTube usando yt-dlp."""
    cmd = [
        "yt-dlp", "--skip-download", "--write-auto-sub",
        "--sub-lang", "pt,en", "--sub-format", "json3",
        "-o", "/tmp/yt_%(id)s",
        f"https://youtube.com/watch?v={video_id}"
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)
    
    # Procura arquivo de legenda gerado
    import glob
    files = glob.glob(f"/tmp/yt_{video_id}*.json3")
    if not files:
        return "Transcrição não disponível"
    
    with open(files[0]) as f:
        data = json.load(f)
    
    # Concatena segmentos
    texts = [e.get("segs", [{}])[0].get("utf8", "") 
             for e in data.get("events", []) if e.get("segs")]
    return " ".join(texts).strip()

transcript = youtube_transcript("dQw4w9WgXcQ")
print(transcript[:500])
```

## PDF local

```python
import subprocess

def pdf_to_text(path: str) -> str:
    """Extrai texto de PDF via pdftotext (poppler-utils)."""
    result = subprocess.run(
        ["pdftotext", "-layout", path, "-"],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout

texto = pdf_to_text("/tmp/paper.pdf")
print(texto[:2000])
```

## Resumo com sub_rlm

```python
conteudo = summarize_url("https://example.com/artigo")
prompt = f"Resuma em 5 tópicos:\n\n{conteudo['text'][:3000]}"
resumo = sub_rlm(prompt)
print(resumo)
```
