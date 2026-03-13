"""
Two-layer addressable memory system for RLM.

Provides:
Layer 1: Raw, lossless code storage (chunking + exact reassembly)
Layer 2: Structured knowledge graph (analysis + explicit linking)

[PHASE 9 REWRITE] 
Now powered entirely by MultiVectorMemory (SQLite FTS5 + Python Vectors)
instead of flat JSON files, providing immense speedups and true semantic search.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
import sqlite3

from rlm.core.memory_manager import MultiVectorMemory

@dataclass
class KnowledgeEntry:
    """A node in the Knowledge Graph (Layer 2)."""
    key: str
    analysis: str
    source_ref: str | None = None
    line_range: tuple[int, int] | None = None
    links: list[dict[str, str]] = None  # [{"relation": "depends_on", "target": "utils"}]
    timestamp: str = ""

    def __post_init__(self):
        if self.links is None:
            self.links = []
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeEntry:
        # Convert list to tuple for line_range
        if data.get("line_range") and isinstance(data["line_range"], list):
            data["line_range"] = tuple(data["line_range"])
        return cls(**data)


class RLMMemory:
    """
    SQLite-based persistent memory system for code analysis and context retention.
    Acts as a wrapper over MultiVectorMemory to preserve the REPL tooling interface.
    """

    def __init__(self, memory_dir: str, enable_embeddings: bool = True):
        self.base_dir = os.path.abspath(memory_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        
        # We share a single SQLite db file for the workspace
        # memory_dir is usually project root / .rlm_memory
        db_path = os.path.join(self.base_dir, "memory_v2.db")
        
        # Initialize the underlying hybrid engine
        self.db = MultiVectorMemory(db_path=db_path)
        
        # Legacy index for fast lookups without querying db every time
        self.session_id = "default_repl"

    # =========================================================================
    # LAYER 1: Raw Storage (Lossless)
    # =========================================================================

    def store(self, key: str, content: str) -> str:
        """Store exact content losslessly under a key."""
        self.db.add_memory(
            session_id=self.session_id,
            content=content,
            metadata={"type": "raw", "key": key},
            memory_id=key
        )
        return f"Stored {len(content)} chars at key: {key}"

    def read(self, key: str, start_line: int | None = None, end_line: int | None = None) -> str | None:
        """Retrieve exact content by key, with optional line range (1-indexed)."""
        mem = self.db.get_memory(key)
        if not mem:
            return None
            
        content = mem["content"]
        
        if start_line is None and end_line is None:
            return content
            
        lines = content.splitlines(keepends=True)
        s = max(1, start_line) - 1 if start_line else 0
        e = min(end_line, len(lines)) if end_line else len(lines)
        
        # Prepend original line numbers for context
        numbered = [f"{i}: {line}" for i, line in enumerate(lines[s:e], start=s+1)]
        header = f"[Fragment of {key} Lines {s+1}-{e}]\n"
        return header + "".join(numbered)

    def chunk_and_store(self, source_path: str, key_prefix: str, chunk_lines: int = 200) -> list[str]:
        """Split a large file into smaller chunks and store losslessly."""
        if not os.path.isfile(source_path):
            return [f"Error: {source_path} is not a file"]
            
        try:
            with open(source_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            return [f"Error reading {source_path}: {e}"]

        total_lines = len(lines)
        if total_lines == 0:
            return []

        # Store metadata about the chunks
        meta_key = f"{key_prefix}_meta"
        meta_content = json.dumps({
            "source_path": source_path,
            "total_lines": total_lines,
            "chunk_size": chunk_lines,
            "num_chunks": (total_lines + chunk_lines - 1) // chunk_lines,
            "timestamp": datetime.now().isoformat()
        }, indent=2)
        self.store(meta_key, meta_content)

        keys_created = [meta_key]
        chunk_idx = 0
        
        for i in range(0, total_lines, chunk_lines):
            chunk = lines[i:i + chunk_lines]
            chunk_key = f"{key_prefix}_chunk_{chunk_idx:03d}"
            
            # Content prepended with strict line numbers for exact reassembly
            header = f"--- CHUNK {chunk_idx:03d} LINES {i+1}-{min(i+chunk_lines, total_lines)} ---\n"
            content = header + "".join(chunk)
            
            self.store(chunk_key, content)
            keys_created.append(chunk_key)
            chunk_idx += 1

        return keys_created

    def reassemble(self, key_prefix: str) -> str:
        """Reconstruct the original file EXACTLY from its chunks."""
        meta_key = f"{key_prefix}_meta"
        meta_json = self.read(meta_key)
        
        if not meta_json:
            return f"Error: No metadata found for prefix {key_prefix}"
            
        try:
            meta = json.loads(meta_json)
        except Exception:
            return "Error: Invalid metadata format"
            
        num_chunks = meta.get("num_chunks", 0)
        reconstructed = []
        
        for i in range(num_chunks):
            chunk_key = f"{key_prefix}_chunk_{i:03d}"
            chunk_content = self.read(chunk_key)
            if not chunk_content:
                return f"Error: Missing chunk {chunk_key}. Reassembly failed."
                
            # Strip the header line added by chunk_and_store
            lines = chunk_content.split("\n", 1)
            if len(lines) > 1:
                reconstructed.append(lines[1])
            else:
                reconstructed.append(chunk_content)
                
        return "".join(reconstructed)

    # =========================================================================
    # LAYER 2: Knowledge Graph (Structured Context)
    # =========================================================================

    def analyze(self, key: str, analysis: str, source_ref: str | None = None, 
                line_range: tuple[int, int] | None = None) -> str:
        """Store LLM analysis linked to a specific key (and optional source block)."""
        entry = KnowledgeEntry(
            key=key,
            analysis=analysis,
            source_ref=source_ref,
            line_range=line_range,
        )
        
        # Merge if exists, to keep links
        existing = self.get_knowledge(key)
        if existing:
            entry.links = existing.get("links", [])
            
        # Store in SQLite VSS Engine!
        self.db.add_memory(
            session_id=self.session_id,
            content=analysis, # Embed the analysis!
            metadata={"type": "knowledge", "entry": entry.to_dict()},
            memory_id=key
        )
            
        return f"Stored knowledge for '{key}'"

    def get_knowledge(self, key: str) -> dict[str, Any] | None:
        """Retrieve a knowledge entry as a dict."""
        mem = self.db.get_memory(key)
        if not mem:
            return None
        
        meta = mem.get("metadata", {})
        if meta.get("type") == "knowledge":
            return meta.get("entry")
        return None

    def link(self, from_key: str, relation: str, to_key: str) -> str:
        """Create an explicit directional relationship between two knowledge nodes."""
        entry_dict = self.get_knowledge(from_key)
        if not entry_dict:
            # Create a placeholder if it doesn't exist
            self.analyze(from_key, f"Auto-created to link to {to_key}")
            entry_dict = self.get_knowledge(from_key)
            
        entry = KnowledgeEntry.from_dict(entry_dict)
        
        # Check if link already exists
        for link in entry.links:
            if link["relation"] == relation and link["target"] == to_key:
                return f"Link {from_key} -[{relation}]-> {to_key} already exists"
                
        entry.links.append({"relation": relation, "target": to_key})
        
        # Re-save
        self.analyze(from_key, entry.analysis, entry.source_ref, entry.line_range)
            
        return f"Created link {from_key} -[{relation}]-> {to_key}"

    def get_links(self, key: str) -> list[dict[str, str]]:
        """Get all outbound links from a key."""
        entry = self.get_knowledge(key)
        return entry.get("links", []) if entry else []

    # =========================================================================
    # Memory Overview Tools
    # =========================================================================

    def list_keys(self, prefix: str = "", layer: str = "both") -> list[str]:
        """List keys in the memory system, optionally filtered by prefix."""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, metadata FROM memory_chunks").fetchall()
            
        results = []
        for r in rows:
            if prefix and not r["id"].startswith(prefix):
                continue
                
            try:
                meta = json.loads(r["metadata"])
                mtype = meta.get("type")
            except:
                continue
                
            if layer == "both" or mtype == layer:
                results.append(r["id"])
                
        return sorted(results)

    def search(self, keyword: str) -> list[dict[str, str]]:
        """Hybrid search powered by MultiVectorMemory (BM25 + RRF Vector)."""
        hybrid_results = self.db.search_hybrid(keyword, limit=10, session_id=self.session_id)
        
        formatted = []
        for r in hybrid_results:
            meta = r.get("metadata", {})
            mtype = meta.get("type", "unknown")
            formatted.append({
                "key": r["id"],
                "type": mtype,
                "preview": r["content"][:100] + "...",
                "match": f"hybrid_score: {r['hybrid_score']}",
            })
            
        return formatted

    def semantic_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Pure semantic search using vector embeddings (Legacy bridge to hybrid search)."""
        # We just route directly to our far superior Hybrid Search
        return self.search(keyword=query)[:top_k]

    def status(self) -> str:
        """Get an overview of the memory system status via SQLite stats."""
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM memory_chunks").fetchone()[0]
                db_size = os.path.getsize(self.db.db_path) / 1024 / 1024
        except Exception:
            count = 0
            db_size = 0.0
            
        return (
            f"🧠 RLM Memory Status (V2 MultiVector)\n"
            f"Location: {self.db.db_path}\n"
            f"Total Nodes: {count} entries ({db_size:.2f} MB)\n"
            f"Engine: SQLite FTS5 + Python RRF Vectors"
        )
