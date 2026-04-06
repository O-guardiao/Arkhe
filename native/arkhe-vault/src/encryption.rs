use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Key, Nonce,
};
use hkdf::Hkdf;
use rand::RngCore;
use sha2::Sha256;
use zeroize::{Zeroize, ZeroizeOnDrop};

use crate::error::{VaultError, VaultResult};

const NONCE_SIZE: usize = 12;
const KEY_SIZE: usize = 32;

/// Chave AES-256 que é zerada ao ser dropada.
#[derive(Clone, ZeroizeOnDrop)]
pub struct EncryptionKey(#[zeroize(bound = "")] [u8; KEY_SIZE]);

impl EncryptionKey {
    /// Gera uma nova chave aleatória segura.
    pub fn new_random() -> Self {
        let mut bytes = [0u8; KEY_SIZE];
        OsRng.fill_bytes(&mut bytes);
        Self(bytes)
    }

    /// Deriva uma chave a partir de uma chave mestre e um salt usando HKDF-SHA256.
    /// `salt` e `info` devem ser únicos por contexto (ex: gateway_id).
    pub fn from_master_key_and_salt(master_key: &[u8], salt: &[u8], info: &[u8]) -> VaultResult<Self> {
        let hk = Hkdf::<Sha256>::new(Some(salt), master_key);
        let mut okm = [0u8; KEY_SIZE];
        hk.expand(info, &mut okm).map_err(|_| VaultError::EncryptionFailed {
            reason: "HKDF expansion failed — info too long".to_string(),
        })?;
        Ok(Self(okm))
    }

    pub(crate) fn as_bytes(&self) -> &[u8; KEY_SIZE] {
        &self.0
    }
}

/// Cifra `plaintext` com AES-256-GCM.
/// Layout do ciphertext: nonce(12 bytes) || ciphertext+tag
pub fn encrypt(key: &EncryptionKey, plaintext: &[u8]) -> VaultResult<Vec<u8>> {
    let cipher_key = Key::<Aes256Gcm>::from_slice(key.as_bytes());
    let cipher = Aes256Gcm::new(cipher_key);

    let mut nonce_bytes = [0u8; NONCE_SIZE];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext = cipher
        .encrypt(nonce, plaintext)
        .map_err(|_| VaultError::EncryptionFailed {
            reason: "AES-GCM encrypt failed".to_string(),
        })?;

    let mut output = Vec::with_capacity(NONCE_SIZE + ciphertext.len());
    output.extend_from_slice(&nonce_bytes);
    output.extend_from_slice(&ciphertext);

    Ok(output)
}

/// Decifra dados cifrados com `encrypt()`.
/// Retorna o plaintext ou `VaultError::DecryptionFailed`.
pub fn decrypt(key: &EncryptionKey, data: &[u8]) -> VaultResult<Vec<u8>> {
    if data.len() < NONCE_SIZE {
        return Err(VaultError::DecryptionFailed);
    }

    let (nonce_bytes, ciphertext) = data.split_at(NONCE_SIZE);
    let cipher_key = Key::<Aes256Gcm>::from_slice(key.as_bytes());
    let cipher = Aes256Gcm::new(cipher_key);
    let nonce = Nonce::from_slice(nonce_bytes);

    cipher
        .decrypt(nonce, ciphertext)
        .map_err(|_| VaultError::DecryptionFailed)
}

impl Drop for EncryptionKey {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}
