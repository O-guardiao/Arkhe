# Global Knowledge Base — Memória Persistente Cross-Session com Retrieval Progressivo

**Data:** 2026-03-27
**Status:** Proposta Arquitetural
**Autor:** Análise do codebase + direcionamento do proprietário

---

## 1. O Problema (sem rodeios)

O cenário concreto:

```
27/03 — Sessão A: Desenvolvemos workflow X. Diagnosticamos bug Y. Corrigimos com Z.
30/03 — Sessão B: Workflow X quebra novamente. RLM abre sessão nova.
         → Não sabe que workflow X existe
         → Não sabe que bug Y já foi diagnosticado
         → Não sabe que correção Z já foi aplicada
         → Recalcula TUDO do zero
```

**Causa raiz no código:**
- `inject_memory_with_budget()` em `memory_budget.py` passa `session_id=self._session_id`
- `search_hybrid()` filtra com `WHERE session_id = ?`
- Resultado: **cada sessão é um universo isolado**. Memórias da sessão A são invisíveis na sessão B.

**O sistema atual é amnésico entre sessões.** O `memory_mini_agent.py` extrai nuggets perfeitos, avalia importância, constrói grafos — mas tudo isso morre quando a sessão fecha.

---

## 2. Diagnóstico: O Que Está Certo e O Que Está Errado

### ✅ Certo no sistema atual:
- Score tripartito (recência × importância × relevância) — academicamente sólido
- Budget gate (30% dos tokens) — necessário para não explodir contexto
- Hot cache com 1-turn lag — prático e rápido
- Mini agent com GPT-4.1-nano para extração — custo-benefício excelente
- Sanitização de injeção (security) — essencial

### ❌ Errado:
- Memória morre com a sessão
- Sem hierarquia de profundidade — tudo é chunk plano de ~400 chars
- Sem indexação por tópico/domínio — busca é puramente semântica
- Obsidian existe apenas no documento de arquitetura, não no código

---

## 3. Arquitetura Proposta: Knowledge Base Global com Retrieval Progressivo

### 3.1 Princípio Central: Memória em 3 Camadas de Profundidade

Inspirado em como humanos lembram:
1. **Sei que sei** — "ah, workflow X, já lidamos com isso" (título + tags)
2. **Lembro o essencial** — "o bug era race condition no lock, corrigimos com mutex" (resumo)
3. **Preciso dos detalhes** — "o código exato era..." (contexto completo)

```
┌─────────────────────────────────────────────────────────────┐
│                    PROMPT DO RLM                             │
│                                                              │
│  [CONHECIMENTO RELEVANTE]                                    │
│    1. Workflow Deploy CI/CD (score: 0.87)                    │
│       Resume: Pipeline falhou por race condition no job      │
│       de lock. Corrigido com mutex em deploy.yaml L42.       │
│       Bug reapareceu quando parallelism > 2.                 │
│    2. ...                                                    │
│  [FIM]                                                       │
│                                                              │
│  [HISTÓRICO COMPACTADO] ...                                  │
│  Usuário: o workflow de deploy quebrou de novo               │
└─────────────────────────────────────────────────────────────┘
```

O RLM recebe o **resumo** por padrão. Se precisar de mais, tem a tool `kb_get_full_context(doc_id)` para puxar o contexto completo sob demanda.

### 3.2 Schema do Knowledge Base Global

