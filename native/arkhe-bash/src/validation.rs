use crate::error::{BashError, BashResult};
use once_cell::sync::Lazy;
use regex::Regex;

/// Padrões bloqueados por motivo de segurança.
/// Reflete os blocked_patterns do claw-code.
static BLOCKED_PATTERNS: Lazy<Vec<(Regex, &'static str)>> = Lazy::new(|| {
    vec![
        (
            Regex::new(r"rm\s+-[rRf]*f[rR]*\s+/").unwrap(),
            "rm -rf em raiz do sistema proibido",
        ),
        (
            Regex::new(r"dd\s+.*of=/dev/").unwrap(),
            "gravação direta em dispositivo proibida",
        ),
        (
            Regex::new(r"mkfs\b").unwrap(),
            "formatação de disco proibida",
        ),
        (
            Regex::new(r":\(\)\s*\{.*:\|:&\s*\}").unwrap(),
            "fork bomb detectada",
        ),
        (
            Regex::new(r">\s*/etc/passwd").unwrap(),
            "sobrescrever /etc/passwd proibido",
        ),
        (
            Regex::new(r">\s*/etc/shadow").unwrap(),
            "sobrescrever /etc/shadow proibido",
        ),
        (
            Regex::new(r"chmod\s+.+\s+/etc/").unwrap(),
            "alteração de permissões em /etc/ proibida",
        ),
        (
            Regex::new(r"curl\s+.*\|\s*(bash|sh|zsh)").unwrap(),
            "pipe de download para shell proibido",
        ),
        (
            Regex::new(r"wget\s+.*\|\s*(bash|sh|zsh)").unwrap(),
            "pipe de download para shell proibido",
        ),
        (
            Regex::new(r"\bsudo\b").unwrap(),
            "sudo proibido em ambiente sandbox",
        ),
        (
            Regex::new(r"\bsu\s+-").unwrap(),
            "troca de usuário proibida",
        ),
    ]
});

pub struct CommandValidator;

impl CommandValidator {
    /// Valida um comando antes de executar.
    /// Retorna Ok(()) se aprovado ou Err(BashError::ValidationFailed) se bloqueado.
    pub fn validate(command: &str) -> BashResult<()> {
        let trimmed = command.trim();

        if trimmed.is_empty() {
            return Err(BashError::ValidationFailed {
                reason: "comando vazio".to_string(),
            });
        }

        for (pattern, reason) in BLOCKED_PATTERNS.iter() {
            if pattern.is_match(trimmed) {
                return Err(BashError::ValidationFailed {
                    reason: format!("padrão bloqueado: {reason}"),
                });
            }
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_rm_rf_root() {
        assert!(CommandValidator::validate("rm -rf /home").is_err());
    }

    #[test]
    fn blocks_fork_bomb() {
        assert!(CommandValidator::validate(":(){:|:&};:").is_err());
    }

    #[test]
    fn blocks_sudo() {
        assert!(CommandValidator::validate("sudo apt update").is_err());
    }

    #[test]
    fn allows_safe_commands() {
        assert!(CommandValidator::validate("ls -la /tmp").is_ok());
        assert!(CommandValidator::validate("echo hello world").is_ok());
        assert!(CommandValidator::validate("cat /workspace/file.txt").is_ok());
    }
}
