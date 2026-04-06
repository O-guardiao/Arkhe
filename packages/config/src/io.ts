import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { ZodError } from "zod";
import { RlmConfigSchema } from "./schema.js";
import type { RlmConfig } from "./types.js";

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

/**
 * Reads a JSON file at `filePath`, validates it against `RlmConfigSchema`
 * and returns the typed config.
 *
 * @throws `Error` when the file cannot be read or parsed as JSON.
 * @throws `ZodError` when the parsed value does not satisfy the schema.
 */
export async function loadConfig(filePath: string): Promise<RlmConfig> {
  let raw: string;
  try {
    raw = await fs.readFile(filePath, "utf8");
  } catch (cause) {
    throw new Error(`Não foi possível ler o arquivo de configuração: ${filePath}`, {
      cause,
    });
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (cause) {
    throw new Error(
      `Arquivo de configuração não é um JSON válido: ${filePath}`,
      { cause }
    );
  }

  return RlmConfigSchema.parse(parsed);
}

// ---------------------------------------------------------------------------
// Write (atomic)
// ---------------------------------------------------------------------------

/**
 * Serialises `config` as pretty JSON and writes it atomically to `filePath`.
 *
 * Atomicity is achieved by writing to a sibling temp file first and then
 * renaming, so a crash mid-write never produces a truncated config file.
 */
export async function saveConfig(
  filePath: string,
  config: RlmConfig
): Promise<void> {
  // Validate before writing — refuse to persist broken configs.
  RlmConfigSchema.parse(config);

  const content = JSON.stringify(config, null, 2) + "\n";

  // Ensure the target directory exists.
  await fs.mkdir(dirname(filePath), { recursive: true });

  // Write to a temp file in the same directory so rename is atomic on POSIX
  // (and as atomic as Windows allows).
  const tmp = join(tmpdir(), `rlm-config-${Date.now()}-${process.pid}.json`);
  await fs.writeFile(tmp, content, "utf8");

  try {
    await fs.rename(tmp, filePath);
  } catch (cause) {
    // Clean up the temp file if rename fails.
    await fs.unlink(tmp).catch(() => undefined);
    throw new Error(
      `Falha ao persistir o arquivo de configuração em: ${filePath}`,
      { cause }
    );
  }
}

// ---------------------------------------------------------------------------
// Merge
// ---------------------------------------------------------------------------

/**
 * Performs a shallow-deep merge of `overrides` into `base`.
 *
 * - Top-level keys (`agent`, `daemon`, `security`) are merged field-by-field
 *   so that a partial override only replaces the provided sub-fields.
 * - `channels` is replaced entirely if present in `overrides`; otherwise
 *   the base array is kept (channel ordering is explicit, not mergeable).
 *
 * The result is validated against the schema before being returned.
 *
 * @throws `ZodError` when the merged config is invalid.
 */
export function mergeConfig(
  base: RlmConfig,
  overrides: Partial<RlmConfig>
): RlmConfig {
  const merged: RlmConfig = {
    agent: overrides.agent !== undefined
      ? { ...base.agent, ...overrides.agent }
      : base.agent,
    channels: overrides.channels !== undefined
      ? overrides.channels
      : base.channels,
    daemon: overrides.daemon !== undefined
      ? { ...base.daemon, ...overrides.daemon }
      : base.daemon,
    security: overrides.security !== undefined
      ? { ...base.security, ...overrides.security }
      : base.security,
  };

  // Re-validate after merge to catch invalid combinations.
  return RlmConfigSchema.parse(merged);
}

export { ZodError };
