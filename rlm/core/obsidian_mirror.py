"""
Obsidian Mirror — sincronização bidirecional KB ↔ Obsidian vault.

Export (KB → vault):
  Escreve cada documento KB como markdown com YAML frontmatter na pasta
  `vault_path/conhecimento/`. Fire-and-forget, não bloqueia o fluxo principal.

Import (vault → KB):
  Lê notas markdown da pasta `vault_path/conceitos/` e as importa como
  documentos KB. Conceitos são criados manualmente pelo humano.
  Importado no boot do servidor ou sob demanda.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional


def export_document_to_vault(
    doc: Dict[str, Any],
    vault_path: str,
) -> str:
    """
    Escreve um documento KB como markdown no vault Obsidian.

    Args:
        doc: Documento completo do KB (get_document result).
        vault_path: Caminho raiz do vault Obsidian.

    Returns:
        Caminho do arquivo escrito.
    """
    output_dir = os.path.join(vault_path, "conhecimento")
    os.makedirs(output_dir, exist_ok=True)

    # Sanitize title for filename
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", doc.get("title", "untitled"))
    safe_title = safe_title[:80].strip()
    filename = f"{safe_title}.md"
    filepath = os.path.join(output_dir, filename)

    tags = doc.get("tags", [])
    if isinstance(tags, str):
        tags = json.loads(tags) if tags.startswith("[") else [tags]
    sessions = doc.get("source_sessions", [])
    if isinstance(sessions, str):
        sessions = json.loads(sessions) if sessions.startswith("[") else [sessions]

    lines = [
        "---",
        f'id: {doc.get("id", "")}',
        f'title: "{safe_title}"',
        f'domain: {doc.get("domain", "general")}',
        f"tags: {json.dumps(tags)}",
        f'importance: {doc.get("importance", 0.5)}',
        f'status: {doc.get("status", "active")}',
        f"sessions: {json.dumps(sessions)}",
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

    content = "\n".join(lines)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def export_all_to_vault(
    kb: Any,
    vault_path: str,
) -> List[str]:
    """
    Exporta todos os documentos ativos do KB para o vault.

    Returns:
        Lista de caminhos escritos.
    """
    docs = kb.list_documents(status="active", limit=1000)
    paths = []
    for doc_summary in docs:
        full_doc = kb.get_document(doc_summary["id"])
        if full_doc:
            path = export_document_to_vault(full_doc, vault_path)
            paths.append(path)
    return paths


def import_conceitos_from_vault(
    vault_path: str,
    kb: Any,
) -> List[str]:
    """
    Importa notas da pasta `conceitos/` do vault para o KB.

    Notas devem ter YAML frontmatter com pelo menos title.
    Se um documento com o mesmo título já existe, pula.

    Returns:
        Lista de doc_ids importados.
    """
    conceitos_dir = os.path.join(vault_path, "conceitos")
    if not os.path.isdir(conceitos_dir):
        return []

    imported: List[str] = []
    for filename in os.listdir(conceitos_dir):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(conceitos_dir, filename)
        try:
            doc_id = _import_single_note(filepath, kb)
            if doc_id:
                imported.append(doc_id)
        except Exception:
            continue

    return imported


def _import_single_note(filepath: str, kb: Any) -> Optional[str]:
    """Importa uma única nota markdown para o KB. Retorna doc_id ou None se já existe."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse YAML frontmatter
    frontmatter, body = _parse_frontmatter(content)
    if not frontmatter:
        return None

    title = frontmatter.get("title", "").strip('"').strip("'")
    if not title:
        title = os.path.splitext(os.path.basename(filepath))[0]

    # Check if already exists by title
    existing = kb.search_hybrid(title, limit=1)
    if existing and existing[0].get("title", "").lower() == title.lower():
        return None  # Já existe

    domain = frontmatter.get("domain", "conceitos")
    tags = frontmatter.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    importance = float(frontmatter.get("importance", 0.6))

    # Split body into summary + full_context
    summary, full_context = _split_body(body)

    doc_id = kb.add_document(
        title=title,
        summary=summary,
        full_context=full_context,
        tags=tags,
        domain=domain,
        importance=importance,
        source_sessions=["obsidian-import"],
    )
    return doc_id


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
            # Parse simple YAML values
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
    """
    Separa o body em summary (primeiro parágrafo ou seção ## Resumo)
    e full_context (resto).
    """
    # Tenta encontrar seção ## Resumo
    resumo_match = re.search(r"##\s*Resumo\s*\n(.*?)(?=\n##|\Z)", body, re.DOTALL)
    if resumo_match:
        summary = resumo_match.group(1).strip()
        # Full context é tudo após o resumo
        ctx_match = re.search(r"##\s*Contexto\s+Completo\s*\n(.*?)(?=\n##|\Z)", body, re.DOTALL)
        full_context = ctx_match.group(1).strip() if ctx_match else body.strip()
        return summary, full_context

    # Fallback: primeiro parágrafo como summary
    paragraphs = body.strip().split("\n\n")
    if paragraphs:
        summary = paragraphs[0].strip()
        full_context = "\n\n".join(paragraphs[1:]).strip() if len(paragraphs) > 1 else ""
        return summary, full_context

    return body.strip()[:500], body.strip()
