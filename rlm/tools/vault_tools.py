"""
Vault tools — exposes Obsidian vault operations to the REPL namespace.

Allows the AI to:
  - vault_search(query, scope)  → search vault notes by content
  - vault_read(note_path)       → read a specific vault note
  - vault_check_corrections()   → check for pending human corrections
  - vault_moc(domain)           → get the MOC for a domain
"""
from __future__ import annotations

import os
import re
from typing import Any


def get_vault_tools(rlm_session: Any) -> dict[str, Any]:
    """
    Build dict of vault callables for REPL injection.

    Args:
        rlm_session: RLMSession instance with ._obsidian_bridge property.

    Returns:
        Dict of tool name -> callable. Empty if no bridge configured.
    """
    bridge = getattr(rlm_session, "_obsidian_bridge", None)
    if bridge is None:
        return {}

    vault_path = bridge.vault_path

    def vault_search(query: str, scope: str = "all") -> list[dict[str, Any]]:
        """Search vault notes by content. scope: 'conceitos', 'conhecimento', 'conflitos', 'sessoes', 'all'."""
        valid_scopes = {"conceitos", "conhecimento", "conflitos", "sessoes", "moc", "all"}
        if scope not in valid_scopes:
            return [{"error": f"Invalid scope. Use one of: {valid_scopes}"}]

        dirs_to_search = [scope] if scope != "all" else ["conhecimento", "conceitos", "conflitos", "sessoes", "moc"]
        results = []
        query_lower = query.lower()

        for subdir in dirs_to_search:
            search_dir = os.path.join(vault_path, subdir)
            if not os.path.isdir(search_dir):
                continue
            for filename in os.listdir(search_dir):
                if not filename.endswith(".md"):
                    continue
                filepath = os.path.join(search_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read(4096)  # First 4KB
                    if query_lower in content.lower():
                        # Extract title from frontmatter
                        title_match = re.search(r"title:\s*[\"']?(.+?)[\"']?\s*$", content, re.MULTILINE)
                        title = title_match.group(1) if title_match else filename[:-3]
                        results.append({
                            "file": f"{subdir}/{filename}",
                            "title": title,
                            "preview": content[:300].replace("---", "").strip()[:200],
                        })
                except Exception:
                    continue
            if len(results) >= 10:
                break
        return results if results else [{"info": f"No notes matching '{query}' in scope '{scope}'"}]

    def vault_read(note_path: str) -> dict[str, Any]:
        """Read a specific vault note. Path relative to vault root, e.g. 'conhecimento/my_note.md'."""
        # Sanitize: prevent path traversal and absolute path injection.
        # os.path.isabs() catches both Unix ("/etc/passwd") and Windows
        # drive-letter paths ("C:\\Windows\\...") that normpath leaves intact.
        clean = os.path.normpath(note_path).replace("\\", "/")
        if ".." in clean or clean.startswith("/") or os.path.isabs(clean):
            return {"error": "Invalid path"}
        filepath = os.path.join(vault_path, clean)
        if not os.path.isfile(filepath):
            return {"error": f"Note not found: {note_path}"}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read(16384)  # 16KB max
            return {"path": note_path, "content": content}
        except Exception as e:
            return {"error": str(e)}

    def vault_check_corrections() -> dict[str, Any]:
        """Check for pending human corrections in the vault (edits to conhecimento/, unresolved conflicts)."""
        result: dict[str, Any] = {"pending_corrections": 0, "pending_conflicts": 0, "details": []}

        # Check corrections via bridge
        try:
            corrections = bridge.sync_corrections()
            meta = corrections.get("metadata_updated", [])
            notes = corrections.get("human_notes_merged", [])
            result["pending_corrections"] = len(meta) + len(notes)
            if meta:
                result["details"].append(f"Metadata updates: {meta}")
            if notes:
                result["details"].append(f"Human notes merged: {notes}")
        except Exception:
            pass

        # Check unresolved conflicts
        conflicts_dir = os.path.join(vault_path, "conflitos")
        if os.path.isdir(conflicts_dir):
            for f in os.listdir(conflicts_dir):
                if f.endswith(".md") and os.path.isfile(os.path.join(conflicts_dir, f)):
                    result["pending_conflicts"] += 1

        return result

    def vault_moc(domain: str) -> dict[str, Any]:
        """Get the Map of Content (MOC) for a domain. Returns the MOC note content."""
        from rlm.core.integrations.obsidian_bridge import _safe_filename
        safe = _safe_filename(domain)
        filepath = os.path.join(vault_path, "moc", f"{safe}.md")
        if not os.path.isfile(filepath):
            # Try regenerating
            try:
                bridge.regenerate_mocs()
            except Exception:
                pass
            if not os.path.isfile(filepath):
                return {"error": f"No MOC found for domain '{domain}'"}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            return {"domain": domain, "content": content}
        except Exception as e:
            return {"error": str(e)}

    return {
        "vault_search": vault_search,
        "vault_read": vault_read,
        "vault_check_corrections": vault_check_corrections,
        "vault_moc": vault_moc,
    }
