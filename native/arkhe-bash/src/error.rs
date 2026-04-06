use thiserror::Error;

#[derive(Debug, Error)]
pub enum BashError {
    #[error("sandbox violation: {reason}")]
    SandboxViolation { reason: String },

    #[error("command timed out after {timeout_ms}ms")]
    Timeout { timeout_ms: u64 },

    #[error("failed to spawn process: {0}")]
    SpawnFailed(#[from] std::io::Error),

    #[error("output exceeded max size of {max_bytes} bytes")]
    OutputOverflow { max_bytes: usize },

    #[error("permission denied: {reason}")]
    PermissionDenied { reason: String },

    #[error("command validation failed: {reason}")]
    ValidationFailed { reason: String },

    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
}

pub type BashResult<T> = Result<T, BashError>;
