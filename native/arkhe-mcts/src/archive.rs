/// ProgramArchive — MAP-Elites-like archive for resurfacing useful prior branches.
///
/// Rust reimplementation of the Python `ProgramArchive` class, with niche
/// computation running as native code for better throughput when archive
/// sizes grow.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Key describing a behavioural niche in the MAP-Elites archive.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct NicheKey(pub String);

/// Lightweight branch record stored in the archive.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArchivedBranch {
    pub branch_id: usize,
    pub total_score: f64,
    pub final_code: String,
    pub aggregated_metrics: HashMap<String, f64>,
    pub pruned_reason: Option<String>,
    pub strategy_name: Option<String>,
    /// Serialised representation of last step outputs for niche computation.
    pub last_stdout: String,
}

impl ArchivedBranch {
    /// Compute the default niche key — mirrors Python `ProgramArchive._default_niche`.
    pub fn default_niche(&self) -> NicheKey {
        // metric_names
        let mut metric_names: Vec<&str> = self
            .aggregated_metrics
            .keys()
            .map(|s| s.as_str())
            .collect();
        metric_names.sort();
        let metric_str = if metric_names.is_empty() {
            "heuristic-only".to_string()
        } else {
            metric_names.join(",")
        };

        // dominant_metric
        let dominant_metric = if self.aggregated_metrics.is_empty() {
            "heuristic".to_string()
        } else {
            self.aggregated_metrics
                .iter()
                .max_by(|a, b| a.1.abs().partial_cmp(&b.1.abs()).unwrap_or(std::cmp::Ordering::Equal))
                .map(|(k, _)| k.clone())
                .unwrap_or_else(|| "heuristic".into())
        };

        // code_bucket
        let code_len = self.final_code.trim().len();
        let code_bucket = if code_len < 80 {
            "short"
        } else if code_len < 240 {
            "medium"
        } else {
            "long"
        };

        // symbol_bucket
        let stripped = self.final_code.trim();
        let symbol_bucket = if stripped.contains("def ") {
            "function"
        } else if stripped.contains("class ") {
            "class"
        } else if stripped.contains("for ") || stripped.contains("while ") {
            "loop"
        } else {
            "assign"
        };

        // output_bucket
        let output_bucket = {
            let stdout = self.last_stdout.trim();
            if stdout.is_empty() {
                "silent"
            } else if stdout.bytes().any(|b| b.is_ascii_digit()) {
                "numeric"
            } else {
                "textual"
            }
        };

        let strategy_bucket = self
            .strategy_name
            .as_deref()
            .unwrap_or("no-strategy");

        let pruned_str = if self.pruned_reason.is_some() {
            "pruned"
        } else {
            "ok"
        };

        NicheKey(format!(
            "{metric_str}|dominant={dominant_metric}|{code_bucket}|{symbol_bucket}|\
             {output_bucket}|strategy={strategy_bucket}|{pruned_str}"
        ))
    }
}

/// MAP-Elites archive: one best branch per behavioural niche.
pub struct ProgramArchive {
    max_size: usize,
    entries: HashMap<NicheKey, ArchivedBranch>,
}

impl ProgramArchive {
    pub fn new(max_size: usize) -> Self {
        Self {
            max_size: max_size.max(1),
            entries: HashMap::new(),
        }
    }

    /// Insert/update branches. Each branch is assigned to its default niche;
    /// it replaces the current occupant only if it scores higher.
    pub fn update(&mut self, branches: &[ArchivedBranch]) {
        for branch in branches {
            if branch.total_score <= -999.0 {
                continue;
            }
            let niche = branch.default_niche();
            let dominated = match self.entries.get(&niche) {
                None => true,
                Some(current) => branch.total_score > current.total_score,
            };
            if dominated {
                self.entries.insert(niche, branch.clone());
            }
        }

        // Evict lowest-scoring if over capacity
        if self.entries.len() > self.max_size {
            let mut ranked: Vec<(NicheKey, f64)> = self
                .entries
                .iter()
                .map(|(k, v)| (k.clone(), v.total_score))
                .collect();
            ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

            let keep: std::collections::HashSet<NicheKey> = ranked
                .into_iter()
                .take(self.max_size)
                .map(|(k, _)| k)
                .collect();

            self.entries.retain(|k, _| keep.contains(k));
        }
    }

