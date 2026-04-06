/**
 * json-output.ts — Envelope JSON padronizado para saída do CLI Arkhe.
 *
 * Porta fiel de rlm/cli/json_output.py
 */

export type Severity = "info" | "warn" | "error" | "success";

export interface CliJsonEnvelope {
  schema_version: 1;
  command: string;
  generated_at: string;
  severity: Severity;
  payload: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// buildCliJsonEnvelope
// ---------------------------------------------------------------------------

export function buildCliJsonEnvelope(
  command: string,
  payload: Record<string, unknown>,
  severity: Severity = "info",
): CliJsonEnvelope {
  return {
    schema_version: 1,
    command,
    generated_at: new Date().toISOString(),
    severity,
    payload,
  };
}

// ---------------------------------------------------------------------------
// printJsonOutput — imprime diretamente em stdout (para pipes/CI)
// ---------------------------------------------------------------------------

export function printJsonOutput(
  command: string,
  payload: Record<string, unknown>,
  severity: Severity = "info",
): void {
  const envelope = buildCliJsonEnvelope(command, payload, severity);
  process.stdout.write(JSON.stringify(envelope, null, 2) + "\n");
}
