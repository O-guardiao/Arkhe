use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Configuração de sandbox — define os limites de execução.
/// Espelha claw-code `SandboxConfig`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SandboxConfig {
    /// Caminhos onde a leitura é permitida (ex.: ["/workspace"])
    pub allowed_paths: Vec<PathBuf>,

    /// Globs de caminhos bloqueados (ex.: ["**/.ssh/**", "**/secrets/**"])
    pub blocked_globs: Vec<String>,

    /// Tamanho máximo de saída em bytes (default: 1 MB)
    pub max_output_bytes: usize,

    /// Tempo máximo de execução em ms (default: 2 minutos)
    pub max_runtime_ms: u64,

    /// Permite escrita em disco dentro de allowed_paths
    pub allow_writes: bool,

    /// Permite acesso à rede
    pub allow_network: bool,
}

impl Default for SandboxConfig {
    fn default() -> Self {
        Self {
            allowed_paths: vec![],
            blocked_globs: vec![
                "**/.ssh/**".to_string(),
                "**/secrets/**".to_string(),
                "**/.env".to_string(),
                "**/.env.*".to_string(),
                "**/node_modules/**".to_string(),
            ],
            max_output_bytes: 1024 * 1024, // 1 MB
            max_runtime_ms: 120_000,       // 2 minutos
            allow_writes: false,
            allow_network: false,
        }
    }
}

impl SandboxConfig {
    /// Sandbox somente leitura — seguro para exploração de código
    pub fn for_readonly() -> Self {
        Self {
            allow_writes: false,
            allow_network: false,
            ..Self::default()
        }
    }

    /// Sandbox com permissão total — DEVE ser confirmado pelo usuário
    pub fn for_trusted(workspace: PathBuf) -> Self {
        Self {
            allowed_paths: vec![workspace],
            allow_writes: true,
            allow_network: true,
            ..Self::default()
        }
    }
}
