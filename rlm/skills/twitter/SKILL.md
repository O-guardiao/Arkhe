+++
name = "twitter"
description = "Interage com Twitter/X via API v2: posta tweets, busca por keyword, monitora menções, lê timeline, envia DMs, segue/deixa de seguir, gerencia listas. Use when: user asks to postar no Twitter, buscar tweets sobre tema, monitorar menções de marca, fazer thread, coletar dados de redes sociais. Requer OAuth 2.0 ou Bearer Token do Twitter Developer Portal."
tags = ["twitter", "tweet", "x.com", "tuitar", "rede social twitter", "thread", "menções", "timeline"]
priority = "lazy"

[requires]
bins = []

[sif]
signature = "twitter.post(text: str, reply_to: str | None = None) -> dict"
prompt_hint = "Use para postar, buscar ou monitorar conteúdo no Twitter/X e acompanhar menções ou tópicos."
short_sig = "twitter.post(txt)→{}"
compose = ["web_search", "browser"]
examples_min = ["postar tweet ou monitorar menções de um tema"]

[runtime]
estimated_cost = 0.7
risk_level = "high"
side_effects = ["remote_api_read", "remote_api_write", "social_post"]
postconditions = ["twitter_content_posted_or_retrieved"]
fallback_policy = "prepare_draft_or_use_web_search"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "twitter x social media tweet thread mention timeline trend"
example_queries = ["poste no Twitter", "busque tweets sobre um assunto"]
+++

# Twitter / X Skill

Automação e análise no Twitter/X via API v2.

## Quando usar

✅ **USE quando:**
- "Posta um tweet sobre lançamento do produto X"
- "Busca tweets sobre 'copa do mundo 2026' das últimas 12h"
- "Monitora menções ao @minha_marca"
- "Cria uma thread com os 5 pontos do relatório"
- "Me mostra trending topics do Brasil"

❌ **NÃO use quando:**
- Analytics profundo → use Twitter Analytics direto
- Anúncios → use Twitter Ads API (diferente)
- Acesso a dados históricos > 7 dias → requer Academic Research track

## Setup de autenticação

```python
import requests, os, base64, urllib.parse

# === OAuth 2.0 App-only (Bearer Token — leitura) ===
BEARER_TOKEN = os.environ.get("TWITTER_BEARER_TOKEN", "")

def auth_headers_readonly() -> dict:
    return {"Authorization": f"Bearer {BEARER_TOKEN}"}

# === OAuth 1.0a (User context — para postar/DM) ===
import hmac, hashlib, time, random, string

TW_API_KEY    = os.environ.get("TWITTER_API_KEY", "")
TW_API_SECRET = os.environ.get("TWITTER_API_SECRET", "")
TW_TOKEN      = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TW_TOKEN_SEC  = os.environ.get("TWITTER_ACCESS_SECRET", "")

def oauth1_header(method: str, url: str, params: dict) -> str:
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    ts    = str(int(time.time()))
    oauth_params = {
        "oauth_consumer_key":     TW_API_KEY,
        "oauth_nonce":            nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        ts,
        "oauth_token":            TW_TOKEN,
        "oauth_version":          "1.0",
    }
    all_params = {**params, **oauth_params}
    param_str  = "&".join(f"{urllib.parse.quote(k,'')  }={urllib.parse.quote(str(v),'')}"
                          for k, v in sorted(all_params.items()))
    base_str   = "&".join([
        method.upper(),
        urllib.parse.quote(url, ""),
        urllib.parse.quote(param_str, ""),
    ])
    signing_key = f"{urllib.parse.quote(TW_API_SECRET,''    )}&{urllib.parse.quote(TW_TOKEN_SEC, '')}"
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base_str.encode(), hashlib.sha1).digest()
    ).decode()
    oauth_params["oauth_signature"] = sig
    header_parts = ", ".join(
        f'{urllib.parse.quote(k, "")}="{urllib.parse.quote(str(v), "")}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_parts}"
```

## Postar tweet

