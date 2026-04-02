/// Hierarchical Navigable Small World (HNSW) index for approximate nearest
/// neighbour search using cosine distance.
///
/// This is a minimal but correct implementation optimised for the RLM memory
/// use-case: dimension ~1536, corpus up to ~1M vectors, query latency < 1 ms.
///
/// References:
///   Malkov & Yashunin, "Efficient and robust approximate nearest neighbor
///   using Hierarchical Navigable Small World graphs", 2018.

use ordered_float::OrderedFloat;
use parking_lot::RwLock;
use rand::Rng;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::cmp::Reverse;

use crate::vecmath::cosine_distance;

// ── Configuration ────────────────────────────────────────────────────────

const DEFAULT_M: usize = 16;          // max edges per node per layer
const DEFAULT_M_MAX0: usize = 32;     // max edges at layer 0 (2 * M)
const DEFAULT_EF_CONSTRUCTION: usize = 200;
const DEFAULT_EF_SEARCH: usize = 50;
const ML_FACTOR: f64 = 1.0;           // 1 / ln(M)

// ── Node ─────────────────────────────────────────────────────────────────

struct Node {
    id: String,
    vector: Vec<f32>,
    /// Connections per layer: layer → list of internal indices.
    connections: Vec<Vec<usize>>,
}

// ── Index ────────────────────────────────────────────────────────────────

pub struct HnswIndex {
    pub dim: usize,
    m: usize,
    m_max0: usize,
    ef_construction: usize,
    ef_search: usize,
    nodes: RwLock<Vec<Node>>,
    id_to_idx: RwLock<HashMap<String, usize>>,
    entry_point: RwLock<Option<usize>>,
    max_layer: RwLock<usize>,
}

impl HnswIndex {
    /// Create an empty index for vectors of `dim` dimensions.
    pub fn new(dim: usize) -> Self {
        Self {
            dim,
            m: DEFAULT_M,
            m_max0: DEFAULT_M_MAX0,
            ef_construction: DEFAULT_EF_CONSTRUCTION,
            ef_search: DEFAULT_EF_SEARCH,
            nodes: RwLock::new(Vec::new()),
            id_to_idx: RwLock::new(HashMap::new()),
            entry_point: RwLock::new(None),
            max_layer: RwLock::new(0),
        }
    }

    pub fn with_params(dim: usize, m: usize, ef_construction: usize, ef_search: usize) -> Self {
        Self {
            dim,
            m,
            m_max0: m * 2,
            ef_construction,
            ef_search,
            nodes: RwLock::new(Vec::new()),
            id_to_idx: RwLock::new(HashMap::new()),
            entry_point: RwLock::new(None),
            max_layer: RwLock::new(0),
        }
    }

    // ── Random level ─────────────────────────────────────────────────────

    fn random_level(&self) -> usize {
        let mut rng = rand::thread_rng();
        let r: f64 = rng.gen::<f64>();
        let ml = ML_FACTOR / (self.m as f64).ln();
        (-r.ln() * ml).floor() as usize
    }

    // ── Greedy search (layers > 0) ──────────────────────────────────────

    fn search_layer_greedy(&self, nodes: &[Node], query: &[f32], ep: usize, layer: usize) -> usize {
        let mut current = ep;
        let mut current_dist = cosine_distance(&nodes[current].vector, query);

        loop {
            let mut changed = false;
            let neighbours = &nodes[current].connections[layer];
            for &nb in neighbours {
                let d = cosine_distance(&nodes[nb].vector, query);
                if d < current_dist {
                    current = nb;
                    current_dist = d;
                    changed = true;
                }
            }
            if !changed {
                break;
            }
        }
        current
    }

    // ── ef-bounded search (layer 0 or construction) ─────────────────────

