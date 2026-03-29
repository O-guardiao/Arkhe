"""
obsidian_bridge.py — Event-driven bridge KB ↔ Obsidian vault.

Substitui obsidian_mirror.py com:
- Export event-driven (hooks on_doc_created / on_doc_updated / on_edge_created)
- Session audit logs
- Import melhorado com hash tracking (detecta edições humanas)
- Knowledge graph export (Mermaid)
- Auto-MOCs por domínio
- Conflict detection e resolution sync

O KB SQLite FTS5 permanece a fonte de verdade.
O vault é uma view materializada + interface de curadoria humana.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from typing import Any, Callable, Dict, List, Optional

from rlm.core.structured_log import get_logger

_log = get_logger("obsidian_bridge")

# ---------------------------------------------------------------------------
# Vault directory structure
# ---------------------------------------------------------------------------
_DIRS = (
    "conhecimento",   # KB docs exported
    "sessoes",        # Session audit logs
    "conceitos",      # Human-curated concepts → imported to KB
    "conflitos",      # Contradictions for human resolution
    "conflitos/resolvidos",
    "moc",            # Auto-generated MOCs by domain
    "grafos",         # Mermaid/dot knowledge graph
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_filename(title: str, max_len: int = 80) -> str:
    """Sanitize title for filesystem usage."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)
    return name[:max_len].strip().rstrip(".")


def _file_hash(filepath: str) -> str:
    """SHA-256 do conteúdo de um arquivo."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_hash(text: str) -> str:
    """SHA-256 de uma string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter delimitado por ---."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not match:
        return {}, content
    fm_text = match.group(1)
    body = match.group(2)
    fm: dict = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                try:
                    fm[key] = json.loads(value)
                except json.JSONDecodeError:
                    fm[key] = value
            elif value.replace(".", "", 1).isdigit():
                fm[key] = float(value) if "." in value else int(value)
            else:
                fm[key] = value
    return fm, body


def _split_body(body: str) -> tuple[str, str]:
    """Separa body em summary + full_context."""
    resumo = re.search(r"##\s*Resumo\s*\n(.*?)(?=\n##|\Z)", body, re.DOTALL)
    if resumo:
        summary = resumo.group(1).strip()
        ctx = re.search(r"##\s*Contexto\s+Completo\s*\n(.*?)(?=\n##|\Z)", body, re.DOTALL)
        full_context = ctx.group(1).strip() if ctx else body.strip()
        return summary, full_context
    paragraphs = body.strip().split("\n\n")
    if paragraphs:
        summary = paragraphs[0].strip()
        full_context = "\n\n".join(paragraphs[1:]).strip() if len(paragraphs) > 1 else ""
        return summary, full_context
    return body.strip()[:500], body.strip()


def _extract_human_notes(body: str) -> Optional[str]:
    """Extrai seção '## Notas Humanas' do body, se existir."""
    match = re.search(r"##\s*Notas\s+Humanas\s*\n(.*?)(?=\n##|\Z)", body, re.DOTALL)
    if match:
        text = match.group(1).strip()
        # Ignora se é apenas o placeholder de comentário HTML
        cleaned = re.sub(r"<!--.*?-->", "", text).strip()
        return cleaned if cleaned else None
    return None


def _extract_wikilinks(body: str) -> List[str]:
    """Extrai todos os wikilinks [[target]] do body."""
    return re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]", body)


# ---------------------------------------------------------------------------
# ObsidianBridge
# ---------------------------------------------------------------------------