```sql
-- Banco GLOBAL: rlm_knowledge_base.db (NÃO é por sessão)
-- Localização: rlm_states/global/knowledge_base.db

CREATE TABLE kb_documents (
    id              TEXT PRIMARY KEY,           -- UUID
    title           TEXT NOT NULL,              -- "Workflow Deploy CI/CD — Race Condition"
    summary         TEXT NOT NULL,              -- 1-3 frases, ~100-200 tokens
    full_context    TEXT NOT NULL,              -- Detalhes completos, sem limite
    tags            TEXT DEFAULT '[]',          -- JSON array: ["workflow", "ci-cd", "deploy", "race-condition"]
    domain          TEXT DEFAULT 'general',     -- Categoria: "devops", "python", "architecture", etc.
    importance      REAL DEFAULT 0.5,           -- 0.0-1.0, atribuído pelo mini agent
    status          TEXT DEFAULT 'active',      -- 'active', 'superseded', 'deprecated'
    superseded_by   TEXT,                       -- ID do documento que substituiu este
    source_sessions TEXT DEFAULT '[]',          -- JSON array de session_ids que contribuíram
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    embedding_title TEXT DEFAULT '[]',          -- Embedding do título (busca rápida)
    embedding_summary TEXT DEFAULT '[]'         -- Embedding do resumo (busca semântica)
);

-- FTS5 para busca lexical rápida no título + summary
CREATE VIRTUAL TABLE kb_fts USING fts5(
    id UNINDEXED,
    title,
    summary,
    tags,
    tokenize="porter"
);

-- Relações entre documentos (grafo de conhecimento)
CREATE TABLE kb_edges (
    id          TEXT PRIMARY KEY,
    from_id     TEXT NOT NULL REFERENCES kb_documents(id),
    to_id       TEXT NOT NULL REFERENCES kb_documents(id),
    edge_type   TEXT NOT NULL,     -- 'supersedes', 'relates', 'contradicts', 'extends', 'caused_by'
    confidence  REAL DEFAULT 1.0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_kb_domain ON kb_documents(domain);
CREATE INDEX idx_kb_status ON kb_documents(status);
CREATE INDEX idx_kb_importance ON kb_documents(importance DESC);
```

### 3.3 Por Que 3 Campos de Texto, Não 1

| Campo | Tamanho típico | Tokens | Quando é lido |
|---|---|---|---|
| `title` | 5-15 palavras | ~10-20 | **SEMPRE** — indexação e matching |
| `summary` | 1-3 frases | ~50-150 | **Top-K matches** — injetado no prompt |
| `full_context` | Parágrafos a páginas | ~500-5000 | **Sob demanda** — tool call do RLM |

**Custo de token por turno com 5 matches:**
- Só títulos: ~100 tokens (inútil, sem contexto suficiente)
- Títulos + resumos: ~500-800 tokens (ideal para 80% dos casos)
- Títulos + resumos + 1 contexto completo: ~1500-3000 tokens (para os 20% complexos)

Comparado com o sistema atual que injeta chunks de 400 chars sem hierarquia (budget de 30% × 8000 = 2400 tokens), o retrieval progressivo é **mais eficiente em tokens E mais informativo**.

---

## 4. Pipeline de Escrita: Session → Global KB

### 4.1 Quando uma sessão fecha (ou a cada N turnos)

```
Sessão fecha / checkpoint a cada 10 turnos
  └→ kb_consolidate(session_id)               # Novo módulo: knowledge_consolidator.py
       │
       ├── 1. Coleta todos os nuggets da sessão (memory_chunks WHERE session_id = ?)
       │
       ├── 2. Agrupa nuggets por tópico (GPT-4.1-nano — clustering semântico)
       │     Input: lista de nuggets
       │     Output: clusters [{topic: "...", nuggets: [...]}]
       │
       ├── 3. Para cada cluster:
       │     ├── Gera title (10-15 palavras descritivas)
       │     ├── Gera summary (1-3 frases com decisões + outcomes)
       │     ├── Gera full_context (concatenação formatada dos nuggets + contexto)
       │     ├── Gera tags (5-10 tags descritivas)
       │     ├── Atribui domain (categoria principal)
       │     └── Atribui importance (score 0.0-1.0)
       │
       ├── 4. Busca documentos KB existentes que colidem (search_hybrid no KB)
       │     ├── Se colidiu → MERGE: atualiza documento existente
       │     │   - Concatena full_context
       │     │   - Regenera summary
       │     │   - Atualiza tags
       │     │   - Max(importance_old, importance_new)
       │     │   - Adiciona session_id ao source_sessions
       │     └── Se não colidiu → INSERT: novo documento
       │
       └── 5. Mirror para Obsidian (fire-and-forget)
             └── Escreve .md com frontmatter YAML no vault
```

### 4.2 Formato do Mirror para Obsidian

```markdown
---
id: kb-2026-03-27-workflow-deploy
title: "Workflow Deploy CI/CD — Race Condition em Parallelism"
domain: devops
tags: [workflow, ci-cd, deploy, race-condition, mutex]
importance: 0.85
status: active
sessions: [sess-20260327-001]
created: 2026-03-27T14:30:00
updated: 2026-03-27T14:30:00
---

## Resumo
Pipeline de deploy falhava intermitentemente quando parallelism > 2.
Causa-raiz: race condition no job de lock do mutex.
Correção: serialização dos jobs de lock em deploy.yaml (linha 42).

## Contexto Completo
[... detalhes completos, código, logs, decisões ...]

## Sessões Relacionadas
- [[sess-20260327-001]] — Diagnóstico inicial e correção
```