    fn search_layer_ef(
        &self,
        nodes: &[Node],
        query: &[f32],
        ep: usize,
        ef: usize,
        layer: usize,
    ) -> Vec<(usize, f32)> {
        // candidates: min-heap (closest first for picking)
        // result: max-heap (farthest first for pruning)
        let ep_dist = cosine_distance(&nodes[ep].vector, query);

        let mut candidates: BinaryHeap<Reverse<(OrderedFloat<f32>, usize)>> = BinaryHeap::new();
        let mut result: BinaryHeap<(OrderedFloat<f32>, usize)> = BinaryHeap::new();
        let mut visited: HashSet<usize> = HashSet::new();

        candidates.push(Reverse((OrderedFloat(ep_dist), ep)));
        result.push((OrderedFloat(ep_dist), ep));
        visited.insert(ep);

        while let Some(Reverse((OrderedFloat(c_dist), c_idx))) = candidates.pop() {
            // If the closest candidate is farther than the farthest in result, stop.
            let farthest_dist = result.peek().map(|(d, _)| d.0).unwrap_or(f32::MAX);
            if c_dist > farthest_dist && result.len() >= ef {
                break;
            }

            if layer < nodes[c_idx].connections.len() {
                for &nb in &nodes[c_idx].connections[layer] {
                    if visited.contains(&nb) {
                        continue;
                    }
                    visited.insert(nb);

                    let nb_dist = cosine_distance(&nodes[nb].vector, query);
                    let farthest = result.peek().map(|(d, _)| d.0).unwrap_or(f32::MAX);

                    if nb_dist < farthest || result.len() < ef {
                        candidates.push(Reverse((OrderedFloat(nb_dist), nb)));
                        result.push((OrderedFloat(nb_dist), nb));
                        if result.len() > ef {
                            result.pop(); // remove farthest
                        }
                    }
                }
            }
        }

        result.into_sorted_vec().into_iter().map(|(d, i)| (i, d.0)).collect()
    }

    // ── Select neighbours (simple heuristic) ────────────────────────────

    fn select_neighbours(candidates: &[(usize, f32)], m: usize) -> Vec<usize> {
        let mut sorted: Vec<_> = candidates.to_vec();
        sorted.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
        sorted.truncate(m);
        sorted.into_iter().map(|(idx, _)| idx).collect()
    }

    // ── INSERT ───────────────────────────────────────────────────────────

    pub fn insert(&self, id: String, vector: Vec<f32>) {
        assert_eq!(vector.len(), self.dim, "vector dimension mismatch");

        let level = self.random_level();

        // --- allocate the node ---
        let mut nodes = self.nodes.write();
        let new_idx = nodes.len();

        let mut connections = Vec::with_capacity(level + 1);
        for _ in 0..=level {
            connections.push(Vec::new());
        }
        nodes.push(Node {
            id: id.clone(),
            vector,
            connections,
        });

        self.id_to_idx.write().insert(id, new_idx);

        let entry_opt = *self.entry_point.read();
        let current_max_layer = *self.max_layer.read();

        if entry_opt.is_none() {
            // First node — just set it as entry point.
            *self.entry_point.write() = Some(new_idx);
            *self.max_layer.write() = level;
            return;
        }

        let mut ep = entry_opt.unwrap();
        let query = &nodes[new_idx].vector.clone(); // clone for borrow checker

        // --- Greedy descent from top layer to level+1 ---
        let top = current_max_layer;
        for lc in (level + 1..=top).rev() {
            ep = self.search_layer_greedy(&nodes, query, ep, lc);
        }

        // --- Insert at each layer from min(level, top) down to 0 ---
        let start_layer = level.min(top);
        for lc in (0..=start_layer).rev() {
            let neighbours = self.search_layer_ef(&nodes, query, ep, self.ef_construction, lc);

            let m_max = if lc == 0 { self.m_max0 } else { self.m };
            let selected = Self::select_neighbours(&neighbours, m_max);

            // Set outgoing edges of new node at this layer
            nodes[new_idx].connections[lc] = selected.clone();

            // Add back-links
            for &nb in &selected {
                if lc < nodes[nb].connections.len() {
                    nodes[nb].connections[lc].push(new_idx);
                    // Prune if over-connected
                    if nodes[nb].connections[lc].len() > m_max {
                        let nb_vec = nodes[nb].vector.clone();
                        let mut conn_dists: Vec<(usize, f32)> = nodes[nb].connections[lc]
                            .iter()
                            .map(|&c| (c, cosine_distance(&nodes[c].vector, &nb_vec)))
                            .collect();
                        conn_dists.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
                        conn_dists.truncate(m_max);
                        nodes[nb].connections[lc] = conn_dists.into_iter().map(|(idx, _)| idx).collect();
                    }
                }
            }

            // Update ep for next lower layer
            if !neighbours.is_empty() {
                ep = neighbours[0].0;
            }
        }

        // --- If new node is higher than current max, update entry point ---
        if level > current_max_layer {
            *self.entry_point.write() = Some(new_idx);
            *self.max_layer.write() = level;
        }
    }

