"""
hypergraph.py — Hipergrafo de conhecimento sobre as notas do vault.

Diferente de um grafo comum (arestas entre PARES), uma hiperaresta conecta um
CONJUNTO de notas que compartilham algo: uma tag, uma pasta, ou a vizinhança
de wikilinks de uma nota. Isso captura comunidades de notas de uma vez só.

O retriever usa `expand()` (ativação por espalhamento) para, a partir das notas
semente trazidas pelo BM25, puxar notas vizinhas relevantes pela ESTRUTURA do
vault — não só pela sobreposição textual.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from rlm.obsidian_rag.models import HyperEdge, Note

# Pesos por tipo de hiperaresta (links explícitos > tags > pasta).
_TYPE_WEIGHT = {
    "links": 1.0,
    "cocitation": 0.9,
    "tag": 0.6,
    "folder": 0.3,
}
# Tags genéricas demais para virar hiperaresta útil.
_STOP_TAGS = {"todo", "draft", "inbox", "wip", "note", "notes"}


def _build_resolver(notes: list[Note]) -> dict[str, str]:
    """
    Mapeia alvo de wikilink -> caminho real da nota.

    Indexa por caminho-sem-extensão e por basename (Obsidian linka por nome).
    Em colisão de basename, o primeiro vence (determinístico por ordenação).
    """
    resolver: dict[str, str] = {}
    for n in sorted(notes, key=lambda x: x.path):
        key_path = n.path[:-3] if n.path.endswith(".md") else n.path
        resolver.setdefault(key_path.lower(), n.path)
        resolver.setdefault(n.basename.lower(), n.path)
        if n.title:
            resolver.setdefault(n.title.lower(), n.path)
    return resolver


def build_hypergraph(notes: list[Note]) -> HyperGraph:
    """Constrói todas as hiperarestas (tag, folder, links) a partir das notas."""
    paths = {n.path for n in notes}
    resolver = _build_resolver(notes)
    edges: list[HyperEdge] = []

    # --- tag: todas as notas que compartilham uma tag ---
    by_tag: dict[str, list[str]] = defaultdict(list)
    for n in notes:
        for tag in n.tags:
            if tag.lower() not in _STOP_TAGS:
                by_tag[tag].append(n.path)
    for tag, members in by_tag.items():
        if len(members) >= 2:
            edges.append(
                HyperEdge(
                    id=f"tag::{tag}",
                    type="tag",
                    label=tag,
                    members=tuple(sorted(members)),
                    weight=_TYPE_WEIGHT["tag"],
                )
            )

    # --- folder: notas na mesma pasta ---
    by_folder: dict[str, list[str]] = defaultdict(list)
    for n in notes:
        by_folder[n.folder].append(n.path)
    for folder, members in by_folder.items():
        if len(members) >= 2:
            edges.append(
                HyperEdge(
                    id=f"folder::{folder or '/'}",
                    type="folder",
                    label=folder or "(raiz)",
                    members=tuple(sorted(members)),
                    weight=_TYPE_WEIGHT["folder"],
                )
            )

    # --- links: a vizinhança de saída de cada nota (nota + seus alvos) ---
    for n in notes:
        targets: list[str] = []
        for link in n.wikilinks:
            resolved = resolver.get(link.lower())
            if resolved and resolved != n.path and resolved in paths:
                targets.append(resolved)
        if targets:
            members = tuple(sorted({n.path, *targets}))
            edges.append(
                HyperEdge(
                    id=f"links::{n.path}",
                    type="links",
                    label=n.basename,
                    members=members,
                    weight=_TYPE_WEIGHT["links"],
                )
            )

    return HyperGraph(edges)


class HyperGraph:
    """Hipergrafo com índice nó->arestas e expansão por ativação."""

    def __init__(self, edges: list[HyperEdge]):
        self.edges = edges
        self._node_edges: dict[str, list[int]] = defaultdict(list)
        for i, e in enumerate(edges):
            for member in e.members:
                self._node_edges[member].append(i)

    @property
    def nodes(self) -> set[str]:
        return set(self._node_edges.keys())

    def neighbors(self, path: str) -> dict[str, float]:
        """Vizinhos diretos de uma nota e o peso acumulado da conexão."""
        out: dict[str, float] = defaultdict(float)
        for ei in self._node_edges.get(path, ()):
            edge = self.edges[ei]
            # normaliza pelo tamanho: hiperarestas grandes diluem o sinal
            contrib = edge.weight / (len(edge.members) - 1 or 1)
            for member in edge.members:
                if member != path:
                    out[member] += contrib
        return dict(out)

    def expand(
        self,
        seeds: dict[str, float],
        *,
        hops: int = 1,
        decay: float = 0.5,
        max_neighbors: int = 64,
    ) -> dict[str, float]:
        """
        Ativação por espalhamento: a partir das notas semente (path->peso),
        propaga score para vizinhos por `hops` saltos, com decaimento.

        Retorna SÓ as notas adicionadas (sem as sementes), com seus scores
        de grafo. O retriever combina isso com o score lexical.
        """
        activation: dict[str, float] = defaultdict(float)
        frontier = dict(seeds)
        for _ in range(max(0, hops)):
            next_frontier: dict[str, float] = defaultdict(float)
            for path, weight in frontier.items():
                for neigh, link_w in self.neighbors(path).items():
                    if neigh in seeds:
                        continue
                    gain = weight * link_w * decay
                    activation[neigh] += gain
                    next_frontier[neigh] += gain
            if not next_frontier:
                break
            # mantém só a fronteira mais ativa (controla explosão combinatória)
            frontier = dict(
                sorted(next_frontier.items(), key=lambda kv: kv[1], reverse=True)[:max_neighbors]
            )
        return dict(activation)

    def edges_for(self, path: str) -> list[HyperEdge]:
        return [self.edges[i] for i in self._node_edges.get(path, ())]

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        for e in self.edges:
            by_type[e.type] += 1
        return {
            "hyperedges": len(self.edges),
            "nodes": len(self._node_edges),
            "by_type": dict(by_type),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"edges": [e.to_dict() for e in self.edges]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HyperGraph:
        return cls([HyperEdge.from_dict(e) for e in d.get("edges", [])])
