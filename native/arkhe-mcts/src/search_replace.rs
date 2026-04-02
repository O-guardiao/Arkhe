/// Search/Replace block parsing and application.
///
/// Handles the SEARCH/REPLACE diff format used by the evolutionary
/// branch mutation system in MCTS.

use regex::Regex;
use std::sync::LazyLock;

/// A single search→replace pair extracted from LLM output.
#[derive(Debug, Clone, PartialEq)]
pub struct SearchReplaceBlock {
    pub search: String,
    pub replace: String,
}

static BLOCK_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?s)<<<<<<< SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>> REPLACE"
    )
    .expect("search/replace regex must compile")
});

/// Parse all SEARCH/REPLACE blocks from text.
pub fn parse_search_replace_blocks(text: &str) -> Vec<SearchReplaceBlock> {
    BLOCK_RE
        .captures_iter(text)
        .map(|cap| SearchReplaceBlock {
            search: cap[1].trim_matches('\n').to_string(),
            replace: cap[2].trim_matches('\n').to_string(),
        })
        .collect()
}

/// Apply SEARCH/REPLACE blocks sequentially to base code.
///
/// Returns `Err` if any search block is not found in the current code.
pub fn apply_search_replace_blocks(
    base_code: &str,
    blocks: &[SearchReplaceBlock],
) -> Result<String, String> {
    let mut updated = base_code.to_string();

    for block in blocks {
        if block.search.is_empty() {
            continue;
        }
        if !updated.contains(&block.search) {
            return Err(format!(
                "search block not found in elite code: {:?}",
                &block.search[..block.search.len().min(80)]
            ));
        }
        updated = updated.replacen(&block.search, &block.replace, 1);
    }

    Ok(updated)
}

// ── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_single_block() {
        let text = r#"Some preamble

<<<<<<< SEARCH
old_function()
=======
new_function()
>>>>>>> REPLACE

Some epilogue"#;
        let blocks = parse_search_replace_blocks(text);
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].search, "old_function()");
        assert_eq!(blocks[0].replace, "new_function()");
    }

    #[test]
    fn test_parse_multiple_blocks() {
        let text = r#"
<<<<<<< SEARCH
line_a
=======
line_a_v2
>>>>>>> REPLACE

more text

<<<<<<< SEARCH
line_b
=======
line_b_v2
>>>>>>> REPLACE
"#;
        let blocks = parse_search_replace_blocks(text);
        assert_eq!(blocks.len(), 2);
        assert_eq!(blocks[0].search, "line_a");
        assert_eq!(blocks[1].search, "line_b");
    }

    #[test]
    fn test_parse_no_blocks() {
        let text = "Just regular text without any search/replace markers.";
        let blocks = parse_search_replace_blocks(text);
        assert!(blocks.is_empty());
    }

    #[test]
    fn test_apply_single_block() {
        let code = "def hello():\n    old_function()\n    return True";
        let blocks = vec![SearchReplaceBlock {
            search: "old_function()".into(),
            replace: "new_function()".into(),
        }];
        let result = apply_search_replace_blocks(code, &blocks).unwrap();
        assert_eq!(result, "def hello():\n    new_function()\n    return True");
    }

    #[test]
    fn test_apply_multiple_blocks() {
        let code = "a = 1\nb = 2\nc = 3";
        let blocks = vec![
            SearchReplaceBlock {
                search: "a = 1".into(),
                replace: "a = 10".into(),
            },
            SearchReplaceBlock {
                search: "c = 3".into(),
                replace: "c = 30".into(),
            },
        ];
        let result = apply_search_replace_blocks(code, &blocks).unwrap();
        assert_eq!(result, "a = 10\nb = 2\nc = 30");
    }

    #[test]
    fn test_apply_missing_block_returns_error() {
        let code = "x = 1";
        let blocks = vec![SearchReplaceBlock {
            search: "y = 2".into(),
            replace: "y = 3".into(),
        }];
        let result = apply_search_replace_blocks(code, &blocks);
        assert!(result.is_err());
    }

    #[test]
    fn test_apply_empty_search_skipped() {
        let code = "x = 1";
        let blocks = vec![SearchReplaceBlock {
            search: "".into(),
            replace: "ignored".into(),
        }];
        let result = apply_search_replace_blocks(code, &blocks).unwrap();
        assert_eq!(result, "x = 1");
    }

    #[test]
    fn test_apply_replaces_only_first_occurrence() {
        let code = "aaa bbb aaa";
        let blocks = vec![SearchReplaceBlock {
            search: "aaa".into(),
            replace: "xxx".into(),
        }];
        let result = apply_search_replace_blocks(code, &blocks).unwrap();
        assert_eq!(result, "xxx bbb aaa");
    }
}
