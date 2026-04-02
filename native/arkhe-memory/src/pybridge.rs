/// PyO3 bridge — exposes the HNSW index and vecmath primitives to Python
/// as the `arkhe_memory` native module.

use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use std::collections::HashMap;
use std::sync::Arc;

use crate::hnsw::HnswIndex;
use crate::vecmath;
use crate::sparse;

// ── Python class wrapping HnswIndex ─────────────────────────────────────

/// High-performance vector index backed by an HNSW graph.
///
/// Usage from Python:
///
/// ```python
/// from arkhe_memory import ArkheVectorIndex
/// idx = ArkheVectorIndex(dim=1536)
/// idx.add("doc-1", embedding_list)
/// results = idx.search(query_embedding, top_k=10)
/// # results: List[(str, float)]  →  (id, cosine_distance)
/// ```
#[pyclass]
pub struct ArkheVectorIndex {
    inner: Arc<HnswIndex>,
}

#[pymethods]
impl ArkheVectorIndex {
    #[new]
    #[pyo3(signature = (dim, m=16, ef_construction=200, ef_search=50))]
    fn new(dim: usize, m: usize, ef_construction: usize, ef_search: usize) -> PyResult<Self> {
        if dim == 0 {
            return Err(PyValueError::new_err("dim must be > 0"));
        }
        Ok(Self {
            inner: Arc::new(HnswIndex::with_params(dim, m, ef_construction, ef_search)),
        })
    }

    /// Add a single vector. Raises ValueError on dimension mismatch.
    fn add(&self, id: String, embedding: Vec<f32>) -> PyResult<()> {
        if embedding.len() != self.inner.dim {
            return Err(PyValueError::new_err(format!(
                "expected dim={}, got {}",
                self.inner.dim,
                embedding.len()
            )));
        }
        self.inner.insert(id, embedding);
        Ok(())
    }

    /// Add multiple vectors at once.
    fn bulk_add(&self, ids: Vec<String>, embeddings: Vec<Vec<f32>>) -> PyResult<()> {
        if ids.len() != embeddings.len() {
            return Err(PyValueError::new_err("ids and embeddings length mismatch"));
        }
        for (id, emb) in ids.into_iter().zip(embeddings.into_iter()) {
            if emb.len() != self.inner.dim {
                return Err(PyValueError::new_err(format!(
                    "expected dim={}, got {} for id={}",
                    self.inner.dim,
                    emb.len(),
                    id
                )));
            }
            self.inner.insert(id, emb);
        }
        Ok(())
    }

    /// Search for the `top_k` nearest neighbours.
    /// Returns list of (id, cosine_distance) sorted ascending.
    #[pyo3(signature = (query, top_k=10))]
    fn search(&self, query: Vec<f32>, top_k: usize) -> PyResult<Vec<(String, f32)>> {
        if query.len() != self.inner.dim {
            return Err(PyValueError::new_err(format!(
                "expected dim={}, got {}",
                self.inner.dim,
                query.len()
            )));
        }
        Ok(self.inner.search(&query, top_k))
    }

    /// Remove a vector by id. Returns True if found and removed.
    fn remove(&self, id: &str) -> bool {
        self.inner.remove(id)
    }

    /// Number of vectors in the index.
    fn __len__(&self) -> usize {
        self.inner.len()
    }

    /// Check if a vector id exists.
    fn __contains__(&self, id: &str) -> bool {
        self.inner.contains(id)
    }

    /// Get all stored ids.
    fn ids(&self) -> Vec<String> {
        self.inner.ids()
    }
}

// ── Standalone functions ────────────────────────────────────────────────

/// Cosine similarity between two vectors (returns value in [-1, 1]).
#[pyfunction]
fn cosine_similarity(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
    if a.len() != b.len() {
        return Err(PyValueError::new_err("vectors must have same length"));
    }
    Ok(vecmath::cosine_similarity(&a, &b))
}

/// Cosine distance (1 - cosine_similarity), in [0, 2].
#[pyfunction]
fn cosine_distance(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
    if a.len() != b.len() {
        return Err(PyValueError::new_err("vectors must have same length"));
    }
    Ok(vecmath::cosine_distance(&a, &b))
}