**O Obsidian aqui é espelho de leitura para humanos, não fonte de dados para o RLM.** A fonte de verdade é `knowledge_base.db`. Isso é consistente com a decisão arquitetural original do documento (§9.1).

Porém — e aqui está o ponto — o Obsidian vault pode ter **notas editadas manualmente por você**. Notas na pasta `conceitos/` podem ser importadas para o KB na boot. Isso dá a você uma interface de escrita humana para alimentar o sistema.

---

## 5. Pipeline de Leitura: KB → Prompt

### 5.1 Retrieval Progressivo em _build_prompt()

```python
def _build_prompt(self, user_message: str) -> str:
    parts = []

    # ── FASE 1: Knowledge Base Global (cross-session) ──────────
    if self._knowledge_base is not None:
        kb_block = self._retrieve_from_kb(user_message, available_tokens=1200)
        if kb_block:
            parts.append(kb_block)

    # ── FASE 2: Memória da sessão atual (como hoje) ────────────
    if self._memory is not None:
        # Budget ajustado: KB usou parte, sessão usa o resto
        session_budget = max(500, 2400 - len(kb_block) * 0.25)
        mem_block = self.build_memory_block(user_message, available_tokens=session_budget)
        if mem_block:
            parts.append(mem_block)

    # ... (resumo compactado + hot turns + user message, como hoje)
```

### 5.2 Retrieval Progressivo Detalhado

```python
def _retrieve_from_kb(self, query: str, available_tokens: int) -> str:
    """
    Retrieval em 2 estágios:
      Stage 1: Busca títulos + summaries (barato em tokens)
      Stage 2: Se algum match é altamente relevante, injeta o summary no prompt

    O full_context NÃO é injetado automaticamente.
    Ele fica disponível via tool kb_get_full_context(doc_id) para o RLM pedir.
    """
    # 1. Busca hybrid no KB (título + summary embeddings)
    candidates = self._knowledge_base.search_hybrid(
        query, limit=10, status='active'
    )

    # 2. Score tripartito adaptado para KB
    scored = []
    for doc in candidates:
        score = kb_score_tripartite(doc)  # recência + importância + relevância
        if score >= KB_SCORE_THRESHOLD:   # threshold mais alto que sessão (0.40)
            scored.append((score, doc))

    scored.sort(reverse=True)

    # 3. Injeta títulos + summaries dos top-K dentro do budget
    lines = ["[CONHECIMENTO PERSISTENTE — base de conhecimento cross-session]"]
    tokens_used = 0
    injected_ids = []

    for score, doc in scored:
        entry = f"• {doc['title']} (relevância: {score:.0%})\n  {doc['summary']}"
        entry_tokens = len(entry) * 0.25
        if tokens_used + entry_tokens > available_tokens:
            break
        lines.append(entry)
        tokens_used += entry_tokens
        injected_ids.append(doc['id'])

    if len(lines) == 1:
        return ""  # nada relevante encontrado

    lines.append("[FIM DO CONHECIMENTO PERSISTENTE]")
    lines.append(f"(Para detalhes completos, use: kb_get_full_context(doc_id))")

    return "\n".join(lines)
```

### 5.3 Tool para Contexto Completo Sob Demanda

```python
# Nova tool exposta ao RLM no REPL namespace
def kb_get_full_context(doc_id: str) -> str:
    """
    Recupera o contexto completo de um documento do Knowledge Base.

    Use quando o resumo injetado automaticamente não tem detalhes suficientes
    para resolver o problema atual. Retorna o texto completo com código,
    logs e decisões tomadas.
    """
    doc = knowledge_base.get_document(doc_id)
    if doc is None:
        return f"Documento '{doc_id}' não encontrado no Knowledge Base."

    return (
        f"[CONTEXTO COMPLETO — {doc['title']}]\n"
        f"Domínio: {doc['domain']} | Tags: {', '.join(doc['tags'])}\n"
        f"Criado: {doc['created_at']} | Atualizado: {doc['updated_at']}\n"
        f"Sessões: {', '.join(doc['source_sessions'])}\n\n"
        f"{doc['full_context']}\n"
        f"[FIM DO CONTEXTO COMPLETO]"
    )
```

