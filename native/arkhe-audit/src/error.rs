use thiserror::Error;

#[derive(Debug, Error)]
pub enum AuditError {
    #[error("log is sealed and cannot accept new entries")]
    LogSealed,

    #[error("chain integrity violation at sequence {seq}: expected hash {expected}, got {actual}")]
    IntegrityViolation {
        seq: u64,
        expected: String,
        actual: String,
    },

    #[error("empty log — nothing to verify")]
    EmptyLog,

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
}

pub type AuditResult<T> = Result<T, AuditError>;