    /// Return top branches sorted by score descending, optionally limited.
    pub fn sample(&self, limit: Option<usize>) -> Vec<&ArchivedBranch> {
        let mut ranked: Vec<&ArchivedBranch> = self.entries.values().collect();
        ranked.sort_by(|a, b| {
            b.total_score
                .partial_cmp(&a.total_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        match limit {
            Some(n) => ranked.into_iter().take(n).collect(),
            None => ranked,
        }
    }

    pub fn size(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

// ── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_branch(id: usize, score: f64, code: &str, stdout: &str) -> ArchivedBranch {
        let mut metrics = HashMap::new();
        metrics.insert("heuristic".into(), score);
        ArchivedBranch {
            branch_id: id,
            total_score: score,
            final_code: code.into(),
            aggregated_metrics: metrics,
            pruned_reason: None,
            strategy_name: None,
            last_stdout: stdout.into(),
        }
    }

    #[test]
    fn test_niche_computation_short_assign_numeric() {
        let b = make_branch(0, 4.0, "x = 42", "42");
        let niche = b.default_niche();
        assert!(niche.0.contains("short"));
        assert!(niche.0.contains("assign"));
        assert!(niche.0.contains("numeric"));
        assert!(niche.0.contains("ok"));
    }

    #[test]
    fn test_niche_computation_function_medium() {
        let b = make_branch(1, 3.0, "def solve():\n    result = heavy_computation()\n    return result\n\nprint(solve())\n# pad to 80+ chars ok", "hello");
        let niche = b.default_niche();
        assert!(niche.0.contains("function"));
        assert!(niche.0.contains("textual"));
    }

    #[test]
    fn test_archive_update_keeps_best_per_niche() {
        let mut archive = ProgramArchive::new(10);
        let b1 = make_branch(0, 3.0, "x = 1", "1");
        let b2 = make_branch(1, 4.0, "x = 2", "2"); // same niche (short/assign/numeric)

        archive.update(&[b1]);
        assert_eq!(archive.size(), 1);

        archive.update(&[b2]);
        assert_eq!(archive.size(), 1); // same niche, replaced
        assert_eq!(archive.sample(None)[0].branch_id, 1);
    }

    #[test]
    fn test_archive_eviction() {
        let mut archive = ProgramArchive::new(2);
        // Create 3 branches with distinct niches
        let branches = vec![
            make_branch(0, 1.0, "x = 1", ""),          // short/assign/silent
            make_branch(1, 3.0, "x = 1", "42"),         // short/assign/numeric
            make_branch(2, 2.0, "def f():\n    pass\n# pad to medium length code block here for test", "text"), // function/medium/textual
        ];
        archive.update(&branches);
        assert!(archive.size() <= 2);
        // Best two should survive
        let top = archive.sample(None);
        assert!(top.iter().all(|b| b.total_score >= 2.0));
    }

    #[test]
    fn test_archive_ignores_negative_999() {
        let mut archive = ProgramArchive::new(10);
        let b = ArchivedBranch {
            branch_id: 0,
            total_score: -999.0,
            final_code: "crash".into(),
            aggregated_metrics: HashMap::new(),
            pruned_reason: Some("exception".into()),
            strategy_name: None,
            last_stdout: "".into(),
        };
        archive.update(&[b]);
        assert_eq!(archive.size(), 0);
    }

    #[test]
    fn test_sample_limit() {
        let mut archive = ProgramArchive::new(10);
        // Ensure diverse niches: different code structures + output types
        let branches = vec![
            make_branch(0, 1.0, "x = 1", ""),                                                 // short/assign/silent
            make_branch(1, 2.0, "x = 2", "42"),                                               // short/assign/numeric
            make_branch(2, 3.0, "def f():\n    pass\n# pad to medium", "hello"),               // function/textual
            make_branch(3, 4.0, "for i in range(10):\n    print(i)\n# pad", "99"),             // loop/numeric
            make_branch(4, 5.0, "class C:\n    x = 1\n# pad to medium-ish code here", "txt"), // class/textual
        ];
        archive.update(&branches);
        assert!(archive.size() >= 3, "expected >= 3 distinct niches, got {}", archive.size());
        let top2 = archive.sample(Some(2));
        assert_eq!(top2.len(), 2);
        assert!(top2[0].total_score >= top2[1].total_score);
    }
}
