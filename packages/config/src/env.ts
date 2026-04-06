import type { LogLevel, RlmConfig } from "./types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Returns the value of the given environment variable.
 * Throws `Error` if the variable is absent or empty.
 */
export function getRequiredEnv(key: string): string {
  const value = process.env[key];
  if (value === undefined || value === "") {
    throw new Error(
      `Variável de ambiente obrigatória ausente: ${key}. ` +
        `Defina-a antes de iniciar o processo.`
    );
  }
  return value;
}

/**
 * Returns the value of the given environment variable, or `fallback` when
 * the variable is absent or empty.
 */
export function getOptionalEnv(key: string, fallback: string): string {
  const value = process.env[key];
  return value !== undefined && value !== "" ? value : fallback;
}

// ---------------------------------------------------------------------------
// Type guards
// ---------------------------------------------------------------------------

function isLogLevel(value: string): value is LogLevel {
  return (
    value === "debug" ||
    value === "info" ||
    value === "warn" ||
    value === "error"
  );
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Reads well-known RLM environment variables and returns a `Partial<RlmConfig>`
 * that can be merged on top of file-based config or defaults.
 *
 * Supported variables:
 *
 * | Variable             | Config path               |
 * |----------------------|---------------------------|
 * | RLM_PORT             | daemon.port               |
 * | RLM_HOST             | daemon.host               |
 * | RLM_LOG_LEVEL        | daemon.log_level          |
 * | RLM_BRAIN_WS_URL     | daemon.brain_ws_url       |
 * | RLM_WS_PATH          | daemon.ws_path            |
 * | RLM_AGENT_NAME       | agent.name                |
 * | RLM_AGENT_MODEL      | agent.model               |
 * | RLM_AGENT_MAX_TOKENS | agent.max_tokens          |
 * | RLM_REQUIRE_AUTH     | security.require_auth     |
 * | RLM_ALLOWED_ORIGINS  | security.allowed_origins  |
 */
export function loadEnvConfig(): Partial<RlmConfig> {
  const result: Partial<RlmConfig> = {};

  // ── daemon ────────────────────────────────────────────────────────────────
  const portRaw = process.env["RLM_PORT"];
  const host = process.env["RLM_HOST"];
  const logLevelRaw = process.env["RLM_LOG_LEVEL"];
  const brainWsUrl = process.env["RLM_BRAIN_WS_URL"];
  const wsPath = process.env["RLM_WS_PATH"];

  const daemonPartial: Partial<RlmConfig["daemon"]> = {};

  if (portRaw !== undefined && portRaw !== "") {
    const port = parseInt(portRaw, 10);
    if (!isNaN(port) && port >= 1 && port <= 65535) {
      daemonPartial.port = port;
    }
  }
  if (host !== undefined && host !== "") {
    daemonPartial.host = host;
  }
  if (logLevelRaw !== undefined && logLevelRaw !== "") {
    if (isLogLevel(logLevelRaw)) {
      daemonPartial.log_level = logLevelRaw;
    }
  }
  if (brainWsUrl !== undefined && brainWsUrl !== "") {
    daemonPartial.brain_ws_url = brainWsUrl;
  }
  if (wsPath !== undefined && wsPath !== "") {
    daemonPartial.ws_path = wsPath;
  }

  if (Object.keys(daemonPartial).length > 0) {
    // We only set partial fields; mergeConfig will fill in the rest from defaults.
    result.daemon = daemonPartial as RlmConfig["daemon"];
  }

  // ── agent ─────────────────────────────────────────────────────────────────
  const agentName = process.env["RLM_AGENT_NAME"];
  const agentModel = process.env["RLM_AGENT_MODEL"];
  const maxTokensRaw = process.env["RLM_AGENT_MAX_TOKENS"];

  const agentPartial: Partial<RlmConfig["agent"]> = {};

  if (agentName !== undefined && agentName !== "") {
    agentPartial.name = agentName;
  }
  if (agentModel !== undefined && agentModel !== "") {
    agentPartial.model = agentModel;
  }
  if (maxTokensRaw !== undefined && maxTokensRaw !== "") {
    const maxTokens = parseInt(maxTokensRaw, 10);
    if (!isNaN(maxTokens) && maxTokens > 0) {
      agentPartial.max_tokens = maxTokens;
    }
  }

  if (Object.keys(agentPartial).length > 0) {
    result.agent = agentPartial as RlmConfig["agent"];
  }

  // ── security ──────────────────────────────────────────────────────────────
  const requireAuthRaw = process.env["RLM_REQUIRE_AUTH"];
  const allowedOriginsRaw = process.env["RLM_ALLOWED_ORIGINS"];

  const securityPartial: Partial<RlmConfig["security"]> = {};

  if (requireAuthRaw !== undefined && requireAuthRaw !== "") {
    securityPartial.require_auth =
      requireAuthRaw === "true" || requireAuthRaw === "1";
  }
  if (allowedOriginsRaw !== undefined && allowedOriginsRaw !== "") {
    securityPartial.allowed_origins = allowedOriginsRaw
      .split(",")
      .map((o) => o.trim())
      .filter((o) => o.length > 0);
  }

  if (Object.keys(securityPartial).length > 0) {
    result.security = securityPartial as RlmConfig["security"];
  }

  return result;
}