```python
import json, requests

def postar_tweet(texto: str, reply_to_id: str | None = None) -> dict:
    """Posta tweet. reply_to_id para responder a um tweet existente."""
    url     = "https://api.twitter.com/2/tweets"
    payload = {"text": texto}
    if reply_to_id:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to_id}

    body_str = json.dumps(payload, separators=(",", ":"))
    auth_header = oauth1_header("POST", url, {})
    r = requests.post(
        url,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
        },
        data=body_str,
        timeout=15,
    )
    return r.json()

resultado = postar_tweet("🚀 RLM v10 lançado! Nova skill de maps, travel, twitter e muito mais. #AI #AGI")
print(f"Tweet ID: {resultado['data']['id']}")
```

## Criar thread (sequência de tweets)

```python
def criar_thread(tweets: list[str]) -> list[dict]:
    """
    Cria uma thread postando cada tweet como resposta ao anterior.
    tweets: lista de textos (máx 280 chars cada)
    """
    resultados = []
    reply_id   = None
    for texto in tweets:
        resultado = postar_tweet(texto[:280], reply_to_id=reply_id)
        if "data" in resultado:
            reply_id = resultado["data"]["id"]
        resultados.append(resultado)
    return resultados

thread = criar_thread([
    "1/5 — Por que o RLM é mais eficiente que sistemas convencionais? 🧵",
    "2/5 — Arquitetura modular: skills carregadas sob demanda, zero overhead desnecessário.",
    "3/5 — Sub-agentes paralelos: tasks independentes executam simultaneamente.",
    "4/5 — Memoria compactada: histórico de 8k tokens com preservação semântica.",
    "5/5 — Open source. Disponível em: github.com/seu-repo #OpenSource #AI",
])
```

## Buscar tweets por keyword (últimas 7 dias)

```python
def buscar_tweets(
    query: str,
    max_results: int = 10,
    idioma: str | None = "pt",
    excluir_retweets: bool = True,
) -> list[dict]:
    """Busca tweets recentes. Bearer Token apenas (read-only)."""
    if excluir_retweets:
        query += " -is:retweet"
    if idioma:
        query += f" lang:{idioma}"

    r = requests.get(
        "https://api.twitter.com/2/tweets/search/recent",
        headers=auth_headers_readonly(),
        params={
            "query": query,
            "max_results": min(max_results, 100),
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "name,username",
        },
        timeout=15,
    )
    data = r.json()
    tweets     = data.get("data", [])
    users_map  = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    return [
        {
            "id":           t["id"],
            "texto":        t["text"],
            "criado_em":    t["created_at"],
            "autor":        users_map.get(t["author_id"], {}).get("username", ""),
            "likes":        t["public_metrics"].get("like_count", 0),
            "retweets":     t["public_metrics"].get("retweet_count", 0),
        }
        for t in tweets
    ]

tweets = buscar_tweets("inteligência artificial brasil", max_results=5)
for t in tweets:
    print(f"@{t['autor']}: {t['texto'][:100]}... ({t['likes']} likes)")
```

## Monitorar menções

```python
def buscar_mencoes(username: str, max_results: int = 20) -> list[dict]:
    """Busca tweets que mencionam @username."""
    return buscar_tweets(f"@{username}", max_results=max_results, idioma=None, excluir_retweets=False)

mencoes = buscar_mencoes("seu_bot", max_results=10)
for m in mencoes:
    print(f"[@{m['autor']}] {m['texto'][:120]}")
```

## Enviar DM

```python
def enviar_dm(recipient_id: str, texto: str) -> dict:
    """Envia Direct Message para um usuário pelo ID."""
    url = "https://api.twitter.com/2/dm_conversations/with/{}/messages".format(recipient_id)
    payload = {"text": texto}
    body_str = json.dumps(payload, separators=(",", ":"))
    auth_header = oauth1_header("POST", url, {})
    r = requests.post(
        url,
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
        data=body_str,
        timeout=15,
    )
    return r.json()
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `TWITTER_BEARER_TOKEN` | Bearer Token (leitura) — obrigatório para busca |
| `TWITTER_API_KEY` | API Key do App |
| `TWITTER_API_SECRET` | API Key Secret |
| `TWITTER_ACCESS_TOKEN` | Access Token do usuário (escrita) |
| `TWITTER_ACCESS_SECRET` | Access Token Secret |

Registrar em: [developer.twitter.com](https://developer.twitter.com) → Create Project → Free tier: 1500 tweets/mês escrita, busca básica.
