/// Branch selection, pruning, and ranking utilities.
///
/// These run as pure Rust functions after Python collects execution
/// results from SandboxREPLs, enabling GIL-free batch ranking.

use serde::{Deserialize, Serialize};

/// Score summary for a single branch after all depth steps.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BranchScore {
    pub branch_id: usize,
    pub total_score: f64,
    pub step_count: usize,
    pub pruned_reason: Option<String>,
    pub metrics: Vec<(String, f64)>,
}

/// Select the best branch from a set of scored branches.
/// Filters out pruned branches (score <= -999) unless all are pruned.
pub fn select_best(branches: &[BranchScore]) -> Option<&BranchScore> {
    let valid: Vec<&BranchScore> = branches
        .iter()
        .filter(|b| b.total_score > -999.0)
        .collect();

    let candidates = if valid.is_empty() { branches.iter().collect::<Vec<_>>() } else { valid };

    candidates
        .into_iter()
        .max_by(|a, b| {
            a.total_score
                .partial_cmp(&b.total_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
}

/// Check if a branch should be pruned after its first step.
/// Returns true if heuristic_score <= 0 (aggressive first-step pruning).
pub fn prune_first_step(heuristic_score: f64) -> bool {
    heuristic_score <= 0.0
}

/// Check early termination: returns true if best score so far reaches
/// the theoretical maximum for the given depth.
pub fn should_early_terminate(best_score: f64, max_depth: usize) -> bool {
    let max_possible = max_depth as f64 * 4.0;
    best_score >= max_possible
}

/// Rank branches by total_score descending. Returns indices in ranked order.
pub fn rank_branches(branches: &[BranchScore]) -> Vec<usize> {
    let mut indexed: Vec<(usize, f64)> = branches
        .iter()
        .enumerate()
        .map(|(i, b)| (i, b.total_score))
        .collect();
    indexed.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    indexed.into_iter().map(|(i, _)| i).collect()
}

/// Partition branches into (valid, pruned) based on score threshold.
pub fn partition_branches(branches: &[BranchScore]) -> (Vec<&BranchScore>, Vec<&BranchScore>) {
    let mut valid = Vec::new();
    let mut pruned = Vec::new();
    for b in branches {
        if b.total_score > -999.0 {
            valid.push(b);
        } else {
            pruned.push(b);
        }
    }
    (valid, pruned)
}

/// Summarize branch feedback into compact text — Rust port of
/// Python `summarize_branch_feedback`.
pub fn summarize_feedback(
    branches: &[BranchScore],
    max_branches: usize,
    code_snippets: &[String],
    code_preview_chars: usize,
) -> String {
    if branches.is_empty() {
        return "No successful branches were available for feedback reuse.".into();
    }

    let mut lines: Vec<String> = Vec::with_capacity(max_branches);
    for (i, branch) in branches.iter().take(max_branches).enumerate() {
        let metrics_str = if branch.metrics.is_empty() {
            "no-metrics".to_string()
        } else {
            branch
                .metrics
                .iter()
                .map(|(name, val)| format!("{name}={val:.2}"))
                .collect::<Vec<_>>()
                .join(", ")
        };

        let code_preview = if i < code_snippets.len() {
            let s = code_snippets[i]
                .trim()
                .replace('\r', " ")
                .replace('\n', " ");
            if s.len() > code_preview_chars {
                s[..code_preview_chars].to_string()
            } else {
                s
            }
        } else {
            String::new()
        };

        let pruned = branch
            .pruned_reason
            .as_deref()
            .unwrap_or("none");

        lines.push(format!(
            "- Branch {}: total_score={:.2}; metrics=[{}]; pruned_reason={}; final_code={}",
            branch.branch_id, branch.total_score, metrics_str, pruned, code_preview
        ));
    }
    lines.join("\n")
}

// ── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_score(id: usize, score: f64) -> BranchScore {
        BranchScore {
            branch_id: id,
            total_score: score,
            step_count: 2,
            pruned_reason: None,
            metrics: vec![("heuristic".into(), score)],
        }
    }

    #[test]
    fn test_select_best() {
        let branches = vec![make_score(0, 2.0), make_score(1, 4.0), make_score(2, 3.0)];
        let best = select_best(&branches).unwrap();
        assert_eq!(best.branch_id, 1);
    }

    #[test]
    fn test_select_best_all_pruned() {
        let branches = vec![
            BranchScore {
                branch_id: 0,
                total_score: -999.0,
                step_count: 1,
                pruned_reason: Some("exception".into()),
                metrics: vec![],
            },
            BranchScore {
                branch_id: 1,
                total_score: -999.0,
                step_count: 1,
                pruned_reason: Some("heuristic-first-step".into()),
                metrics: vec![],
            },
        ];
        // Should still return one (least bad)
        let best = select_best(&branches);
        assert!(best.is_some());
    }

    #[test]
    fn test_prune_first_step() {
        assert!(prune_first_step(0.0));
        assert!(prune_first_step(-1.5));
        assert!(!prune_first_step(0.5));
    }

    #[test]
    fn test_early_termination() {
        assert!(should_early_terminate(8.0, 2)); // 8 >= 2*4
        assert!(!should_early_terminate(7.0, 2)); // 7 < 8
    }

    #[test]
    fn test_rank_branches() {
        let branches = vec![make_score(0, 1.0), make_score(1, 4.0), make_score(2, 3.0)];
        let ranked = rank_branches(&branches);
        assert_eq!(ranked, vec![1, 2, 0]);
    }

    #[test]
    fn test_partition_branches() {
        let branches = vec![
            make_score(0, 3.0),
            BranchScore {
                branch_id: 1,
                total_score: -999.0,
                step_count: 1,
                pruned_reason: Some("pruned".into()),
                metrics: vec![],
            },
            make_score(2, 1.0),
        ];
        let (valid, pruned) = partition_branches(&branches);
        assert_eq!(valid.len(), 2);
        assert_eq!(pruned.len(), 1);
    }

    #[test]
    fn test_summarize_feedback() {
        let branches = vec![make_score(0, 4.0), make_score(1, 2.0)];
        let codes = vec!["x = compute()\nprint(x)".into(), "y = other()\nprint(y)".into()];
        let summary = summarize_feedback(&branches, 3, &codes, 220);
        assert!(summary.contains("Branch 0"));
        assert!(summary.contains("total_score=4.00"));
    }
}
