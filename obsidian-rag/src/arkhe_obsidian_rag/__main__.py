"""
__main__.py — CLI do motor Obsidian-RAG-hipergrafo.

Saída de dados sempre em JSON no STDOUT (logs vão para STDERR), para que um
plugin/hook do Obsidian possa invocar via child_process e parsear o stdout.

Comandos:
    retrieve  --vault PATH --query "..."   → ContextPack JSON (ou markdown)
    index     --vault PATH                 → (re)constrói cache, imprime stats
    stats     --vault PATH                 → estatísticas do índice

Exemplos:
    python -m arkhe_obsidian_rag retrieve --vault ~/Vault --query "o que é arkhe?"
    python -m arkhe_obsidian_rag retrieve --vault ~/Vault --query "..." --markdown
    python -m arkhe_obsidian_rag index --vault ~/Vault --workers 8
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from arkhe_obsidian_rag.embeddings import get_embedder
from arkhe_obsidian_rag.index import VaultIndex
from arkhe_obsidian_rag.retriever import retrieve


def _eprint(*args) -> None:
    print(*args, file=sys.stderr)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--vault", required=True, help="Caminho raiz do vault Obsidian.")
    p.add_argument(
        "--workers", type=int, default=None, help="Processos paralelos (default: nº de CPUs)."
    )
    p.add_argument("--max-chunk-chars", type=int, default=1200, help="Tamanho máx. de cada chunk.")
    p.add_argument("--no-cache", action="store_true", help="Ignora/não grava o cache em disco.")
    p.add_argument("--verbose", action="store_true", help="Logs de progresso no stderr.")


def _add_semantic(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--embedder",
        default="none",
        help="Backend semântico: none|hashing|hashing:N|openai|openai:modelo (default none).",
    )
    p.add_argument(
        "--semantic-weight", type=float, default=0.5, help="Peso do sinal semântico na mistura."
    )


def _load_index(args) -> VaultIndex:
    t0 = time.perf_counter()
    index = VaultIndex.from_vault(
        args.vault,
        workers=args.workers,
        max_chunk_chars=args.max_chunk_chars,
        use_cache=not args.no_cache,
    )
    if args.verbose:
        elapsed = (time.perf_counter() - t0) * 1000.0
        _eprint(f"[obsidian_rag] índice pronto em {elapsed:.0f}ms: {index.stats()}")
    return index


def cmd_retrieve(args) -> int:
    index = _load_index(args)
    embedder = get_embedder(args.embedder)
    pack = retrieve(
        index,
        args.query,
        top_notes=args.top_notes,
        hops=args.hops,
        max_chars=args.max_chars,
        chunks_per_note=args.chunks_per_note,
        graph_weight=args.graph_weight,
        semantic_weight=args.semantic_weight if embedder else 0.0,
        embedder=embedder,
    )
    if args.markdown:
        print(pack.context_markdown)
    else:
        json.dump(
            pack.to_dict(), sys.stdout, ensure_ascii=False, indent=None if args.compact else 2
        )
        sys.stdout.write("\n")
    return 0


def cmd_index(args) -> int:
    index = _load_index(args)
    json.dump(index.stats(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_stats(args) -> int:
    return cmd_index(args)


def cmd_serve(args) -> int:
    from arkhe_obsidian_rag.server import serve

    embedder = get_embedder(args.embedder)
    serve(
        args.vault,
        host=args.host,
        port=args.port,
        workers=args.workers,
        max_chunk_chars=args.max_chunk_chars,
        embedder=embedder,
        semantic_weight=args.semantic_weight,
        log=_eprint,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m arkhe_obsidian_rag",
        description="Motor RAG-hipergrafo para Obsidian (leitura paralela real, contexto pré-LLM).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ret = sub.add_parser("retrieve", help="Recupera contexto pré-LLM para uma query.")
    _add_common(p_ret)
    p_ret.add_argument("--query", required=True, help="Consulta em linguagem natural.")
    p_ret.add_argument("--top-notes", type=int, default=8)
    p_ret.add_argument("--chunks-per-note", type=int, default=3)
    p_ret.add_argument("--hops", type=int, default=1, help="Saltos de expansão no hipergrafo.")
    p_ret.add_argument("--graph-weight", type=float, default=0.6)
    p_ret.add_argument(
        "--max-chars", type=int, default=12000, help="Orçamento de caracteres do contexto."
    )
    p_ret.add_argument(
        "--markdown", action="store_true", help="Imprime só o markdown pronto p/ colar."
    )
    p_ret.add_argument("--compact", action="store_true", help="JSON sem indentação (uma linha).")
    _add_semantic(p_ret)
    p_ret.set_defaults(func=cmd_retrieve)

    p_idx = sub.add_parser("index", help="(Re)constrói o cache do índice e imprime stats.")
    _add_common(p_idx)
    p_idx.set_defaults(func=cmd_index)

    p_st = sub.add_parser("stats", help="Estatísticas do índice do vault.")
    _add_common(p_st)
    p_st.set_defaults(func=cmd_stats)

    p_srv = sub.add_parser("serve", help="Sobe servidor HTTP local com índice quente.")
    _add_common(p_srv)
    _add_semantic(p_srv)
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8787)
    p_srv.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # erro estruturado no stdout p/ o plugin tratar
        json.dump({"error": str(exc), "type": type(exc).__name__}, sys.stdout)
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
