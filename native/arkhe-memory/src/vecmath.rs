/// SIMD-friendly vector math for cosine similarity / distance.
///
/// All functions operate on `&[f32]` slices.  The compiler auto-vectorises
/// the tight loops into SSE/AVX on x86 and NEON on ARM when built with
/// `-C target-cpu=native`.

#[inline]
pub fn dot_product(a: &[f32], b: &[f32]) -> f32 {
    debug_assert_eq!(a.len(), b.len());
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

#[inline]
pub fn l2_norm(v: &[f32]) -> f32 {
    v.iter().map(|x| x * x).sum::<f32>().sqrt()
}

#[inline]
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot = dot_product(a, b);
    let na = l2_norm(a);
    let nb = l2_norm(b);
    if na == 0.0 || nb == 0.0 {
        return 0.0;
    }
    dot / (na * nb)
}

/// cosine_distance = 1.0 - cosine_similarity  (used as metric in HNSW)
#[inline]
pub fn cosine_distance(a: &[f32], b: &[f32]) -> f32 {
    1.0 - cosine_similarity(a, b)
}

/// Batch cosine: compute similarity of `query` against every row in `matrix`.
/// Returns Vec<(index, similarity)> sorted descending, truncated to `top_k`.
pub fn batch_cosine_top_k(query: &[f32], matrix: &[Vec<f32>], top_k: usize) -> Vec<(usize, f32)> {
    let mut scores: Vec<(usize, f32)> = matrix
        .iter()
        .enumerate()
        .map(|(i, row)| (i, cosine_similarity(query, row)))
        .collect();
    // partial sort: only need top_k
    scores.sort_unstable_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    scores.truncate(top_k);
    scores
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_identical_vectors() {
        let v = vec![1.0f32, 2.0, 3.0];
        assert!((cosine_similarity(&v, &v) - 1.0).abs() < 1e-6);
    }

    #[test]
    fn test_orthogonal() {
        assert!((cosine_similarity(&[1.0, 0.0], &[0.0, 1.0])).abs() < 1e-6);
    }

    #[test]
    fn test_opposite() {
        assert!((cosine_similarity(&[1.0, 0.0], &[-1.0, 0.0]) - (-1.0)).abs() < 1e-6);
    }

    #[test]
    fn test_zero_vector() {
        assert_eq!(cosine_similarity(&[0.0, 0.0], &[1.0, 2.0]), 0.0);
    }

    #[test]
    fn test_batch_top_k() {
        let q = vec![1.0f32, 0.0, 0.0];
        let matrix = vec![
            vec![1.0, 0.0, 0.0], // sim=1
            vec![0.0, 1.0, 0.0], // sim=0
            vec![0.7, 0.7, 0.0], // sim≈0.707
        ];
        let top = batch_cosine_top_k(&q, &matrix, 2);
        assert_eq!(top.len(), 2);
        assert_eq!(top[0].0, 0); // closest
        assert_eq!(top[1].0, 2); // second
    }
}
