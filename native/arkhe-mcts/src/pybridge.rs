/// PyO3 bridge — exposes MCTS scoring, archive, selection, and search/replace
/// to Python as the `arkhe_mcts` native module.

use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use std::collections::HashMap;

use crate::archive::{ArchivedBranch, ProgramArchive as RustArchive};
use crate::scoring;
use crate::selection::{self, BranchScore};
use crate::search_replace;

// ── Scoring ────────────────────────────────────────────────────────────

/// Default heuristic score for a branch step (stdout, stderr, code) → float.
#[pyfunction]
fn default_score(stdout: &str, stderr: &str, code: &str) -> f64 {
    scoring::default_score(stdout, stderr, code)
}

/// Batch-score multiple (stdout, stderr, code) triples in one call.
/// Returns list of heuristic scores.
#[pyfunction]
fn batch_default_score(triples: Vec<(String, String, String)>) -> Vec<f64> {
    scoring::batch_default_score(&triples)
}

/// Evaluate a step through a scoring pipeline.
/// stages: list of (name, raw_score, weight, min_score_or_none).
/// Returns (weighted_total, pruned_stage_name_or_none).
#[pyfunction]
fn evaluate_stages(
    heuristic: f64,
    stages: Vec<(String, f64, f64, Option<f64>)>,
) -> (f64, Option<String>) {
    scoring::evaluate_stages(heuristic, &stages)
}

// ── Selection ──────────────────────────────────────────────────────────

/// Check aggressive first-step pruning: True if branch should be discarded.
#[pyfunction]
fn prune_first_step(heuristic_score: f64) -> bool {
    selection::prune_first_step(heuristic_score)
}

/// Check if early termination is warranted (best score >= max_depth * 4).
#[pyfunction]
fn should_early_terminate(best_score: f64, max_depth: usize) -> bool {
    selection::should_early_terminate(best_score, max_depth)
}

/// Rank branch scores descending. Returns list of (branch_id, total_score).
#[pyfunction]
fn rank_branches(
    branch_data: Vec<(usize, f64, usize, Option<String>, Vec<(String, f64)>)>,
) -> Vec<(usize, f64)> {
    let scores: Vec<BranchScore> = branch_data
        .into_iter()
        .map(|(id, score, steps, pruned, metrics)| BranchScore {
            branch_id: id,
            total_score: score,
            step_count: steps,
            pruned_reason: pruned,
            metrics,
        })
        .collect();

    let ranked = selection::rank_branches(&scores);
    ranked
        .into_iter()
        .map(|i| (scores[i].branch_id, scores[i].total_score))
        .collect()
}

/// Select the best branch from scored results.
/// Returns (branch_id, total_score) or None if empty.
#[pyfunction]
fn select_best(
    branch_data: Vec<(usize, f64, usize, Option<String>, Vec<(String, f64)>)>,
) -> Option<(usize, f64)> {
    let scores: Vec<BranchScore> = branch_data
        .into_iter()
        .map(|(id, score, steps, pruned, metrics)| BranchScore {
            branch_id: id,
            total_score: score,
            step_count: steps,
            pruned_reason: pruned,
            metrics,
        })
        .collect();

    selection::select_best(&scores).map(|b| (b.branch_id, b.total_score))
}

/// Summarize branch feedback into compact text.
#[pyfunction]
#[pyo3(signature = (branch_data, code_snippets, max_branches=3, code_preview_chars=220))]
fn summarize_feedback(
    branch_data: Vec<(usize, f64, usize, Option<String>, Vec<(String, f64)>)>,
    code_snippets: Vec<String>,
    max_branches: usize,
    code_preview_chars: usize,
) -> String {
    let scores: Vec<BranchScore> = branch_data
        .into_iter()
        .map(|(id, score, steps, pruned, metrics)| BranchScore {
            branch_id: id,
            total_score: score,
            step_count: steps,
            pruned_reason: pruned,
            metrics,
        })
        .collect();

    selection::summarize_feedback(&scores, max_branches, &code_snippets, code_preview_chars)
}

// ── Search/Replace ─────────────────────────────────────────────────────