---

## 6. Decisões de Design e Trade-offs

### 6.1 Por que NÃO "lembrar múltiplas sessões diretamente"

O usuário levantou a dúvida: "não sei se lembrar de múltiplas sessões é o caminho certo."

**Está certo em duvidar.** Simplesmente remover o filtro `session_id` do `search_hybrid()` cria 3 problemas:

1. **Poluição de contexto:** Sessão sobre Python injeta chunks de uma sessão sobre Docker
2. **Crescimento O(n):** Com 100 sessões, `search_hybrid()` faz cosine similarity contra TODOS os chunks
3. **Sem hierarquia:** Chunks de sessões diferentes têm granularidade inconsistente

A solução correta é **consolidar** o conhecimento de sessões em documentos estruturados (KB), não misturar chunks crus entre sessões.

### 6.2 Por que 2 bancos (session memory.db + global knowledge_base.db)

| Aspecto | Session memory.db | Global knowledge_base.db |
|---|---|---|
| Escopo | 1 sessão | Todas as sessões |
| Granularidade | Nuggets (~1 frase) | Documentos (~1 tópico) |
| Ciclo de vida | Morre com a sessão* | Persiste para sempre |
| Velocidade | Hot cache <1ms | Busca ~5-20ms |
| Uso | Injeção automática no prompt | Injeção automática + tool sob demanda |

*Nota: os nuggets da sessão NÃO são deletados. Eles são **consolidados** no KB quando a sessão fecha, e depois ficam disponíveis como "fonte rastreável" no full_context.

### 6.3 Obsidian: Espelho + Interface de Escrita Humana

```
knowledge_base.db ──mirror──→ vault/conhecimento/{doc_id}.md  (RLM escrevendo)
vault/conceitos/*.md ──import──→ knowledge_base.db             (Humano escrevendo)
```

- **RLM → Obsidian:** Toda vez que o KB é atualizado, um .md espelho é escrito (fire-and-forget). Você pode ler, revisar, e debugar no Obsidian.
- **Humano → KB:** Notas na pasta `conceitos/` são importadas no boot. Você pode criar/editar fatos canônicos como "O servidor de produção é X", "Nunca use Y neste projeto" e o RLM absorve.

### 6.4 Vector DB: SQLite é suficiente?

**Para o volume atual, sim.** O `MultiVectorMemory` com cosine similarity em Python funciona até ~50K chunks. Acima disso, ChromaDB ou sqlite-vec oferecem indexação ANN (Approximate Nearest Neighbor) que é O(log n) em vez de O(n).

**Recomendação pragmática:**
1. **Fase 1:** KB global em SQLite (mesmo engine, zero dependência nova)
2. **Fase 2 (se > 10K docs):** Migrar para ChromaDB (drop-in, embedded, Python-native)
3. **Nunca:** Servidores dedicados tipo Pinecone/Weaviate — overkill para agente pessoal

---

## 7. Plano de Implementação

### Fase 1: Knowledge Base Core (~3 dias)
- [ ] `rlm/core/knowledge_base.py` — CRUD + search_hybrid para kb_documents
- [ ] Schema SQLite com FTS5
- [ ] `kb_score_tripartite()` adaptado para documentos (pesos diferentes)
- [ ] Testes unitários (schema, CRUD, search, score)

### Fase 2: Consolidador (~2 dias)
- [ ] `rlm/core/knowledge_consolidator.py` — converte nuggets de sessão → documentos KB
- [ ] Clustering de nuggets por tópico (GPT-4.1-nano)
- [ ] Detecção de merge com documentos existentes
- [ ] Hook em `RLMSession.close()` para trigger automático
- [ ] Checkpoint periódico (a cada 10 turnos) para sessões longas

### Fase 3: Retrieval Progressivo (~2 dias)
- [ ] Integrar `_retrieve_from_kb()` em `_build_prompt()`
- [ ] Tool `kb_get_full_context(doc_id)` no REPL namespace
- [ ] Tool `kb_search(query)` para busca manual
- [ ] Budget dinâmico: KB + sessão compartilham o budget total

