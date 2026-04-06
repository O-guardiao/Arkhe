use crate::error::{AuditError, AuditResult};
use crate::log::AuditEntry;
use std::path::Path;
use tokio::fs::{File, OpenOptions};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tracing::{debug, info, warn};

/// Cadeia de auditoria: log JSONL append-only com verificação de integridade.
pub struct AuditChain {
    file: File,
    last_hash: String,
    seq: u64,
    is_sealed: bool,
    path: std::path::PathBuf,
}

impl AuditChain {
    /// Cria ou abre um arquivo de log existente.
    pub async fn open(path: &Path) -> AuditResult<Self> {
        // Carrega entradas existentes para recuperar last_hash e seq
        let (last_hash, seq) = if path.exists() {
            replay_chain(path).await?
        } else {
            ("genesis".to_string(), 0)
        };

        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .await?;

        info!(
            path = %path.display(),
            seq,
            "AuditChain opened"
        );

        Ok(Self {
            file,
            last_hash,
            seq,
            is_sealed: false,
            path: path.to_path_buf(),
        })
    }

    /// Adiciona uma entrada ao log.
    pub async fn append(
        &mut self,
        actor: &str,
        action: &str,
        resource: &str,
        outcome: &str,
        details: Option<serde_json::Value>,
    ) -> AuditResult<AuditEntry> {
        if self.is_sealed {
            return Err(AuditError::LogSealed);
        }

        self.seq += 1;
        let timestamp = chrono_like_now();

        let entry_hash = AuditEntry::compute_hash(
            self.seq,
            &timestamp,
            actor,
            action,
            resource,
            outcome,
            details.as_ref(),
            &self.last_hash,
        );

        let entry = AuditEntry {
            seq: self.seq,
            timestamp,
            actor: actor.to_string(),
            action: action.to_string(),
            resource: resource.to_string(),
            outcome: outcome.to_string(),
            details,
            prev_hash: self.last_hash.clone(),
            entry_hash: entry_hash.clone(),
        };

        let mut line = serde_json::to_string(&entry)?;
        line.push('\n');

        self.file.write_all(line.as_bytes()).await?;
        self.file.flush().await?;

        self.last_hash = entry_hash;

        debug!(seq = self.seq, actor, action, "Audit entry written");
        Ok(entry)
    }

    /// Veda o log — nenhuma nova entrada aceita após seal().
    pub async fn seal(&mut self) -> AuditResult<()> {
        self.append("system", "audit.seal", &self.path.display().to_string(), "success", None)
            .await?;
        self.is_sealed = true;
        info!(path = %self.path.display(), "AuditChain sealed");
        Ok(())
    }

    pub fn current_seq(&self) -> u64 {
        self.seq
    }

    pub fn is_sealed(&self) -> bool {
        self.is_sealed
    }
}

/// Verifica a integridade da cadeia de hashes a partir do arquivo.
pub async fn verify_chain_integrity(path: &Path) -> AuditResult<u64> {
    let file = tokio::fs::File::open(path).await?;
    let reader = BufReader::new(file);
    let mut lines = reader.lines();

    let mut expected_prev = "genesis".to_string();
    let mut count = 0u64;

    while let Some(line) = lines.next_line().await? {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let entry: AuditEntry = serde_json::from_str(line)?;

        // Verifica prev_hash
        if entry.prev_hash != expected_prev {
            return Err(AuditError::IntegrityViolation {
                seq: entry.seq,
                expected: expected_prev,
                actual: entry.prev_hash,
            });
        }

        // Recalcula entry_hash
        let expected_hash = AuditEntry::compute_hash(
            entry.seq,
            &entry.timestamp,
            &entry.actor,
            &entry.action,
            &entry.resource,
            &entry.outcome,
            entry.details.as_ref(),
            &entry.prev_hash,
        );

        if expected_hash != entry.entry_hash {
            warn!(seq = entry.seq, "Hash mismatch in audit chain");
            return Err(AuditError::IntegrityViolation {
                seq: entry.seq,
                expected: expected_hash,
                actual: entry.entry_hash,
            });
        }

        expected_prev = entry.entry_hash;
        count += 1;
    }

    if count == 0 {
        return Err(AuditError::EmptyLog);
    }

    Ok(count)
}

// ---------------------------------------------------------------------------
// Helpers internos
// ---------------------------------------------------------------------------

async fn replay_chain(path: &Path) -> AuditResult<(String, u64)> {
    let file = tokio::fs::File::open(path).await?;
    let reader = BufReader::new(file);
    let mut lines = reader.lines();

    let mut last_hash = "genesis".to_string();
    let mut seq = 0u64;

    while let Some(line) = lines.next_line().await? {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let entry: AuditEntry = serde_json::from_str(line)?;
        last_hash = entry.entry_hash;
        seq = entry.seq;
    }

    Ok((last_hash, seq))
}

/// Substitui chrono sem adicionar dep — usa std::time como ISO-8601 básico.
fn chrono_like_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    // Formato: seconds from epoch como string ISO-8601 aproximado
    // Em produção usar time = "0.3" com OffsetDateTime::now_utc().format(&Rfc3339)
    format!("{secs}")
}
