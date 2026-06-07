"""
server.py — Servidor HTTP local que mantém o índice do vault QUENTE.

Para vaults grandes, recarregar/parsear a cada chamada custa caro mesmo com
cache. Este servidor (stdlib `http.server`, sem dependências novas) carrega o
VaultIndex uma vez e o mantém em memória; cada `retrieve` reusa o índice quente
e o `reindex` faz refresh incremental.

Pensado para ser falado por um plugin/hook do Obsidian rodando na mesma máquina
(localhost). Não tem autenticação — não exponha em rede pública.

Endpoints:
    GET  /health             → {"ok": true}
    GET  /stats              → estatísticas do índice
    POST /reindex            → refresh incremental, devolve stats
    POST /retrieve           → ContextPack JSON
         body: {"query": "...", "top_notes": 8, "hops": 1, "max_chars": 12000,
                "semantic": false}
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rlm.obsidian_rag.embeddings import EmbeddingProvider, get_embedder
from rlm.obsidian_rag.index import VaultIndex
from rlm.obsidian_rag.retriever import retrieve


class RagService:
    """Mantém o índice quente e serializa acessos com um lock."""

    def __init__(
        self,
        vault_path: str,
        *,
        workers: int | None = None,
        max_chunk_chars: int = 1200,
        embedder: EmbeddingProvider | None = None,
        semantic_weight: float = 0.5,
    ):
        self.vault_path = vault_path
        self.workers = workers
        self.max_chunk_chars = max_chunk_chars
        self.embedder = embedder
        self.semantic_weight = semantic_weight
        self._lock = threading.Lock()
        self.index = self._load()

    def _load(self) -> VaultIndex:
        idx = VaultIndex.from_vault(
            self.vault_path, workers=self.workers, max_chunk_chars=self.max_chunk_chars
        )
        if self.embedder is not None:
            idx.ensure_vectors(self.embedder)
        return idx

    def reindex(self) -> dict:
        with self._lock:
            self.index = self._load()
            return self.index.stats()

    def stats(self) -> dict:
        with self._lock:
            return self.index.stats()

    def retrieve(self, body: dict) -> dict:
        query = str(body.get("query", "")).strip()
        if not query:
            raise ValueError("campo 'query' é obrigatório")
        use_semantic = bool(body.get("semantic", self.embedder is not None))
        with self._lock:
            pack = retrieve(
                self.index,
                query,
                top_notes=int(body.get("top_notes", 8)),
                hops=int(body.get("hops", 1)),
                chunks_per_note=int(body.get("chunks_per_note", 3)),
                graph_weight=float(body.get("graph_weight", 0.6)),
                semantic_weight=self.semantic_weight if use_semantic else 0.0,
                embedder=self.embedder if use_semantic else None,
                max_chars=int(body.get("max_chars", 12000)),
            )
        return pack.to_dict()


def _make_handler(service: RagService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silencia o log padrão (vai p/ stderr só se erro)
            pass

        def _send(self, code: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8")) if raw else {}

        def do_GET(self):  # noqa: N802 (assinatura da stdlib)
            if self.path.rstrip("/") == "/health":
                self._send(200, {"ok": True})
            elif self.path.rstrip("/") == "/stats":
                self._send(200, service.stats())
            else:
                self._send(404, {"error": "not found", "path": self.path})

        def do_POST(self):  # noqa: N802
            try:
                path = self.path.rstrip("/")
                if path == "/reindex":
                    self._send(200, service.reindex())
                elif path == "/retrieve":
                    self._send(200, service.retrieve(self._read_body()))
                else:
                    self._send(404, {"error": "not found", "path": self.path})
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"error": str(exc), "type": type(exc).__name__})
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": str(exc), "type": type(exc).__name__})

    return Handler


def serve(
    vault_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    workers: int | None = None,
    max_chunk_chars: int = 1200,
    embedder: EmbeddingProvider | None = None,
    semantic_weight: float = 0.5,
    log=print,
) -> None:
    """Sobe o servidor (bloqueante) com o índice quente já carregado."""
    service = RagService(
        vault_path,
        workers=workers,
        max_chunk_chars=max_chunk_chars,
        embedder=embedder,
        semantic_weight=semantic_weight,
    )
    httpd = ThreadingHTTPServer((host, port), _make_handler(service))
    log(f"[obsidian_rag] servindo {vault_path} em http://{host}:{port}  {service.index.stats()}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


__all__ = ["RagService", "serve", "get_embedder"]