### Fase 4: Obsidian Mirror (~1 dia)
- [ ] Mirror write: KB → vault/conhecimento/*.md (com frontmatter YAML)
- [ ] Import read: vault/conceitos/*.md → KB (no boot)
- [ ] MCP integration para VPS (Piotr1215/mcp-obsidian)

### Fase 5: Testes End-to-End (~1 dia)
- [ ] Teste: criar sessão A sobre "workflow X", fechar, abrir sessão B, perguntar sobre "workflow X"
- [ ] Teste: editar nota em conceitos/, reiniciar, verificar que KB absorveu
- [ ] Teste: merge de documentos quando tópico se repete
- [ ] Teste: full_context sob demanda via tool

---

## 8. Cenário Validador (O Teste de Fogo)

```
# 27/03/2026 — Sessão A
Usuário: "o workflow de deploy está falhando"
RLM: [diagnostica, descobre race condition, corrige]
→ Sessão fecha
→ Consolidador cria documento KB:
    title: "Workflow Deploy CI/CD — Race Condition em Parallelism > 2"
    summary: "Pipeline falhava intermitentemente com parallelism > 2.
              Causa: race condition no lock mutex. Fix: serialização em deploy.yaml L42."
    full_context: [logs, código, decisões, 15 nuggets consolidados]
    tags: ["workflow", "deploy", "race-condition", "mutex", "ci-cd"]
    importance: 0.85

# 30/03/2026 — Sessão B
Usuário: "o workflow de deploy quebrou de novo"
→ _build_prompt() busca KB com query "workflow de deploy quebrou"
→ Encontra documento com score 0.91 (alta relevância semântica + tags match)
→ Injeta no prompt:

  [CONHECIMENTO PERSISTENTE]
  • Workflow Deploy CI/CD — Race Condition em Parallelism > 2 (relevância: 91%)
    Pipeline falhava intermitentemente com parallelism > 2.
    Causa: race condition no lock mutex. Fix: serialização em deploy.yaml L42.
  [FIM]

RLM: "Isso parece relacionado ao problema que diagnosticamos em 27/03.
      A causa anterior era race condition no mutex com parallelism > 2.
      Vou verificar se a correção em deploy.yaml L42 foi mantida..."
→ Se precisar de detalhes: chama kb_get_full_context("kb-xxx")
→ Recebe o contexto completo com logs e código exato
```

**ISSO é o que "memória persistente" significa: não lembrar tudo, mas lembrar o suficiente para não recomeçar do zero.**

---

## 9. Sobre os Pontos do Usuário

> "não sei se lembrar de múltiplas sessões é o caminho certo"

**Correto.** Lembrar sessões é errado. Lembrar *conhecimento consolidado* é certo. Sessões são efêmeras por natureza — o que importa são as conclusões, decisões e artifacts que saíram delas.

> "titulo e sumario, baixa profundidade para sistema ler e identificar"

**Correto.** É exatamente o Stage 1 do retrieval progressivo. O sistema lê títulos + summaries (~50-150 tokens cada) para decidir o que é relevante. Isso é O(n) em tokens mas O(1) em decisão.

> "resumo breve, para a maioria dos casos pode resolver"

**Correto.** O summary é o "sweet spot" — informação suficiente para ~80% dos casos. É isso que fica no prompt por padrão.

> "contexto completo, para após ler título e sumário, ler resumo, e mesmo assim não teve contexto suficiente, rlm pode pedir contexto completo"

**Correto, com uma correção:** o título + sumário + resumo na proposta são 2 campos, não 3. O `summary` já serve como "resumo breve" e "identificador de relevância". A terceira camada é o `full_context`. Duas camadas de profundidade são suficientes para a leitura automática; a terceira é sob demanda (tool).

> "banco de dados vetoriais"

**Correto para escala.** SQLite + cosine em Python funciona até ~50K docs. Acima disso, embeddings precisam de indexação ANN (ChromaDB, sqlite-vec). Para o uso atual, SQLite é suficiente e evita dependência extra.

> "obsidian combinado"

**Correto como interface de leitura/debug.** Obsidian não deve ser o backend de retrieval (sem embeddings, sem busca semântica, muito lento). Mas é perfeito como espelho visual e como interface de escrita humana para `conceitos/`.
