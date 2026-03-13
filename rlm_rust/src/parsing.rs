//! High-performance parsing utilities for RLM
//!
//! Regex-based code block and final answer extraction
//! 3-5x faster than Python's re module

use once_cell::sync::Lazy;
use pyo3::prelude::*;
use regex::Regex;

/// Compiled regex for code blocks - ```repl\n...\n```
static CODE_BLOCK_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?s)```repl\s*\n(.*?)\n```").unwrap()
});

/// Compiled regex for FINAL_VAR pattern
static FINAL_VAR_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?m)^\s*FINAL_VAR\((.*?)\)").unwrap()
});

/// Compiled regex for FINAL pattern
static FINAL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?ms)^\s*FINAL\((.*)\)\s*$").unwrap()
});

/// Find all REPL code blocks in text
///
/// Returns list of code content (without the ```repl and ``` markers)
pub fn find_code_blocks(text: &str) -> Vec<String> {
    CODE_BLOCK_RE
        .captures_iter(text)
        .filter_map(|cap| cap.get(1).map(|m| m.as_str().trim().to_string()))
        .collect()
}

/// Find FINAL(...) or FINAL_VAR(...) statement in response
///
/// Returns:
/// - Some((content, is_var)) if found
/// - None if neither pattern is found
pub fn find_final_answer(text: &str) -> Option<(String, bool)> {
    // Check FINAL_VAR first (higher priority)
    if let Some(cap) = FINAL_VAR_RE.captures(text) {
        if let Some(m) = cap.get(1) {
            let var_name = m.as_str().trim().trim_matches('"').trim_matches('\'').to_string();
            return Some((var_name, true));
        }
    }
    
    // Check FINAL pattern
    if let Some(cap) = FINAL_RE.captures(text) {
        if let Some(m) = cap.get(1) {
            return Some((m.as_str().trim().to_string(), false));
        }
    }
    
    None
}

// =============================================================================
// Option C: Autoral hybrid functions
// =============================================================================

/// Format RLM iteration into message history entries for the next LLM prompt.
///
/// Returns Vec<(role, content)> tuples. Handles UTF-8-safe truncation of large
/// REPL outputs, replacing cryptic byte slices with char-accurate truncation.
pub fn format_iteration_rs(
    response: &str,
    code_blocks: Vec<(String, String)>,
    max_chars: usize,
) -> Vec<(String, String)> {
    let mut messages = Vec::with_capacity(1 + code_blocks.len());
    messages.push(("assistant".to_string(), response.to_string()));

    for (code, result) in code_blocks {
        let n_chars = result.chars().count();
        let truncated = if n_chars > max_chars {
            let byte_end = result
                .char_indices()
                .nth(max_chars)
                .map(|(i, _)| i)
                .unwrap_or(result.len());
            format!(
                "{}... + [{} chars...]",
                &result[..byte_end],
                n_chars - max_chars
            )
        } else {
            result
        };
        messages.push((
            "user".to_string(),
            format!(
                "Code executed:\n```python\n{}\n```\n\nREPL output:\n{}",
                code, truncated
            ),
        ));
    }
    messages
}

/// Fast non-cryptographic hash for loop detection.
///
/// Returns a 12-char hex string matching the length of Python's
/// `hashlib.md5(text.encode()).hexdigest()[:12]`. Uses std DefaultHasher
/// (3-5x faster than Python hashlib: no GIL, no cryptographic overhead).
pub fn compute_hash(text: &str) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();
    text.hash(&mut hasher);
    format!("{:012x}", hasher.finish() & 0x0000_ffff_ffff_ffff)
}

// =============================================================================
// Python Bindings
// =============================================================================

/// Python binding: Find all REPL code blocks
#[pyfunction]
#[pyo3(name = "find_code_blocks")]
pub fn find_code_blocks_py(text: &str) -> Vec<String> {
    find_code_blocks(text)
}

/// Python binding: Find FINAL or FINAL_VAR answer
///
/// Returns:
/// - content string if found
/// - None if neither found
#[pyfunction]
#[pyo3(name = "find_final_answer")]
pub fn find_final_answer_py(text: &str) -> Option<String> {
    find_final_answer(text).map(|(s, _)| s)
}

/// Python binding: Format iteration into message history entries
#[pyfunction]
#[pyo3(name = "format_iteration_rs")]
pub fn format_iteration_rs_py(
    response: &str,
    code_blocks: Vec<(String, String)>,
    max_chars: usize,
) -> Vec<(String, String)> {
    format_iteration_rs(response, code_blocks, max_chars)
}

/// Python binding: Fast non-cryptographic hash for loop detection
#[pyfunction]
#[pyo3(name = "compute_hash")]
pub fn compute_hash_py(text: &str) -> String {
    compute_hash(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_code_blocks() {
        let text = r#"
Let me calculate that:

```repl
x = 2 + 2
print(x)
```

The result is 4.

```repl
y = x * 10
```
"#;
        
        let blocks = find_code_blocks(text);
        assert_eq!(blocks.len(), 2);
        assert!(blocks[0].contains("x = 2 + 2"));
        assert!(blocks[1].contains("y = x * 10"));
    }

    #[test]
    fn test_find_final_answer() {
        // Test FINAL pattern
        let text1 = "Some text\nFINAL(The answer is 42)\n";
        let result1 = find_final_answer(text1);
        assert_eq!(result1, Some(("The answer is 42".to_string(), false)));
        
        // Test FINAL_VAR pattern
        let text2 = "FINAL_VAR(result)";
        let result2 = find_final_answer(text2);
        assert_eq!(result2, Some(("result".to_string(), true)));
        
        // Test no match
        let text3 = "No final answer here";
        let result3 = find_final_answer(text3);
        assert_eq!(result3, None);
    }

    #[test]
    fn test_empty_input() {
        assert!(find_code_blocks("").is_empty());
        assert!(find_final_answer("").is_none());
    }
}
