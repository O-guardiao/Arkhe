/// MCTS branch scoring — Rust-accelerated heuristic + multi-stage pipeline.
///
/// Mirrors `default_score_fn` from Python mcts.py but runs as native code,
/// enabling batch scoring across branches without GIL overhead.

use serde::{Deserialize, Serialize};

/// Components of a single step score, kept separate for observability.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoreComponents {
    pub heuristic: f64,
    pub stage_scores: Vec<(String, f64)>,
    pub total: f64,
}

/// Default heuristic scoring — identical logic to Python `default_score_fn`.
///
/// Rules (additive):
///   +2.0  — no error in stderr
///   +1.0  — stdout is non-empty
///   +1.0  — stdout contains at least one digit
///   -2.0  — stderr contains 'Error' or 'Traceback'
///   -1.0  — stdout is empty
///   -0.5  — code is fewer than 30 chars
///
/// Returns a value in roughly [-3, 4].
pub fn default_score(stdout: &str, stderr: &str, code: &str) -> f64 {
    let mut score: f64 = 0.0;

    let has_error = !stderr.is_empty()
        && (stderr.contains("Error") || stderr.contains("Traceback"));

    if !has_error {
        score += 2.0;
    } else {
        score -= 2.0;
    }

    let stdout_trimmed = stdout.trim();
    if !stdout_trimmed.is_empty() {
        score += 1.0;
        if stdout_trimmed.bytes().any(|b| b.is_ascii_digit()) {
            score += 1.0;
        }
    } else {
        score -= 1.0;
    }

    if code.trim().len() < 30 {
        score -= 0.5;
    }

    score
}

/// Score a batch of (stdout, stderr, code) triples in one call.
/// Returns Vec<f64> of heuristic scores, one per triple.
pub fn batch_default_score(triples: &[(String, String, String)]) -> Vec<f64> {
    triples
        .iter()
        .map(|(stdout, stderr, code)| default_score(stdout, stderr, code))
        .collect()
}

/// Evaluate a branch step through a pipeline of named scoring stages.
///
/// `stage_scores` are provided externally (from Python evaluation callbacks).
/// This function applies weights and checks min_score thresholds.
///
/// Returns (weighted_total, pruned_stage_name_or_none).
pub fn evaluate_stages(
    heuristic: f64,
    stage_results: &[(String, f64, f64, Option<f64>)], // (name, raw_score, weight, min_score)
) -> (f64, Option<String>) {
    let mut total = heuristic;

    for (name, raw_score, weight, min_score) in stage_results {
        total += weight * raw_score;
        if let Some(threshold) = min_score {
            if raw_score < threshold {
                return (total, Some(name.clone()));
            }
        }
    }

    (total, None)
}

// ── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_score_clean_numeric_output() {
        let score = default_score("42\n", "", "result = compute_answer()\nprint(result)");
        // +2 (no error) +1 (non-empty) +1 (digit) = 4.0
        assert!((score - 4.0).abs() < 1e-9);
    }

    #[test]
    fn test_default_score_error() {
        let score = default_score("", "Traceback (most recent call last):\n...", "x");
        // -2 (error) -1 (empty stdout) -0.5 (short code) = -3.5
        assert!((score - (-3.5)).abs() < 1e-9);
    }

    #[test]
    fn test_default_score_empty_non_short() {
        let score = default_score("", "", "a_reasonable_length_piece_of_code = True");
        // +2 (no error) -1 (empty stdout) = 1.0
        assert!((score - 1.0).abs() < 1e-9);
    }

    #[test]
    fn test_default_score_non_numeric_output() {
        let score = default_score("hello world", "", "print('hello world')  # something");
        // +2 (no error) +1 (non-empty) +0 (no digit) = 3.0
        assert!((score - 3.0).abs() < 1e-9);
    }

    #[test]
    fn test_batch_scoring() {
        let triples = vec![
            ("42\n".into(), "".into(), "x = compute()\nprint(x)\n# padded".into()),
            ("".into(), "Error: boom".into(), "x".into()),
        ];
        let scores = batch_default_score(&triples);
        assert_eq!(scores.len(), 2);
        assert!((scores[0] - 4.0).abs() < 1e-9);
        assert!((scores[1] - (-3.5)).abs() < 1e-9);
    }

    #[test]
    fn test_evaluate_stages_no_prune() {
        let stages = vec![
            ("correctness".into(), 0.8, 2.0, Some(0.5)),
            ("style".into(), 0.6, 1.0, None),
        ];
        let (total, pruned) = evaluate_stages(3.0, &stages);
        // 3.0 + 2.0*0.8 + 1.0*0.6 = 5.2
        assert!((total - 5.2).abs() < 1e-9);
        assert!(pruned.is_none());
    }

    #[test]
    fn test_evaluate_stages_prune_on_threshold() {
        let stages = vec![
            ("correctness".into(), 0.3, 2.0, Some(0.5)), // 0.3 < 0.5 → prune
            ("style".into(), 0.9, 1.0, None),
        ];
        let (_, pruned) = evaluate_stages(3.0, &stages);
        assert_eq!(pruned, Some("correctness".into()));
    }
}
