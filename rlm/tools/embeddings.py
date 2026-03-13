"""
RLM Embedding Index — Local vector search for the Knowledge Graph.

Uses sentence-transformers for local embedding generation (no API calls).
Stores vectors alongside the JSON knowledge graph for hybrid search:
- Exact search: substring/regex via memory.search()
- Semantic search: cosine similarity via EmbeddingIndex.search()

Falls back gracefully if sentence-transformers is not installed.
"""

from __future__ import annotations

import json
import os
from typing import Any

import numpy as np


class EmbeddingIndex:
    """Lightweight local vector index for semantic search over memory nodes.

    Stores embeddings as a NumPy .npz file alongside the .rlm_memory directory.
    Uses all-MiniLM-L6-v2 (~80MB) for fast, local embedding generation.
    """

    def __init__(self, memory_dir: str, model_name: str = "all-MiniLM-L6-v2"):
        """Initialize the embedding index.

        Args:
            memory_dir: Path to the .rlm_memory directory.
            model_name: Sentence-transformer model name.
        """
        self.memory_dir = memory_dir
        self.model_name = model_name
        self.vectors_path = os.path.join(memory_dir, "vectors.npz")
        self.index_path = os.path.join(memory_dir, "vector_index.json")

        self._model = None
        self._keys: list[str] = []
        self._texts: list[str] = []
        self._embeddings: np.ndarray | None = None

        # Load existing index if available
        self._load()

    def _get_model(self):
        """Lazy-load the sentence-transformer model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for semantic search. "
                    "Install it with: pip install sentence-transformers"
                )
        return self._model

    def _load(self):
        """Load existing vectors and index from disk."""
        if os.path.exists(self.index_path) and os.path.exists(self.vectors_path):
            try:
                with open(self.index_path, "r") as f:
                    index_data = json.load(f)
                self._keys = index_data.get("keys", [])
                self._texts = index_data.get("texts", [])

                data = np.load(self.vectors_path)
                self._embeddings = data["embeddings"]
            except Exception:
                self._keys = []
                self._texts = []
                self._embeddings = None

    def _save(self):
        """Persist vectors and index to disk."""
        os.makedirs(self.memory_dir, exist_ok=True)

        # Save index metadata
        with open(self.index_path, "w") as f:
            json.dump({"keys": self._keys, "texts": self._texts}, f, indent=2)

        # Save vectors
        if self._embeddings is not None:
            np.savez_compressed(self.vectors_path, embeddings=self._embeddings)

    def add(self, key: str, text: str) -> None:
        """Add a text entry to the vector index.

        Args:
            key: The memory key (e.g., file path or analysis key).
            text: The text to embed (typically the analysis summary).
        """
        model = self._get_model()

        # Generate embedding
        embedding = model.encode([text], show_progress_bar=False)

        if self._embeddings is None:
            self._embeddings = embedding
        else:
            self._embeddings = np.vstack([self._embeddings, embedding])

        self._keys.append(key)
        self._texts.append(text[:500])  # Store truncated text for display

        self._save()

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Search for semantically similar entries.

        Args:
            query: Natural language search query.
            top_k: Number of results to return.

        Returns:
            List of dicts with 'key', 'text', and 'similarity' fields,
            sorted by descending similarity.
        """
        if self._embeddings is None or len(self._keys) == 0:
            return []

        model = self._get_model()
        query_embedding = model.encode([query], show_progress_bar=False)

        # Cosine similarity
        similarities = np.dot(self._embeddings, query_embedding.T).flatten()
        norms = np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(query_embedding)
        norms = np.maximum(norms, 1e-10)  # Avoid division by zero
        similarities = similarities / norms

        # Get top-k indices
        top_indices = np.argsort(similarities)[::-1][:top_k]

        results = []
        for idx in top_indices:
            results.append({
                "key": self._keys[idx],
                "text": self._texts[idx],
                "similarity": float(similarities[idx]),
            })

        return results

    def count(self) -> int:
        """Return the number of indexed entries."""
        return len(self._keys)

    def status(self) -> str:
        """Return a summary of the index status."""
        if self._embeddings is None:
            return "Embedding index: empty (0 entries)"
        dim = self._embeddings.shape[1] if len(self._embeddings.shape) > 1 else 0
        return f"Embedding index: {len(self._keys)} entries, {dim}-dim vectors, model={self.model_name}"
