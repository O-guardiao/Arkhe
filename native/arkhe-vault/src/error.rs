use thiserror::Error;

#[derive(Debug, Error)]
pub enum VaultError {
    #[error("encryption failed: {reason}")]
    EncryptionFailed { reason: String },

    #[error("decryption failed — invalid key or corrupted data")]
    DecryptionFailed,

    #[error("key not found: {key}")]
    KeyNotFound { key: String },

    #[error("key already exists: {key}")]
    KeyExists { key: String },

    #[error("invalid key name: {reason}")]
    InvalidKeyName { reason: String },

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
}

pub type VaultResult<T> = Result<T, VaultError>;