    // ── SEARCH ───────────────────────────────────────────────────────────

    /// Search for `k` nearest neighbours.  Returns Vec<(id, distance)>.
    pub fn search(&self, query: &[f32], k: usize) -> Vec<(String, f32)> {
        assert_eq!(query.len(), self.dim, "query dimension mismatch");

        let nodes = self.nodes.read();
        if nodes.is_empty() {
            return Vec::new();
        }

        let entry = match *self.entry_point.read() {
            Some(ep) => ep,
            None => return Vec::new(),
        };

        let top = *self.max_layer.read();
        let ef = self.ef_search.max(k);

        let mut ep = entry;
        // Greedy descent from top layer to layer 1
        for lc in (1..=top).rev() {
            ep = self.search_layer_greedy(&nodes, query, ep, lc);
        }

        // ef-bounded search at layer 0
        let mut results = self.search_layer_ef(&nodes, query, ep, ef, 0);
        results.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(k);

        results
            .into_iter()
            .map(|(idx, dist)| (nodes[idx].id.clone(), dist))
            .collect()
    }

    // ── Utility ──────────────────────────────────────────────────────────

    pub fn len(&self) -> usize {
        self.nodes.read().len()
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.read().is_empty()
    }

    pub fn contains(&self, id: &str) -> bool {
        self.id_to_idx.read().contains_key(id)
    }

    /// Remove a vector by id (marks as deleted — tombstone).
    /// For simplicity, we replace its connections with empty vecs.
    pub fn remove(&self, id: &str) -> bool {
        let idx = match self.id_to_idx.write().remove(id) {
            Some(i) => i,
            None => return false,
        };
        let mut nodes = self.nodes.write();
        // Clear connections (soft delete)
        for layer in &mut nodes[idx].connections {
            layer.clear();
        }
        // Remove back-links from other nodes pointing to idx
        for node in nodes.iter_mut() {
            for layer in &mut node.connections {
                layer.retain(|&x| x != idx);
            }
        }
        true
    }

    /// Get all stored ids.
    pub fn ids(&self) -> Vec<String> {
        self.id_to_idx.read().keys().cloned().collect()
    }
}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_vec(dim: usize, val: f32) -> Vec<f32> {
        vec![val; dim]
    }

    #[test]
    fn test_insert_and_search_basic() {
        let idx = HnswIndex::new(3);
        idx.insert("a".into(), vec![1.0, 0.0, 0.0]);
        idx.insert("b".into(), vec![0.0, 1.0, 0.0]);
        idx.insert("c".into(), vec![0.0, 0.0, 1.0]);
        idx.insert("d".into(), vec![0.9, 0.1, 0.0]);

        let results = idx.search(&[1.0, 0.0, 0.0], 2);
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].0, "a");
        assert_eq!(results[1].0, "d");
    }

    #[test]
    fn test_search_empty() {
        let idx = HnswIndex::new(4);
        let results = idx.search(&[1.0, 0.0, 0.0, 0.0], 5);
        assert!(results.is_empty());
    }

    #[test]
    fn test_remove() {
        let idx = HnswIndex::new(3);
        idx.insert("a".into(), vec![1.0, 0.0, 0.0]);
        idx.insert("b".into(), vec![0.0, 1.0, 0.0]);
        assert_eq!(idx.len(), 2);
        assert!(idx.remove("a"));
        assert!(!idx.contains("a"));
    }

    #[test]
    fn test_larger_index() {
        let dim = 128;
        let idx = HnswIndex::new(dim);
        let mut rng = rand::thread_rng();

        // Insert 500 random vectors
        for i in 0..500 {
            let v: Vec<f32> = (0..dim).map(|_| rng.gen::<f32>()).collect();
            idx.insert(format!("v{}", i), v);
        }

        assert_eq!(idx.len(), 500);

        // Search should return something
        let q: Vec<f32> = (0..dim).map(|_| rng.gen::<f32>()).collect();
        let results = idx.search(&q, 10);
        assert_eq!(results.len(), 10);

        // Results should be sorted by distance (ascending)
        for pair in results.windows(2) {
            assert!(pair[0].1 <= pair[1].1 + 1e-6);
        }
    }
}