class ObsidianBridge:
    """
    Ponte bidirecional event-driven entre GlobalKnowledgeBase e Obsidian vault.

    Export (RLM → Vault):
        Hooks registrados no KB disparam escrita no vault automaticamente.

    Import (Vault → RLM):
        sync_all() lê edições humanas e propaga de volta ao KB.
    """

    def __init__(self, vault_path: str, kb: Any):
        """
        Args:
            vault_path: Caminho raiz do vault Obsidian.
            kb: Instância de GlobalKnowledgeBase.
        """
        self.vault_path = os.path.abspath(vault_path)
        self.kb = kb
        self._setup_directories()
        self._init_sync_db()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_directories(self) -> None:
        """Cria a estrutura de diretórios do vault se não existir."""
        for d in _DIRS:
            os.makedirs(os.path.join(self.vault_path, d), exist_ok=True)

    def _init_sync_db(self) -> None:
        """Cria tabela vault_sync no DB do KB para hash tracking."""
        with closing(sqlite3.connect(self.kb.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vault_sync (
                    doc_id          TEXT PRIMARY KEY,
                    file_path       TEXT NOT NULL,
                    file_hash       TEXT NOT NULL DEFAULT '',
                    human_notes_hash TEXT DEFAULT '',
                    last_synced     DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    # ------------------------------------------------------------------
    # EXPORT: KB → Vault
    # ------------------------------------------------------------------

    def on_doc_created(self, doc_id: str) -> str:
        """Hook: KB.add_document → escreve vault/conhecimento/{title}.md"""
        doc = self.kb.get_document(doc_id)
        if not doc:
            return ""
        return self._write_doc_to_vault(doc)

    def on_doc_updated(self, doc_id: str) -> str:
        """Hook: KB.update_document → atualiza vault/conhecimento/{title}.md"""
        doc = self.kb.get_document(doc_id)
        if not doc:
            return ""
        return self._write_doc_to_vault(doc)

    def on_edge_created(self, from_id: str, to_id: str, edge_type: str) -> None:
        """Hook: KB.add_edge → atualiza seção Relacionamentos em ambos os docs."""
        for did in (from_id, to_id):
            doc = self.kb.get_document(did)
            if doc:
                self._write_doc_to_vault(doc)

    def export_all(self) -> List[str]:
        """Exporta todos os documentos ativos do KB para o vault."""
        docs = self.kb.list_documents(status="active", limit=1000)
        paths = []
        for doc_summary in docs:
            full_doc = self.kb.get_document(doc_summary["id"])
            if full_doc:
                path = self._write_doc_to_vault(full_doc)
                paths.append(path)
        self._regenerate_index()
        return paths

    def export_session_log(self, session_data: dict) -> str:
        """
        Cria vault/sessoes/YYYY-MM-DD_{session_id}.md com audit trail.

        Args:
            session_data: dict com chaves:
                session_id, client_id, created_at, status,
                total_completions, total_tokens_used,
                prompt (opcional), iterations (opcional, lista de dicts),
                final_output (opcional), kb_docs_created (opcional)
        """
        sid = session_data.get("session_id", "unknown")
        date_prefix = time.strftime("%Y-%m-%d")
        filename = f"{date_prefix}_{sid[:12]}.md"
        filepath = os.path.join(self.vault_path, "sessoes", filename)

        model = session_data.get("model", "unknown")
        created = session_data.get("created_at", "")
        status = session_data.get("status", "completed")
        completions = session_data.get("total_completions", 0)
        tokens = session_data.get("total_tokens_used", 0)
        prompt = session_data.get("prompt", "")
        iterations = session_data.get("iterations", [])
        final_output = session_data.get("final_output", "")
        kb_docs = session_data.get("kb_docs_created", [])

        lines = [
            "---",
            f"session_id: {sid}",
            f"model: {model}",
            f"started: {created}",
            f"status: {status}",
            f"total_completions: {completions}",
            f"total_tokens: {tokens}",
            f"kb_docs_created: {json.dumps(kb_docs)}",
            "---",
            "",
            f"# Sessão {sid[:12]}",
            "",
        ]

        if prompt:
            lines += ["## Prompt Original", f"> {prompt[:500]}", ""]

        if iterations:
            for i, it in enumerate(iterations, 1):
                lines.append(f"## Iteração {i}")
                if it.get("repl_code"):
                    lines += ["```python", it["repl_code"], "```"]
                if it.get("output"):
                    lines += [f"**Output:** {it['output'][:300]}", ""]

        if final_output:
            lines += ["## FINAL", final_output, ""]

        if kb_docs:
            lines.append("## Documentos KB Gerados")
            for did in kb_docs:
                lines.append(f"- [[{did}]]")
            lines.append("")

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        _log.debug(f"Session log exported: {filepath}")
        return filepath

    # ------------------------------------------------------------------
    # IMPORT: Vault → KB
    # ------------------------------------------------------------------

    def sync_conceitos(self) -> List[str]:
        """Importa vault/conceitos/*.md → KB (com hash tracking, skip duplicatas)."""
        conceitos_dir = os.path.join(self.vault_path, "conceitos")
        if not os.path.isdir(conceitos_dir):
            return []

        imported: List[str] = []
        for filename in os.listdir(conceitos_dir):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(conceitos_dir, filename)
            try:
                doc_id = self._import_single_conceito(filepath)
                if doc_id:
                    imported.append(doc_id)
            except Exception as exc:
                _log.warn(f"Import falhou para {filename}: {exc}")
                continue
        return imported

    def sync_corrections(self) -> dict:
        """
        Detecta edições humanas em conhecimento/*.md via hash comparison.
        Sincroniza: frontmatter (importance/domain/tags) + seção Notas Humanas.

        Returns:
            {"metadata_updated": [...], "human_notes_merged": [...]}
        """
        result: dict = {"metadata_updated": [], "human_notes_merged": []}
        conhecimento_dir = os.path.join(self.vault_path, "conhecimento")
        if not os.path.isdir(conhecimento_dir):
            return result

        for filename in os.listdir(conhecimento_dir):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(conhecimento_dir, filename)
            try:
                self._sync_single_correction(filepath, result)
            except Exception as exc:
                _log.warn(f"Sync correction falhou para {filename}: {exc}")
        return result

    def sync_conflict_resolutions(self) -> List[dict]:
        """
        Lê conflitos resolvidos → atualiza KB (deprecar perdedor).
        Arquivo de conflito com frontmatter 'resolucao: doc_id_vencedor'.
        """
        resolved = []
        conflicts_dir = os.path.join(self.vault_path, "conflitos")
        resolved_dir = os.path.join(self.vault_path, "conflitos", "resolvidos")

        if not os.path.isdir(conflicts_dir):
            return resolved

        for filename in os.listdir(conflicts_dir):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(conflicts_dir, filename)
            if not os.path.isfile(filepath):
                continue

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                fm, _ = _parse_frontmatter(content)
                resolucao = fm.get("resolucao", "")
                if not resolucao:
                    continue

                winner_id = str(resolucao).strip('"').strip("'")
                loser_id = str(fm.get("loser_id", "")).strip('"').strip("'")

                if winner_id and loser_id:
                    self.kb.deprecate_document(loser_id, superseded_by=winner_id)
                    # Move para resolvidos
                    dest = os.path.join(resolved_dir, filename)
                    os.rename(filepath, dest)
                    resolved.append({
                        "winner": winner_id,
                        "loser": loser_id,
                        "file": filename,
                    })
                    _log.info(f"Conflict resolved: {winner_id} wins over {loser_id}")
            except Exception as exc:
                _log.warn(f"Conflict resolution falhou para {filename}: {exc}")
        return resolved

    def sync_wikilinks_as_edges(self) -> int:
        """
        Lê wikilinks manuais de notas em conhecimento/ → cria edges no KB.
        Wikilinks humanos ganham confidence=0.8 (acima do default 0.5 da IA).
        Returns: número de edges criados.
        """
        edges_created = 0
        conhecimento_dir = os.path.join(self.vault_path, "conhecimento")
        if not os.path.isdir(conhecimento_dir):
            return 0

        # Indexa títulos → doc_ids
        title_to_id: dict[str, str] = {}
        docs = self.kb.list_documents(status="active", limit=1000)
        for d in docs:
            title_to_id[d["title"].lower()] = d["id"]

        existing_edges = self._get_existing_edges()

        for filename in os.listdir(conhecimento_dir):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(conhecimento_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                fm, body = _parse_frontmatter(content)
                doc_id = str(fm.get("id", ""))
                if not doc_id:
                    continue

                # Extrai wikilinks do body (excluindo seção Sessões Relacionadas)
                # Remove seção de sessões para evitar falsos positivos
                clean_body = re.sub(
                    r"##\s*Sessões\s+Relacionadas.*?(?=\n##|\Z)", "",
                    body, flags=re.DOTALL,
                )
                links = _extract_wikilinks(clean_body)
                for target_title in links:
                    target_id = title_to_id.get(target_title.lower())
                    if target_id and target_id != doc_id:
                        edge_key = (doc_id, target_id, "related")
                        if edge_key not in existing_edges:
                            self.kb.add_edge(doc_id, target_id, "related", confidence=0.8)
                            existing_edges.add(edge_key)
                            edges_created += 1
            except Exception:
                continue
        return edges_created

    def sync_all(self) -> dict:
        """Executa full sync: conceitos + corrections + conflicts + wikilinks."""
        stats: dict = {}
        try:
            imported = self.sync_conceitos()
            stats["conceitos_imported"] = len(imported)
        except Exception as exc:
            _log.warn(f"sync_conceitos falhou: {exc}")
            stats["conceitos_imported"] = 0

        try:
            corrections = self.sync_corrections()
            stats["metadata_updated"] = len(corrections.get("metadata_updated", []))
            stats["human_notes_merged"] = len(corrections.get("human_notes_merged", []))
        except Exception as exc:
            _log.warn(f"sync_corrections falhou: {exc}")
            stats["metadata_updated"] = 0
            stats["human_notes_merged"] = 0

        try:
            resolutions = self.sync_conflict_resolutions()
            stats["conflicts_resolved"] = len(resolutions)
        except Exception as exc:
            _log.warn(f"sync_conflict_resolutions falhou: {exc}")
            stats["conflicts_resolved"] = 0

        try:
            stats["edges_from_wikilinks"] = self.sync_wikilinks_as_edges()
        except Exception as exc:
            _log.warn(f"sync_wikilinks_as_edges falhou: {exc}")
            stats["edges_from_wikilinks"] = 0

        return stats

    # ------------------------------------------------------------------
    # Knowledge Graph Export
    # ------------------------------------------------------------------

    def export_knowledge_graph(self) -> str:
        """Exporta grafo completo como Mermaid → vault/grafos/knowledge_graph.md."""
        filepath = os.path.join(self.vault_path, "grafos", "knowledge_graph.md")

        # Coleta todos os edges
        with closing(sqlite3.connect(self.kb.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            edges = conn.execute(
                "SELECT from_id, to_id, edge_type, confidence FROM kb_edges"
            ).fetchall()

        # Coleta títulos
        docs = self.kb.list_documents(status="active", limit=1000)
        id_to_title: dict[str, str] = {}
        for d in docs:
            safe = d["title"].replace('"', "'")[:40]
            id_to_title[d["id"]] = safe

        edge_styles = {
            "causes": "-->|causes|",
            "fixes": "-->|fixes|",
            "related": "---|related|",
            "contradicts": "-..->|contradicts|",
            "supports": "-->|supports|",
        }

        mermaid_lines = ["```mermaid", "graph TD"]
        node_ids: dict[str, str] = {}
        counter = 0

        for edge in edges:
            fid, tid = edge["from_id"], edge["to_id"]
            for did in (fid, tid):
                if did not in node_ids:
                    node_ids[did] = f"N{counter}"
                    counter += 1
                    title = id_to_title.get(did, did[:8])
                    mermaid_lines.append(f'    {node_ids[did]}["{title}"]')

            arrow = edge_styles.get(edge["edge_type"], "-->")
            mermaid_lines.append(
                f"    {node_ids[fid]} {arrow} {node_ids[tid]}"
            )

        mermaid_lines.append("```")

        content = "\n".join([
            "---",
            "title: Knowledge Graph",
            "auto_generated: true",
            f"updated: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
            f"total_edges: {len(edges)}",
            f"total_nodes: {len(node_ids)}",
            "---",
            "",
            "# Knowledge Graph",
            "",
            *mermaid_lines,
            "",
        ])

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    # ------------------------------------------------------------------
    # MOCs (Maps of Content)
    # ------------------------------------------------------------------

    def regenerate_mocs(self) -> List[str]:
        """Agrupa KB docs por domain → gera vault/moc/{domain}.md."""
        docs = self.kb.list_documents(status="active", limit=1000)
        domains: dict[str, list] = {}
        for d in docs:
            dom = d.get("domain", "general")
            domains.setdefault(dom, []).append(d)

        paths = []
        for domain, domain_docs in domains.items():
            path = self._write_moc(domain, domain_docs)
            paths.append(path)

        # MOC raiz (_index.md)
        self._regenerate_index()
        return paths

    def on_consolidation_conflict(
        self,
        doc_a_id: str,
        doc_b_id: str,
        merge_score: float,
        divergence: str = "",
    ) -> str:
        """Cria vault/conflitos/conflito_{timestamp}.md linkando ambos os docs."""
        doc_a = self.kb.get_document(doc_a_id)
        doc_b = self.kb.get_document(doc_b_id)
        title_a = doc_a["title"] if doc_a else doc_a_id
        title_b = doc_b["title"] if doc_b else doc_b_id

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"conflito_{timestamp}.md"
        filepath = os.path.join(self.vault_path, "conflitos", filename)

        lines = [
            "---",
            f'doc_a_id: "{doc_a_id}"',
            f'doc_b_id: "{doc_b_id}"',
            f'loser_id: ""',
            f"merge_score: {merge_score:.3f}",
            f'resolucao: ""',
            f"created: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
            "---",
            "",
            "# Conflito Detectado",
            "",
            f"**Score de merge:** {merge_score:.3f}",
            "",
            f"## Documento A: [[{title_a}]]",
            f"ID: `{doc_a_id}`",
            "",
            f"**Resumo:** {doc_a.get('summary', '') if doc_a else ''}",
            "",
            f"## Documento B: [[{title_b}]]",
            f"ID: `{doc_b_id}`",
            "",
            f"**Resumo:** {doc_b.get('summary', '') if doc_b else ''}",
            "",
        ]

        if divergence:
            lines += ["## Divergência Detectada", divergence, ""]

        lines += [
            "## Resolução",
            "Para resolver, edite o frontmatter acima:",
            "- `resolucao`: coloque o ID do documento VENCEDOR",
            "- `loser_id`: coloque o ID do documento a deprecar",
            "",
        ]

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        _log.info(f"Conflict flagged: {title_a} vs {title_b} (score={merge_score:.3f})")
        return filepath

    # ------------------------------------------------------------------
    # KB Hook Handler
    # ------------------------------------------------------------------

    def handle_kb_event(self, event_type: str, *args: Any) -> None:
        """
        Handler unificado para hooks do KB.
        Chamado por KB.add_document(), KB.update_document(), KB.add_edge().
        """
        try:
            if event_type == "doc_created":
                self.on_doc_created(args[0])
            elif event_type == "doc_updated":
                self.on_doc_updated(args[0])
            elif event_type == "edge_created" and len(args) >= 3:
                self.on_edge_created(args[0], args[1], args[2])
        except Exception as exc:
            _log.warn(f"Bridge event handler falhou ({event_type}): {exc}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_doc_to_vault(self, doc: Dict[str, Any]) -> str:
        """Escreve um documento KB como markdown no vault."""
        output_dir = os.path.join(self.vault_path, "conhecimento")
        safe_title = _safe_filename(doc.get("title", "untitled"))
        filename = f"{safe_title}.md"
        filepath = os.path.join(output_dir, filename)

        tags = doc.get("tags", [])
        if isinstance(tags, str):
            tags = json.loads(tags) if tags.startswith("[") else [tags]
        sessions = doc.get("source_sessions", [])
        if isinstance(sessions, str):
            sessions = json.loads(sessions) if sessions.startswith("[") else [sessions]

        # Get edges for this doc
        relations = self._get_doc_relations(doc["id"])

        lines = [
            "---",
            f'id: {doc.get("id", "")}',
            f'title: "{safe_title}"',
            f'domain: {doc.get("domain", "general")}',
            f"tags: {json.dumps(tags)}",
            f'importance: {doc.get("importance", 0.5)}',
            f'status: {doc.get("status", "active")}',
            f"sessions: {json.dumps(sessions)}",
        ]
        if relations:
            lines.append("relations:")
            for rel in relations:
                lines.append(f'  - type: {rel["edge_type"]}')
                lines.append(f'    target: "{rel["title"]}"')
        lines += [
            f"human_reviewed: false",
            f'created: {doc.get("created_at", "")}',
            f'updated: {doc.get("updated_at", "")}',
            "---",
            "",
            "## Resumo",
            doc.get("summary", ""),
            "",
            "## Contexto Completo",
            doc.get("full_context", ""),
            "",
        ]

        if sessions:
            lines.append("## Sessões Relacionadas")
            for sid in sessions:
                lines.append(f"- [[{sid}]]")
            lines.append("")

        if relations:
            lines.append("## Relacionamentos")
            for rel in relations:
                edge_label = rel["edge_type"].capitalize()
                lines.append(f"- **{edge_label}** → [[{rel['title']}]]")
            lines.append("")

        lines += [
            "## Notas Humanas",
            "<!-- Seção editável. Bridge detecta mudanças via hash. -->",
            "",
        ]

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        # Update sync tracking
        self._update_sync_record(doc["id"], filepath, content)
        return filepath

    def _import_single_conceito(self, filepath: str) -> Optional[str]:
        """Importa uma nota de conceitos/ para o KB."""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        fm, body = _parse_frontmatter(content)
        if not fm:
            return None

        title = fm.get("title", "").strip('"').strip("'")
        if not title:
            title = os.path.splitext(os.path.basename(filepath))[0]

        # Check hash — skip if already imported and unchanged
        current_hash = _file_hash(filepath)
        existing_hash = self._get_sync_hash_by_path(filepath)
        if existing_hash == current_hash:
            return None  # Nenhuma mudança

        # Check if doc with same title already exists
        existing = self.kb.search_hybrid(title, limit=1)
        if existing and existing[0].get("title", "").lower() == title.lower():
            return None

        domain = fm.get("domain", "conceitos")
        if isinstance(domain, str):
            domain = domain.strip('"').strip("'")
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        importance = float(fm.get("importance", 0.6))

        summary, full_context = _split_body(body)

        doc_id = self.kb.add_document(
            title=title,
            summary=summary,
            full_context=full_context,
            tags=tags,
            domain=domain,
            importance=importance,
            source_sessions=["obsidian-import"],
        )

        # Track hash
        self._update_sync_record(doc_id, filepath, content)
        return doc_id

    def _sync_single_correction(self, filepath: str, result: dict) -> None:
        """Sincroniza uma nota editada de conhecimento/ de volta ao KB."""
        current_hash = _file_hash(filepath)

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        fm, body = _parse_frontmatter(content)
        doc_id = str(fm.get("id", ""))
        if not doc_id:
            return

        # Check if file changed since last sync
        stored_hash = self._get_sync_hash(doc_id)
        if stored_hash == current_hash:
            return  # Nenhuma mudança

        # File changed — check what changed
        existing = self.kb.get_document(doc_id)
        if not existing:
            return

        updates: dict = {}

        # Check metadata changes
        fm_importance = fm.get("importance")
        if fm_importance is not None and isinstance(fm_importance, (int, float)):
            if abs(float(fm_importance) - existing.get("importance", 0.5)) > 0.01:
                updates["importance"] = float(fm_importance)

        fm_domain = fm.get("domain")
        if fm_domain and isinstance(fm_domain, str):
            fm_domain = fm_domain.strip('"').strip("'")
            if fm_domain != existing.get("domain", "general"):
                updates["domain"] = fm_domain

        fm_tags = fm.get("tags")
        if fm_tags and isinstance(fm_tags, list):
            if set(fm_tags) != set(existing.get("tags", [])):
                updates["tags"] = fm_tags

        if updates:
            self.kb.update_document(doc_id, **updates)
            result["metadata_updated"].append(doc_id)

        # Check human notes section
        human_notes = _extract_human_notes(body)
        if human_notes:
            stored_notes_hash = self._get_human_notes_hash(doc_id)
            notes_hash = _content_hash(human_notes)
            if notes_hash != stored_notes_hash:
                existing_context = existing.get("full_context", "")
                # Append human notes (não sobrescreve, adiciona)
                if "[Notas Humanas]" not in existing_context:
                    merged = existing_context + "\n\n---\n[Notas Humanas]\n" + human_notes
                else:
                    # Atualiza seção de notas humanas existente
                    merged = re.sub(
                        r"\[Notas Humanas\]\n.*$",
                        f"[Notas Humanas]\n{human_notes}",
                        existing_context,
                        flags=re.DOTALL,
                    )
                self.kb.update_document(doc_id, full_context=merged)
                self._update_human_notes_hash(doc_id, notes_hash)
                result["human_notes_merged"].append(doc_id)

        # Update file hash
        self._update_sync_hash(doc_id, current_hash)

    def _write_moc(self, domain: str, docs: List[Dict[str, Any]]) -> str:
        """Escreve MOC para um domínio."""
        safe_domain = _safe_filename(domain)
        filepath = os.path.join(self.vault_path, "moc", f"{safe_domain}.md")

        # Sort by importance desc
        docs_sorted = sorted(docs, key=lambda d: d.get("importance", 0.5), reverse=True)

        def _importance_icon(imp: float) -> str:
            if imp >= 0.8:
                return "🔴"
            elif imp >= 0.5:
                return "🟡"
            return "🟢"

        lines = [
            "---",
            f'title: "MOC: {domain.capitalize()}"',
            "type: moc",
            "auto_generated: true",
            f"doc_count: {len(docs_sorted)}",
            f"updated: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
            "---",
            "",
            f"# {domain.capitalize()} — Mapa de Conteúdo",
            "",
            "## Documentos por Importância",
        ]

        for d in docs_sorted:
            imp = d.get("importance", 0.5)
            icon = _importance_icon(imp)
            title = d.get("title", "?")
            lines.append(f"- {icon} ({imp:.1f}) [[{title}]]")

        lines.append("")

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def _regenerate_index(self) -> str:
        """Gera _index.md como MOC raiz."""
        filepath = os.path.join(self.vault_path, "_index.md")
        stats = self.kb.stats()
        domains = stats.get("domains", {})

        lines = [
            "---",
            "title: Arkhe Knowledge Index",
            "type: moc",
            "auto_generated: true",
            f"total_docs: {stats.get('active_documents', 0)}",
            f"total_edges: {stats.get('total_edges', 0)}",
            f"updated: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
            "---",
            "",
            "# 📚 Arkhe Knowledge Index",
            "",
            "## Domínios",
        ]

        for domain, count in sorted(domains.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- [[MOC: {domain.capitalize()}|{domain}]] ({count} docs)")

        lines += [
            "",
            "## Links Rápidos",
            f"- [[knowledge_graph|Knowledge Graph]] ({stats.get('total_edges', 0)} edges)",
            f"- Total: {stats.get('active_documents', 0)} documentos ativos",
            "",
        ]

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def _get_doc_relations(self, doc_id: str) -> List[Dict[str, Any]]:
        """Get typed relations for a document."""
        try:
            return self.kb.get_related(doc_id)
        except Exception:
            return []

    def _get_existing_edges(self) -> set:
        """Retorna set de (from_id, to_id, edge_type) existentes."""
        try:
            with closing(sqlite3.connect(self.kb.db_path)) as conn:
                rows = conn.execute(
                    "SELECT from_id, to_id, edge_type FROM kb_edges"
                ).fetchall()
                return {(r[0], r[1], r[2]) for r in rows}
        except Exception:
            return set()

    # ------------------------------------------------------------------
    # Sync DB operations
    # ------------------------------------------------------------------

    def _update_sync_record(self, doc_id: str, filepath: str, content: str) -> None:
        """Atualiza registro de sync para um documento."""
        fhash = _content_hash(content)
        with closing(sqlite3.connect(self.kb.db_path)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO vault_sync
                    (doc_id, file_path, file_hash, last_synced)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (doc_id, filepath, fhash))
            conn.commit()

    def _get_sync_hash(self, doc_id: str) -> str:
        """Retorna hash armazenado para um doc_id."""
        try:
            with closing(sqlite3.connect(self.kb.db_path)) as conn:
                row = conn.execute(
                    "SELECT file_hash FROM vault_sync WHERE doc_id = ?", (doc_id,)
                ).fetchone()
                return row[0] if row else ""
        except Exception:
            return ""

    def _get_sync_hash_by_path(self, filepath: str) -> str:
        """Retorna hash armazenado para um filepath."""
        try:
            with closing(sqlite3.connect(self.kb.db_path)) as conn:
                row = conn.execute(
                    "SELECT file_hash FROM vault_sync WHERE file_path = ?", (filepath,)
                ).fetchone()
                return row[0] if row else ""
        except Exception:
            return ""

    def _update_sync_hash(self, doc_id: str, file_hash: str) -> None:
        """Atualiza apenas o file_hash no sync record."""
        try:
            with closing(sqlite3.connect(self.kb.db_path)) as conn:
                conn.execute(
                    "UPDATE vault_sync SET file_hash = ?, last_synced = CURRENT_TIMESTAMP WHERE doc_id = ?",
                    (file_hash, doc_id),
                )
                conn.commit()
        except Exception:
            pass

    def _get_human_notes_hash(self, doc_id: str) -> str:
        """Retorna hash das notas humanas armazenado."""
        try:
            with closing(sqlite3.connect(self.kb.db_path)) as conn:
                row = conn.execute(
                    "SELECT human_notes_hash FROM vault_sync WHERE doc_id = ?", (doc_id,)
                ).fetchone()
                return row[0] if row else ""
        except Exception:
            return ""

    def _update_human_notes_hash(self, doc_id: str, notes_hash: str) -> None:
        """Atualiza hash das notas humanas no sync record."""
        try:
            with closing(sqlite3.connect(self.kb.db_path)) as conn:
                conn.execute(
                    "UPDATE vault_sync SET human_notes_hash = ?, last_synced = CURRENT_TIMESTAMP WHERE doc_id = ?",
                    (notes_hash, doc_id),
                )
                conn.commit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    def _usage():
        print("Usage: python -m rlm.core.obsidian_bridge [--sync|--export-all|--mocs|--graph]")
        sys.exit(1)

    if len(sys.argv) < 2:
        _usage()

    # Load config from environment or defaults
    vault_path = os.environ.get("ARKHE_VAULT_PATH", os.path.expanduser("~/.arkhe/vault"))
    kb_path = os.environ.get("ARKHE_KB_PATH", os.path.expanduser("~/.arkhe/data/knowledge_base.db"))

    from rlm.core.knowledge_base import GlobalKnowledgeBase
    kb = GlobalKnowledgeBase(db_path=kb_path)
    bridge = ObsidianBridge(vault_path=vault_path, kb=kb)

    cmd = sys.argv[1]
    if cmd == "--sync":
        stats = bridge.sync_all()
        print(f"Sync complete: {json.dumps(stats, indent=2)}")
    elif cmd == "--export-all":
        paths = bridge.export_all()
        print(f"Exported {len(paths)} documents")
    elif cmd == "--mocs":
        paths = bridge.regenerate_mocs()
        print(f"Generated {len(paths)} MOCs")
    elif cmd == "--graph":
        path = bridge.export_knowledge_graph()
        print(f"Graph exported to {path}")
    else:
        _usage()
