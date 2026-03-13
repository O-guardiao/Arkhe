+++
name = "notion"
description = "Base de conhecimento e gestão de tarefas via Notion API: cria/lê/atualiza páginas, consulta databases, gerencia tarefas, armazena memória estruturada longa. Use when: user asks to salvar no Notion, criar tarefa, atualizar projeto, consultar base de conhecimento, criar nota de reunião, adicionar item a tabela Notion. Requer Integration Token do Notion."
tags = ["notion", "tarefa", "nota", "base de conhecimento", "projeto", "documentar", "salvar", "notas", "página", "database notion", "kanban"]
priority = "contextual"

[sif]
signature = "notion.page(title: str, parent_id: str, props: dict = {}) -> dict"
prompt_hint = "Use para salvar conhecimento estruturado, criar nota, tarefa, página ou atualizar base no Notion."
short_sig = "notion.page(t,p,**kw)"
compose = ["shell", "web_search", "email", "calendar"]
examples_min = ["criar uma página ou tarefa estruturada no Notion"]

[runtime]
estimated_cost = 0.85
risk_level = "medium"
side_effects = ["remote_api_read", "remote_api_write"]
preconditions = ["env:NOTION_TOKEN"]
postconditions = ["notion_page_or_database_updated"]
fallback_policy = "draft_in_memory_or_ask_user"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "notion page database task note knowledge base project documentation"
example_queries = ["salve isso no Notion", "crie uma tarefa na base Notion"]

[requires]
bins = []
+++

# Notion Skill

Base de conhecimento, memória persistente e gestão de projetos via Notion API v1.

## Quando usar

✅ **USE quando:**
- "Salva esse relatório no Notion"
- "Cria uma tarefa: revisar proposta até sexta"
- "Lista meus projetos em andamento no Notion"
- "Adiciona reunião de hoje na minha base de notas"
- "Busca na minha base de conhecimento sobre 'arquitetura RLM'"
- "Atualiza status do projeto X para 'Concluído'"

## Setup

```python
import requests, os, json
from datetime import datetime

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DB_ID = os.environ.get("NOTION_DATABASE_ID", "")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def notion_get(path: str) -> dict:
    r = requests.get(f"https://api.notion.com/v1{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

def notion_post(path: str, payload: dict) -> dict:
    r = requests.post(
        f"https://api.notion.com/v1{path}",
        headers=HEADERS,
        data=json.dumps(payload),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

def notion_patch(path: str, payload: dict) -> dict:
    r = requests.patch(
        f"https://api.notion.com/v1{path}",
        headers=HEADERS,
        data=json.dumps(payload),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()
```

## Criar página simples (nota)

```python
def criar_nota(
    parent_page_id: str,
    titulo: str,
    conteudo: str,
    tags: list[str] = [],
) -> dict:
    """Cria página no Notion com título e texto."""
    blocos = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": paragrafo}}]
            },
        }
        for paragrafo in conteudo.split("\n\n")
        if paragrafo.strip()
    ]

    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": titulo}}]
            }
        },
        "children": blocos,
    }
    return notion_post("/pages", payload)

# Exemplo:
nova_pagina = criar_nota(
    "seu-page-id-aqui",
    titulo=f"Reunião — {datetime.now():%d/%m/%Y}",
    conteudo="Participantes: João, Maria, Pedro\n\nPontos discutidos:\n- Roadmap Q2\n- Budget aprovado\n\nAções:\n- João revisa proposta até sexta",
)
print(f"Criado: {nova_pagina['url']}")
```

## Adicionar item a Database (tabela)

```python
def criar_tarefa(
    database_id: str,
    nome: str,
    status: str = "A fazer",      # valor da propriedade Status
    prioridade: str = "Média",    # valor da propriedade Priority
    data_prazo: str | None = None,  # "2026-05-15"
    tags: list[str] = [],
) -> dict:
    """Cria item em database do Notion (adapta propriedades conforme seu schema)."""
    props: dict = {
        "Name": {
            "title": [{"type": "text", "text": {"content": nome}}]
        },
    }
    if status:
        props["Status"] = {"select": {"name": status}}
    if prioridade:
        props["Priority"] = {"select": {"name": prioridade}}
    if data_prazo:
        props["Due Date"] = {"date": {"start": data_prazo}}
    if tags:
        props["Tags"] = {"multi_select": [{"name": t} for t in tags]}

    return notion_post("/pages", {
        "parent": {"database_id": database_id},
        "properties": props,
    })

nova_tarefa = criar_tarefa(
    NOTION_DB_ID,
    nome="Revisar proposta cliente ABC",
    status="Em andamento",
    prioridade="Alta",
    data_prazo="2026-01-31",
    tags=["vendas", "urgente"],
)
print(f"Tarefa criada: {nova_tarefa['url']}")
```

