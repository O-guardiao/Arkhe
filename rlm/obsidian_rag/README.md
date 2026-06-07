# Arkhe · Obsidian RAG-Hipergrafo

Motor de **RAG-hipergrafo focado em Obsidian**. Lê **muitos `.md` em paralelo
real**, monta um índice (BM25 + hipergrafo) e entrega um **context pack
pré-LLM** pronto para colar antes do prompt.

Não é conversacional. Ele é o **backend de um hook/plugin do Obsidian**: você
chama, recebe contexto, e *quem chama* faz a chamada ao LLM.

## Por que existe

O `vault_tools`/`ObsidianBridge` antigos liam o vault **sequencialmente**
(`for f in os.listdir(): open(f)`), o que é lento em vaults grandes. Este
módulo lê e parseia em **processos separados** (`ProcessPoolExecutor`), sem
disputa de GIL — o parsing escala com os núcleos da máquina.

## Arquitetura

```
vault/*.md ──► reader.py ──► parser.py ──► [Note, Note, ...]
              (ProcessPool,  (frontmatter,      │
               paralelo real) tags, wikilinks,  │
                              chunks)            │
                                                 ▼
                          index.py  ─►  BM25Index (bm25.py)
                          (cache em        +
                           .arkhe_rag/   HyperGraph (hypergraph.py)
                           incremental)         │
                                                 ▼
   query ──────────────────►  retriever.py  ──►  ContextPack (JSON + markdown)
                              BM25 → sementes → expansão no hipergrafo → budget
```

- **Hipergrafo**: hiperarestas conectam *conjuntos* de notas que compartilham
  uma tag, uma pasta ou a vizinhança de wikilinks. A expansão por ativação
  traz notas relacionadas pela **estrutura** do vault, não só pelo texto.
- **Cache incremental**: em `.arkhe_rag/index.json`. Runs seguintes só
  re-parseiam arquivos com `mtime` alterado.

## CLI (JSON no stdout)

```bash
# context pack completo (JSON)
python -m rlm.obsidian_rag retrieve --vault ~/Vault --query "o que é arkhe?"

# só o markdown pronto p/ colar no prompt
python -m rlm.obsidian_rag retrieve --vault ~/Vault --query "..." --markdown

# JSON em uma linha (mais fácil de parsear no plugin)
python -m rlm.obsidian_rag retrieve --vault ~/Vault --query "..." --compact

# (re)constrói o cache e mostra estatísticas
python -m rlm.obsidian_rag index --vault ~/Vault --workers 8
```

Flags úteis: `--top-notes`, `--chunks-per-note`, `--hops` (saltos no
hipergrafo), `--graph-weight`, `--max-chars` (orçamento), `--no-cache`,
`--workers`.

Toda saída de dados vai para **stdout** em JSON; logs vão para **stderr**.
Em erro, o stdout recebe `{"error": "...", "type": "..."}` e o exit code é `1`.

## Uso como biblioteca

```python
from rlm.obsidian_rag import VaultIndex, retrieve

index = VaultIndex.from_vault("/caminho/vault")   # paralelo + cache
pack = retrieve(index, "como funciona X?", top_notes=8, hops=1)
print(pack.context_markdown)        # contexto pré-LLM
for n in pack.notes:
    print(n.path, n.score, n.reasons)
```

## Integração com um plugin/hook do Obsidian (TypeScript)

Chame a CLI via `child_process` e injete `context_markdown` no seu prompt:

```ts
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const pexec = promisify(execFile);

async function preLlmContext(vault: string, query: string): Promise<string> {
  const { stdout } = await pexec("python", [
    "-m", "rlm.obsidian_rag", "retrieve",
    "--vault", vault,
    "--query", query,
    "--compact",
  ], { maxBuffer: 16 * 1024 * 1024 });
  const pack = JSON.parse(stdout);
  if (pack.error) throw new Error(`${pack.type}: ${pack.error}`);
  return pack.context_markdown;   // cole isto ANTES do prompt do usuário
}
```

Padrões de hook recomendados:

- **Ao abrir/salvar nota** ou **on-demand** (comando): rode `retrieve` com a
  seleção/título como `query` e mostre/cole o `context_markdown`.
- **Mantenha o cache quente**: rode `index` uma vez no boot do plugin; depois
  cada `retrieve` reusa o cache e só re-parseia o que mudou.
```

## Servidor HTTP local (índice quente)

Para evitar o custo de carregar/parsear a cada chamada (mesmo com cache), suba
um servidor que mantém o `VaultIndex` **em memória**:

```bash
python -m rlm.obsidian_rag serve --vault ~/Vault --port 8787
# com semântica ligada:
python -m rlm.obsidian_rag serve --vault ~/Vault --embedder hashing
```

Endpoints (localhost, sem auth — não exponha em rede pública):

| Método | Rota        | Corpo / Resposta                                   |
|--------|-------------|----------------------------------------------------|
| GET    | `/health`   | `{"ok": true}`                                     |
| GET    | `/stats`    | estatísticas do índice                             |
| POST   | `/reindex`  | refresh incremental → stats                        |
| POST   | `/retrieve` | `{"query": "...", "top_notes": 8, "semantic": true}` → ContextPack |

```ts
async function preLlmContext(query: string): Promise<string> {
  const r = await fetch("http://127.0.0.1:8787/retrieve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, top_notes: 8 }),
  });
  const pack = await r.json();
  return pack.context_markdown;
}
```

## Embeddings opcionais (retrieval semântico)

Por padrão o retrieval é **lexical (BM25) + estrutural (hipergrafo)** — zero
dependências externas, zero rede. Para recall semântico (achar notas que falam
do mesmo assunto com outras palavras), ligue um embedder:

```bash
# offline/determinístico (fallback lexical-vetorial, bom p/ testes)
python -m rlm.obsidian_rag retrieve --vault ~/Vault --query "..." --embedder hashing

# semântico de verdade (usa a dep `openai` + OPENAI_API_KEY)
python -m rlm.obsidian_rag retrieve --vault ~/Vault --query "..." \
    --embedder openai --semantic-weight 0.6
```

Os vetores são cacheados em `.arkhe_rag/vectors__<embedder>.json`, indexados por
**hash do conteúdo** do chunk — só o que muda é re-embeddado. O score final
combina `lexical_weight·BM25 + semantic_weight·cosseno + graph_weight·grafo`,
e cada nota traz a proveniência em `reasons` (`bm25`, `semantic`, `tag:x`,
`link:y`, `graph`).