/// Compute cosine similarity of `query` against each row in `matrix`,
/// return the top-k (index, similarity) pairs sorted descending.
#[pyfunction]
#[pyo3(signature = (query, matrix, k))]
fn batch_cosine_top_k(query: Vec<f32>, matrix: Vec<Vec<f32>>, k: usize) -> PyResult<Vec<(usize, f32)>> {
    Ok(vecmath::batch_cosine_top_k(&query, &matrix, k))
}

// ── Module registration ─────────────────────────────────────────────────

// ── Sparse Semantic Index (PyO3 wrapper) ────────────────────────────────

/// In-memory sparse TF-trigram semantic text index.
///
/// Usage from Python:
///
/// ```python
/// from arkhe_memory import ArkheSemanticIndex
/// idx = ArkheSemanticIndex()
/// idx.add("doc-1", "Python asyncio tutorial")
/// results = idx.search("python async", top_k=5)
/// # results: List[(str, str, float)]  →  (key, text, similarity)
/// ```
#[pyclass]
pub struct ArkheSemanticIndex {
    inner: parking_lot::RwLock<sparse::SemanticIndex>,
}

#[pymethods]
impl ArkheSemanticIndex {
    #[new]
    fn new() -> Self {
        Self {
            inner: parking_lot::RwLock::new(sparse::SemanticIndex::new()),
        }
    }

    /// Add a document. Returns True if added, False if text was empty.
    fn add(&self, key: String, text: String) -> bool {
        self.inner.write().add(key, text)
    }

    /// Search for the top_k most similar documents.
    /// Returns list of (key, text, similarity) tuples.
    #[pyo3(signature = (query, top_k=5))]
    fn search(&self, query: &str, top_k: usize) -> Vec<(String, String, f32)> {
        self.inner.read().search(query, top_k)
    }

    fn __len__(&self) -> usize {
        self.inner.read().len()
    }
}

// ── Sparse standalone functions ─────────────────────────────────────────

/// Sparse cosine similarity between two {dimension: weight} dicts.
#[pyfunction]
fn sparse_cosine(left: HashMap<String, f32>, right: HashMap<String, f32>) -> f32 {
    sparse::sparse_cosine_similarity(&left, &right)
}

/// Compute semantic similarity between two texts (vectorise + cosine).
#[pyfunction]
fn semantic_text_similarity(query: &str, candidate: &str) -> f32 {
    let v1 = sparse::vectorize_text(query);
    let v2 = sparse::vectorize_text(candidate);
    sparse::sparse_cosine_similarity(&v1, &v2)
}

/// Vectorise text into a sparse TF-trigram dict.
#[pyfunction]
fn py_vectorize_text(text: &str) -> HashMap<String, f32> {
    sparse::vectorize_text(text)
}

/// Tokenise text (normalise → extract → alias resolution).
#[pyfunction]
fn py_tokenize(text: &str) -> Vec<String> {
    sparse::tokenize(text)
}

/// NFKD normalise + strip combining marks + lowercase.
#[pyfunction]
fn py_normalize_text(text: &str) -> String {
    sparse::normalize_text(text)
}

/// The `arkhe_memory` Python module.
#[pymodule]
fn arkhe_memory(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Dense vector
    m.add_class::<ArkheVectorIndex>()?;
    m.add_function(wrap_pyfunction!(cosine_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(cosine_distance, m)?)?;
    m.add_function(wrap_pyfunction!(batch_cosine_top_k, m)?)?;
    // Sparse semantic
    m.add_class::<ArkheSemanticIndex>()?;
    m.add_function(wrap_pyfunction!(sparse_cosine, m)?)?;
    m.add_function(wrap_pyfunction!(semantic_text_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(py_vectorize_text, m)?)?;
    m.add_function(wrap_pyfunction!(py_tokenize, m)?)?;
    m.add_function(wrap_pyfunction!(py_normalize_text, m)?)?;
    Ok(())
}
