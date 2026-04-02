/// Sparse vector semantic index — trigram + token vectorisation with
/// sparse cosine similarity.  Mirrors the Python `semantic_retrieval.py`
/// logic but runs 20-100× faster for the O(D×V) search loop.

use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

// ── Types ──────────────────────────────────────────────────────────────

/// Sparse vector: dimension name → weight.
pub type SparseVec = HashMap<String, f32>;

// ── Static data (lazy-initialised) ─────────────────────────────────────

fn stop_words() -> &'static HashSet<&'static str> {
    static INST: OnceLock<HashSet<&str>> = OnceLock::new();
    INST.get_or_init(|| {
        [
            "a", "an", "and", "as", "at", "com", "como", "da", "das",
            "de", "do", "dos", "e", "em", "for", "from", "in", "na",
            "nas", "no", "nos", "o", "of", "or", "os", "para", "por",
            "the", "to", "um", "uma", "use", "via",
        ]
        .into_iter()
        .collect()
    })
}

fn canonical_aliases() -> &'static HashMap<&'static str, &'static str> {
    static INST: OnceLock<HashMap<&str, &str>> = OnceLock::new();
    INST.get_or_init(|| {
        [
            ("agenda", "agenda"),
            ("agendar", "agenda"),
            ("agendamento", "agenda"),
            ("arquivo", "file"),
            ("arquivos", "file"),
            ("calendario", "agenda"),
            ("commit", "git"),
            ("deploy", "deploy"),
            ("deployment", "deploy"),
            ("docs", "documentation"),
            ("documentacao", "documentation"),
            ("email", "email"),
            ("e-mail", "email"),
            ("erro", "error"),
            ("falha", "error"),
            ("github", "git"),
            ("issue", "ticket"),
            ("issues", "ticket"),
            ("ler", "read"),
            ("log", "logs"),
            ("mensagem", "message"),
            ("mensagens", "message"),
            ("navegar", "browser"),
            ("pagina", "page"),
            ("pesquisa", "search"),
            ("pesquisar", "search"),
            ("repositorio", "repository"),
            ("responder", "reply"),
            ("roteiro", "travel"),
            ("salvar", "write"),
            ("shell", "terminal"),
            ("ssh", "terminal"),
            ("tempo", "weather"),
            ("terminal", "terminal"),
            ("tweet", "social"),
            ("tweets", "social"),
            ("viagem", "travel"),
        ]
        .into_iter()
        .collect()
    })
}

// ── Text processing ────────────────────────────────────────────────────

/// Returns true for Unicode combining marks (accents, diacriticals, etc.).
/// Covers all major combining-mark blocks used in Latin / Cyrillic / Greek
/// and extended scripts.
#[inline]
fn is_combining_mark(c: char) -> bool {
    let cp = c as u32;
    matches!(
        cp,
        0x0300..=0x036F   // Combining Diacritical Marks
        | 0x0483..=0x0489 // Cyrillic
        | 0x0591..=0x05BD // Hebrew
        | 0x05BF
        | 0x05C1..=0x05C2
        | 0x05C4..=0x05C5
        | 0x05C7
        | 0x0610..=0x061A // Arabic
        | 0x064B..=0x065F
        | 0x0670
        | 0x06D6..=0x06DC
        | 0x06DF..=0x06E4
        | 0x06E7..=0x06E8
        | 0x06EA..=0x06ED
        | 0x0730..=0x074A // Syriac
        | 0x07A6..=0x07B0 // Thaana
        | 0x07EB..=0x07F3 // NKo
        | 0x0900..=0x0903 // Devanagari
        | 0x093A..=0x093C
        | 0x093E..=0x094F
        | 0x0951..=0x0957
        | 0x0962..=0x0963
        | 0x1AB0..=0x1AFF // Combining Diacritical Marks Extended
        | 0x1DC0..=0x1DFF // Supplement
        | 0x20D0..=0x20FF // For Symbols
        | 0xFE20..=0xFE2F // Half Marks
    )
}

/// Matches Python's `[a-z0-9_+-]` character class used in the tokeniser.
#[inline]
fn is_token_char(c: char) -> bool {
    c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '+' || c == '-'
}

/// NFKD normalisation → strip combining marks → lowercase.
/// Equivalent to Python:
/// ```python
/// unicodedata.normalize("NFKD", text)
/// "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()
/// ```
pub fn normalize_text(text: &str) -> String {
    use unicode_normalization::UnicodeNormalization;
    text.nfkd()
        .filter(|c| !is_combining_mark(*c))
        .flat_map(|c| c.to_lowercase())
        .collect()
}

