use crate::encryption::{decrypt, encrypt, EncryptionKey};
use crate::error::{VaultError, VaultResult};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use tokio::fs;
use tracing::{debug, info};
use zeroize::Zeroizing;

/// Entrada cifrada no vault (armazenamento em disco).
#[derive(Debug, Serialize, Deserialize)]
struct VaultEntry {
    /// Ciphertext em base64
    ciphertext_b64: String,
    /// Versão do schema para migração futura
    schema_version: u32,
}

/// Vault de segredos em memória com persistência opcional em arquivo.
///
/// Todos os valores são armazenados cifrados em memória.
/// A persistência usa escrita atômica (write + rename) para evitar corrupção.
pub struct Vault {
    key: EncryptionKey,
    entries: HashMap<String, Vec<u8>>, // nome → ciphertext
    persist_path: Option<PathBuf>,
}

impl Vault {
    /// Cria vault em memória sem persistência.
    pub fn in_memory(key: EncryptionKey) -> Self {
        Self {
            key,
            entries: HashMap::new(),
            persist_path: None,
        }
    }

    /// Cria vault com persistência em arquivo.
    pub fn with_path(key: EncryptionKey, path: PathBuf) -> Self {
        Self {
            key,
            entries: HashMap::new(),
            persist_path: Some(path),
        }
    }

    /// Carrega vault de arquivo existente.
    pub async fn load(key: EncryptionKey, path: PathBuf) -> VaultResult<Self> {
        let mut vault = Self::with_path(key, path.clone());

        if !path.exists() {
            return Ok(vault);
        }

        let contents = fs::read(&path).await?;
        let stored: HashMap<String, VaultEntry> = serde_json::from_slice(&contents)?;

        for (name, entry) in stored {
            validate_key_name(&name)?;
            let ciphertext = base64_decode(&entry.ciphertext_b64).map_err(|_| {
                VaultError::Io(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    format!("Invalid base64 for key: {name}"),
                ))
            })?;
            vault.entries.insert(name, ciphertext);
        }

        info!(count = vault.entries.len(), "Vault loaded");
        Ok(vault)
    }

    /// Insere um segredo. Sobrescreve se já existir (use `insert_if_absent` para idempotência).
    pub fn insert(&mut self, name: &str, value: &[u8]) -> VaultResult<()> {
        validate_key_name(name)?;
        let ciphertext = encrypt(&self.key, value)?;
        self.entries.insert(name.to_string(), ciphertext);
        debug!(key = name, "Secret stored");
        Ok(())
    }

    /// Insere somente se a chave não existir.
    pub fn insert_if_absent(&mut self, name: &str, value: &[u8]) -> VaultResult<bool> {
        if self.entries.contains_key(name) {
            return Ok(false);
        }
        self.insert(name, value)?;
        Ok(true)
    }

    /// Recupera um segredo. O valor retornado é zerado ao ser dropado.
    pub fn get(&self, name: &str) -> VaultResult<Zeroizing<Vec<u8>>> {
        let ciphertext = self
            .entries
            .get(name)
            .ok_or_else(|| VaultError::KeyNotFound { key: name.to_string() })?;

        let plaintext = decrypt(&self.key, ciphertext)?;
        Ok(Zeroizing::new(plaintext))
    }

    /// Remove um segredo.
    pub fn remove(&mut self, name: &str) -> VaultResult<()> {
        self.entries
            .remove(name)
            .ok_or_else(|| VaultError::KeyNotFound { key: name.to_string() })?;
        Ok(())
    }

    /// Lista os nomes das chaves armazenadas.
    pub fn keys(&self) -> Vec<&str> {
        self.entries.keys().map(String::as_str).collect()
    }

    /// Persiste vault em disco de forma atômica.
    pub async fn flush(&self) -> VaultResult<()> {
        let path = match &self.persist_path {
            Some(p) => p.clone(),
            None => return Ok(()),
        };

        let mut stored: HashMap<String, VaultEntry> = HashMap::new();
        for (name, ciphertext) in &self.entries {
            stored.insert(
                name.clone(),
                VaultEntry {
                    ciphertext_b64: base64_encode(ciphertext),
                    schema_version: 1,
                },
            );
        }

        let json = serde_json::to_vec_pretty(&stored)?;

        // Escrita atômica: escreve em .tmp + rename
        let tmp_path = path.with_extension("tmp");
        fs::write(&tmp_path, &json).await?;
        fs::rename(&tmp_path, &path).await?;

        debug!(path = %path.display(), "Vault flushed to disk");
        Ok(())
    }
}

fn validate_key_name(name: &str) -> VaultResult<()> {
    if name.is_empty() || name.len() > 256 {
        return Err(VaultError::InvalidKeyName {
            reason: "key name must be 1–256 chars".to_string(),
        });
    }
    if !name
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_' || c == '-' || c == '.')
    {
        return Err(VaultError::InvalidKeyName {
            reason: "key name must only contain alphanumeric, _, -, .".to_string(),
        });
    }
    Ok(())
}

fn base64_encode(data: &[u8]) -> String {
    use std::fmt::Write;
    let encoded = data
        .iter()
        .fold(String::new(), |mut s, b| {
            let _ = write!(s, "{b:02x}");
            s
        });
    // Use simple hex encoding (no external dep) — production should use base64 crate
    encoded
}

fn base64_decode(s: &str) -> Result<Vec<u8>, ()> {
    if s.len() % 2 != 0 {
        return Err(());
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).map_err(|_| ()))
        .collect()
}
