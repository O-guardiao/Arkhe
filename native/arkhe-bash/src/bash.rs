use crate::error::{BashError, BashResult};
use crate::sandbox::SandboxConfig;
use crate::validation::CommandValidator;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use tokio::process::Command;
use tokio::time::{timeout, Duration};
use tracing::{debug, warn};

/// Input para execução de bash — espelha claw-code `BashCommandInput`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BashCommandInput {
    /// Comando a executar (interpretado por /bin/sh -c)
    pub command: String,

    /// Diretório de trabalho (default: /tmp)
    pub working_dir: Option<String>,

    /// Variáveis de ambiente extras
    pub env_vars: Option<HashMap<String, String>>,

    /// Timeout em ms (sobrescreve SandboxConfig.max_runtime_ms se menor)
    pub timeout_ms: Option<u64>,

    /// Se falso, spawna e retorna imediatamente sem aguardar (não captura output)
    pub wait_for_completion: bool,
}

/// Resultado de um comando bash executado.
#[derive(Debug, Serialize, Deserialize)]
pub struct BashOutput {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
    pub truncated: bool,
    pub elapsed_ms: u64,
}

pub struct BashExecutor {
    sandbox: SandboxConfig,
}

impl BashExecutor {
    pub fn new(sandbox: SandboxConfig) -> Self {
        Self { sandbox }
    }

    /// Executa o comando dentro das restrições do sandbox.
    pub async fn execute(&self, input: BashCommandInput) -> BashResult<BashOutput> {
        // 1. Validação de segurança
        CommandValidator::validate(&input.command)?;

        let effective_timeout_ms = match input.timeout_ms {
            Some(t) => t.min(self.sandbox.max_runtime_ms),
            None => self.sandbox.max_runtime_ms,
        };

        let working_dir = input
            .working_dir
            .unwrap_or_else(|| "/tmp".to_string());

        debug!(
            command = %input.command,
            timeout_ms = effective_timeout_ms,
            working_dir = %working_dir,
            "Executing bash command"
        );

        let start = std::time::Instant::now();

        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c")
            .arg(&input.command)
            .current_dir(&working_dir)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);

        if let Some(env) = &input.env_vars {
            for (key, val) in env {
                cmd.env(key, val);
            }
        }

        let execution = async {
            let child = cmd.spawn()?;
            let output = child.wait_with_output().await?;

            let elapsed_ms = start.elapsed().as_millis() as u64;
            let max = self.sandbox.max_output_bytes;

            let (stdout_raw, truncated_out) = truncate_output(output.stdout, max / 2);
            let (stderr_raw, truncated_err) = truncate_output(output.stderr, max / 2);

            let exit_code = output.status.code().unwrap_or(-1);

            Ok::<BashOutput, std::io::Error>(BashOutput {
                stdout: stdout_raw,
                stderr: stderr_raw,
                exit_code,
                truncated: truncated_out || truncated_err,
                elapsed_ms,
            })
        };

        match timeout(
            Duration::from_millis(effective_timeout_ms),
            execution,
        )
        .await
        {
            Ok(Ok(output)) => {
                debug!(
                    exit_code = output.exit_code,
                    elapsed_ms = output.elapsed_ms,
                    "Command complete"
                );
                Ok(output)
            }
            Ok(Err(io_err)) => Err(BashError::SpawnFailed(io_err)),
            Err(_elapsed) => {
                warn!(timeout_ms = effective_timeout_ms, "Command timed out");
                Err(BashError::Timeout {
                    timeout_ms: effective_timeout_ms,
                })
            }
        }
    }
}

fn truncate_output(bytes: Vec<u8>, max: usize) -> (String, bool) {
    if bytes.len() <= max {
        (String::from_utf8_lossy(&bytes).into_owned(), false)
    } else {
        let truncated = &bytes[..max];
        let s = String::from_utf8_lossy(truncated).into_owned();
        (
            format!("{s}\n[output truncado: {} bytes omitidos]", bytes.len() - max),
            true,
        )
    }
}