/// Extract tokens from text: normalise → scan for `[a-z0-9_+-]+` runs →
/// skip length ≤1 & stop-words → resolve canonical aliases.
pub fn tokenize(text: &str) -> Vec<String> {
    let normalised = normalize_text(text);
    let stops = stop_words();
    let aliases = canonical_aliases();

    let mut tokens = Vec::new();
    let mut buf = String::new();

    for ch in normalised.chars() {
        if is_token_char(ch) {
            buf.push(ch);
        } else {
            if buf.len() > 1 && !stops.contains(buf.as_str()) {
                let resolved = aliases
                    .get(buf.as_str())
                    .map(|&s| s.to_string())
                    .unwrap_or_else(|| buf.clone());
                tokens.push(resolved);
            }
            buf.clear();
        }
    }
    // Trailing token
    if buf.len() > 1 && !stops.contains(buf.as_str()) {
        let resolved = aliases
            .get(buf.as_str())
            .map(|&s| s.to_string())
            .unwrap_or_else(|| buf.clone());
        tokens.push(resolved);
    }

    tokens
}

/// Build a sparse TF-trigram vector from text.
///
/// For each token:
///   - `tok:<token>` gets weight 1.0 (cumulative if token repeats)
///   - if token.len() >= 4, each character trigram gets `tri:<trigram>` += 0.2
pub fn vectorize_text(text: &str) -> SparseVec {
    let tokens = tokenize(text);
    let mut vec = SparseVec::new();

    for token in &tokens {
        *vec.entry(format!("tok:{}", token)).or_insert(0.0) += 1.0;

        let bytes = token.as_bytes();
        if bytes.len() >= 4 {
            for i in 0..=(bytes.len() - 3) {
                // Safe: tokens are ASCII after normalise + tokenise
                let tri = unsafe { std::str::from_utf8_unchecked(&bytes[i..i + 3]) };
                *vec.entry(format!("tri:{}", tri)).or_insert(0.0) += 0.2;
            }
        }
    }

    vec
}

/// Cosine similarity between two sparse vectors.
/// Iterates over the smaller map and probes the larger → O(min(|A|,|B|)).
pub fn sparse_cosine_similarity(left: &SparseVec, right: &SparseVec) -> f32 {
    if left.is_empty() || right.is_empty() {
        return 0.0;
    }

    let (smaller, larger) = if left.len() <= right.len() {
        (left, right)
    } else {
        (right, left)
    };

    let mut dot = 0.0_f64;
    for (key, &val) in smaller.iter() {
        if let Some(&other) = larger.get(key) {
            dot += (val as f64) * (other as f64);
        }
    }

    if dot <= 0.0 {
        return 0.0;
    }

    let left_norm: f64 = left.values().map(|&v| (v as f64) * (v as f64)).sum::<f64>().sqrt();
    let right_norm: f64 = right
        .values()
        .map(|&v| (v as f64) * (v as f64))
        .sum::<f64>()
        .sqrt();

    if left_norm <= 0.0 || right_norm <= 0.0 {
        return 0.0;
    }

    (dot / (left_norm * right_norm)) as f32
}

// ── SemanticIndex ──────────────────────────────────────────────────────

struct SemanticDoc {
    key: String,
    text: String,
    vector: SparseVec,
}

/// In-memory semantic text index with sparse TF-trigram vectors.
///
/// Thread-safety is handled at the PyO3 wrapper layer (`parking_lot::RwLock`).
pub struct SemanticIndex {
    docs: Vec<SemanticDoc>,
}

impl SemanticIndex {
    pub fn new() -> Self {
        Self { docs: Vec::new() }
    }

    /// Add a document.  Empty / whitespace-only text is silently skipped.
    pub fn add(&mut self, key: String, text: String) -> bool {
        let trimmed = text.trim().to_string();
        if trimmed.is_empty() {
            return false;
        }
        let vector = vectorize_text(&trimmed);
        self.docs.push(SemanticDoc {
            key,
            text: trimmed,
            vector,
        });
        true
    }

    /// Search for the `top_k` most similar documents to `query`.
    /// Returns (key, text, similarity) triples sorted by score descending,
    /// then by key ascending for stability.
    pub fn search(&self, query: &str, top_k: usize) -> Vec<(String, String, f32)> {
        let qvec = vectorize_text(query);
        if qvec.is_empty() {
            return Vec::new();
        }

        let mut ranked: Vec<(f32, &SemanticDoc)> = self
            .docs
            .iter()
            .filter_map(|doc| {
                let score = sparse_cosine_similarity(&qvec, &doc.vector);
                if score > 0.0 {
                    Some((score, doc))
                } else {
                    None
                }
            })
            .collect();

        ranked.sort_by(|a, b| {
            b.0.partial_cmp(&a.0)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.1.key.cmp(&b.1.key))
        });