/// Parse SEARCH/REPLACE blocks from text.
/// Returns list of (search_string, replace_string) tuples.
#[pyfunction]
fn parse_search_replace_blocks(text: &str) -> Vec<(String, String)> {
    search_replace::parse_search_replace_blocks(text)
        .into_iter()
        .map(|b| (b.search, b.replace))
        .collect()
}

/// Apply SEARCH/REPLACE blocks to base code.
/// Raises ValueError if a search block is not found.
#[pyfunction]
fn apply_search_replace_blocks(
    base_code: &str,
    blocks: Vec<(String, String)>,
) -> PyResult<String> {
    let rust_blocks: Vec<search_replace::SearchReplaceBlock> = blocks
        .into_iter()
        .map(|(s, r)| search_replace::SearchReplaceBlock {
            search: s,
            replace: r,
        })
        .collect();

    search_replace::apply_search_replace_blocks(base_code, &rust_blocks)
        .map_err(|e| PyValueError::new_err(e))
}

// ── ProgramArchive (MAP-Elites) ────────────────────────────────────────

#[pyclass]
struct ArkheProgramArchive {
    inner: RustArchive,
}

#[pymethods]
impl ArkheProgramArchive {
    #[new]
    #[pyo3(signature = (max_size=24))]
    fn new(max_size: usize) -> Self {
        Self {
            inner: RustArchive::new(max_size),
        }
    }

    /// Update archive with branch results.
    /// Each entry: (branch_id, total_score, final_code, metrics_dict, pruned_reason, strategy_name, last_stdout)
    fn update(
        &mut self,
        branches: Vec<(usize, f64, String, HashMap<String, f64>, Option<String>, Option<String>, String)>,
    ) {
        let rust_branches: Vec<ArchivedBranch> = branches
            .into_iter()
            .map(
                |(id, score, code, metrics, pruned, strategy, stdout)| ArchivedBranch {
                    branch_id: id,
                    total_score: score,
                    final_code: code,
                    aggregated_metrics: metrics,
                    pruned_reason: pruned,
                    strategy_name: strategy,
                    last_stdout: stdout,
                },
            )
            .collect();

        self.inner.update(&rust_branches);
    }

    /// Return top branches as list of dicts.
    #[pyo3(signature = (limit=None))]
    fn sample(&self, limit: Option<usize>) -> Vec<HashMap<String, PyObject>> {
        Python::with_gil(|py| {
            self.inner
                .sample(limit)
                .into_iter()
                .map(|b| {
                    let mut map: HashMap<String, PyObject> = HashMap::new();
                    map.insert("branch_id".into(), b.branch_id.to_object(py));
                    map.insert("total_score".into(), b.total_score.to_object(py));
                    map.insert("final_code".into(), b.final_code.clone().to_object(py));
                    map.insert(
                        "pruned_reason".into(),
                        b.pruned_reason.as_deref().to_object(py),
                    );
                    map.insert(
                        "strategy_name".into(),
                        b.strategy_name.as_deref().to_object(py),
                    );
                    map
                })
                .collect()
        })
    }

    fn size(&self) -> usize {
        self.inner.size()
    }

    fn __len__(&self) -> usize {
        self.inner.size()
    }
}

// ── Module registration ────────────────────────────────────────────────

#[pymodule]
fn arkhe_mcts(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Scoring
    m.add_function(wrap_pyfunction!(default_score, m)?)?;
    m.add_function(wrap_pyfunction!(batch_default_score, m)?)?;
    m.add_function(wrap_pyfunction!(evaluate_stages, m)?)?;

    // Selection
    m.add_function(wrap_pyfunction!(prune_first_step, m)?)?;
    m.add_function(wrap_pyfunction!(should_early_terminate, m)?)?;
    m.add_function(wrap_pyfunction!(rank_branches, m)?)?;
    m.add_function(wrap_pyfunction!(select_best, m)?)?;
    m.add_function(wrap_pyfunction!(summarize_feedback, m)?)?;

    // Search/Replace
    m.add_function(wrap_pyfunction!(parse_search_replace_blocks, m)?)?;
    m.add_function(wrap_pyfunction!(apply_search_replace_blocks, m)?)?;

    // Archive
    m.add_class::<ArkheProgramArchive>()?;

    Ok(())
}