## Consultar database (listar itens com filtro)

```python
def consultar_database(
    database_id: str,
    filtro: dict | None = None,
    ordenacao: list | None = None,
    limite: int = 20,
) -> list[dict]:
    """Consulta items de um database Notion."""
    payload: dict = {"page_size": limite}
    if filtro:
        payload["filter"] = filtro
    if ordenacao:
        payload["sorts"] = ordenacao

    resultado = notion_post(f"/databases/{database_id}/query", payload)
    items = []
    for page in resultado.get("results", []):
        props = page["properties"]
        item = {"id": page["id"], "url": page["url"]}
        for chave, valor in props.items():
            tipo = valor.get("type")
            if tipo == "title":
                item[chave] = "".join(t["plain_text"] for t in valor.get("title", []))
            elif tipo == "rich_text":
                item[chave] = "".join(t["plain_text"] for t in valor.get("rich_text", []))
            elif tipo == "select":
                item[chave] = (valor.get("select") or {}).get("name", "")
            elif tipo == "multi_select":
                item[chave] = [s["name"] for s in valor.get("multi_select", [])]
            elif tipo == "date":
                item[chave] = (valor.get("date") or {}).get("start", "")
            elif tipo == "checkbox":
                item[chave] = valor.get("checkbox", False)
            elif tipo == "number":
                item[chave] = valor.get("number")
        items.append(item)
    return items

# Listar tarefas com status "Em andamento"
tarefas = consultar_database(
    NOTION_DB_ID,
    filtro={"property": "Status", "select": {"equals": "Em andamento"}},
    ordenacao=[{"property": "Due Date", "direction": "ascending"}],
)
for t in tarefas:
    print(f"[{t.get('Priority','')}] {t.get('Name','')} — prazo: {t.get('Due Date','')}")
```

## Atualizar propriedades de página

```python
def atualizar_status(page_id: str, novo_status: str) -> dict:
    """Atualiza status de uma tarefa/página."""
    return notion_patch(f"/pages/{page_id}", {
        "properties": {
            "Status": {"select": {"name": novo_status}}
        }
    })

atualizar_status("page-id-exemplo", "Concluído")
```

## Buscar no workspace (Search)

```python
def buscar_notion(query: str, tipo: str = "page") -> list[dict]:
    """Busca páginas/databases no workspace por texto."""
    resultado = notion_post("/search", {
        "query": query,
        "filter": {"value": tipo, "property": "object"},
        "page_size": 10,
    })
    items = []
    for obj in resultado.get("results", []):
        titulo = ""
        props = obj.get("properties", {})
        for _, v in props.items():
            if v.get("type") == "title":
                titulo = "".join(t["plain_text"] for t in v.get("title", []))
                break
        items.append({
            "id":     obj["id"],
            "titulo": titulo or obj.get("title", [{}])[0].get("plain_text", "") if "title" in obj else titulo,
            "url":    obj.get("url", ""),
            "tipo":   obj["object"],
        })
    return items

resultados = buscar_notion("arquitetura RLM")
for r in resultados:
    print(f"{r['titulo']} — {r['url']}")
```

## Ler conteúdo de página

```python
def ler_pagina(page_id: str) -> str:
    """Lê blocos de texto de uma página e retorna como texto plano."""
    resultado = notion_get(f"/blocks/{page_id}/children")
    partes = []
    for bloco in resultado.get("results", []):
        tipo = bloco.get("type", "")
        dados = bloco.get(tipo, {})
        textos = dados.get("rich_text", [])
        texto  = "".join(t.get("plain_text", "") for t in textos)
        if texto:
            partes.append(texto)
    return "\n\n".join(partes)

conteudo = ler_pagina("page-id-exemplo")
print(conteudo[:500])
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `NOTION_TOKEN` | Integration Token (`secret_...`) |
| `NOTION_DATABASE_ID` | ID do database padrão de tarefas |

**Criar Integration:** [notion.so/my-integrations](https://www.notion.so/my-integrations) → New Integration → Internal → copiar token.
**Permissão:** abrir cada page/database no Notion → Share → adicionar a integration.