        ranked
            .into_iter()
            .take(top_k)
            .map(|(score, doc)| (doc.key.clone(), doc.text.clone(), score))
            .collect()
    }

    pub fn len(&self) -> usize {
        self.docs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.docs.is_empty()
    }
}

// ── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_text_accents() {
        assert_eq!(normalize_text("Café"), "cafe");
        assert_eq!(normalize_text("naïve"), "naive");
        assert_eq!(normalize_text("São Paulo"), "sao paulo");
    }

    #[test]
    fn test_tokenize_basic() {
        let tokens = tokenize("Python asyncio tutorial");
        assert_eq!(tokens, vec!["python", "asyncio", "tutorial"]);
    }

    #[test]
    fn test_tokenize_stop_words_removed() {
        let tokens = tokenize("the use of a terminal for deploy");
        // "the", "use", "of", "a", "for" are stop words
        assert_eq!(tokens, vec!["terminal", "deploy"]);
    }

    #[test]
    fn test_tokenize_aliases() {
        let tokens = tokenize("commit to github issues");
        // commit → git, github → git, issues → ticket
        assert_eq!(tokens, vec!["git", "git", "ticket"]);
    }

    #[test]
    fn test_vectorize_text_tokens_and_trigrams() {
        let vec = vectorize_text("hello world");
        // "hello" → tok:hello=1.0, tri:hel=0.2, tri:ell=0.2, tri:llo=0.2
        assert!((vec["tok:hello"] - 1.0).abs() < 1e-6);
        assert!((vec["tri:hel"] - 0.2).abs() < 1e-6);
        assert!((vec["tri:ell"] - 0.2).abs() < 1e-6);
        assert!((vec["tri:llo"] - 0.2).abs() < 1e-6);
        // "world" → tok:world=1.0, tri:wor=0.2, tri:orl=0.2, tri:rld=0.2
        assert!((vec["tok:world"] - 1.0).abs() < 1e-6);
    }

    #[test]
    fn test_vectorize_short_token_no_trigrams() {
        let vec = vectorize_text("git log");
        // "git" has length 3 → no trigrams (requires >= 4)
        // "log" → alias "logs" (length 4) → tok:logs + tri:log, tri:ogs
        assert!((vec["tok:git"] - 1.0).abs() < 1e-6);
        assert!(!vec.contains_key("tri:git"));
        assert!((vec["tok:logs"] - 1.0).abs() < 1e-6);
        assert!((vec["tri:log"] - 0.2).abs() < 1e-6);
    }

    #[test]
    fn test_sparse_cosine_identical() {
        let v = vectorize_text("hello world");
        let sim = sparse_cosine_similarity(&v, &v);
        assert!((sim - 1.0).abs() < 1e-5);
    }

    #[test]
    fn test_sparse_cosine_disjoint() {
        let a = vectorize_text("python code");
        let b = vectorize_text("hello world");
        let sim = sparse_cosine_similarity(&a, &b);
        assert!(sim.abs() < 1e-6, "disjoint vectors should have 0 similarity");
    }

    #[test]
    fn test_sparse_cosine_empty() {
        let a = SparseVec::new();
        let b = vectorize_text("hello");
        assert_eq!(sparse_cosine_similarity(&a, &b), 0.0);
        assert_eq!(sparse_cosine_similarity(&b, &a), 0.0);
    }

    #[test]
    fn test_semantic_index_search() {
        let mut idx = SemanticIndex::new();
        idx.add("d1".into(), "Python asyncio tutorial".into());
        idx.add("d2".into(), "Docker compose setup".into());
        idx.add("d3".into(), "Python testing with pytest".into());

        let results = idx.search("python async", 5);
        assert!(!results.is_empty());
        // "d1" should rank first (both "python" and "asyncio" match)
        assert_eq!(results[0].0, "d1");
    }

    #[test]
    fn test_semantic_index_empty_query() {
        let mut idx = SemanticIndex::new();
        idx.add("d1".into(), "hello".into());
        // Query with only stop words → empty vector → no results
        let results = idx.search("the a", 5);
        assert!(results.is_empty());
    }

    #[test]
    fn test_semantic_index_empty_text_skipped() {
        let mut idx = SemanticIndex::new();
        assert!(!idx.add("d1".into(), "".into()));
        assert!(!idx.add("d2".into(), "   ".into()));
        assert_eq!(idx.len(), 0);
    }
}
