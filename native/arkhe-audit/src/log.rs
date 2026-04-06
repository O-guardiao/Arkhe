use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Uma entrada no log de auditoria com hash encadeado.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    /// Número sequencial da entrada (começa em 1)
    pub seq: u64,

    /// Timestamp ISO-8601 em UTC
    pub timestamp: String,

    /// Ator que realizou a ação (ex.: "user:123", "system", "tool:bash")
    pub actor: String,

    /// Ação executada (ex.: "tool.execute", "session.start", "permission.deny")
    pub action: String,

    /// Recurso afetado (ex.: path, tool name, session id)
    pub resource: String,

    /// Resultado: "success", "failure", "denied", "pending"
    pub outcome: String,

    /// Detalhes adicionais em JSON (opcional)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<serde_json::Value>,

    /// Hash SHA-256 da entrada anterior (hex), ou "genesis" na primeira
    pub prev_hash: String,

    /// Hash SHA-256 desta entrada completa (calculado sobre todos os campos exceto entry_hash)
    pub entry_hash: String,
}

impl AuditEntry {
    pub fn compute_hash(
        seq: u64,
        timestamp: &str,
        actor: &str,
        action: &str,
        resource: &str,
        outcome: &str,
        details: Option<&serde_json::Value>,
        prev_hash: &str,
    ) -> String {
        let mut hasher = Sha256::new();
        hasher.update(seq.to_string().as_bytes());
        hasher.update(b"\x00");
        hasher.update(timestamp.as_bytes());
        hasher.update(b"\x00");
        hasher.update(actor.as_bytes());
        hasher.update(b"\x00");
        hasher.update(action.as_bytes());
        hasher.update(b"\x00");
        hasher.update(resource.as_bytes());
        hasher.update(b"\x00");
        hasher.update(outcome.as_bytes());
        hasher.update(b"\x00");
        if let Some(d) = details {
            hasher.update(d.to_string().as_bytes());
        }
        hasher.update(b"\x00");
        hasher.update(prev_hash.as_bytes());
        hex::encode(hasher.finalize())
    }
}
