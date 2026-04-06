import { ZodError } from "zod";
import { DEFAULT_RLM_CONFIG } from "./defaults.js";
import { RlmConfigSchema } from "./schema.js";
import type { ChannelConfig, ChannelType, LogLevel, RlmConfig } from "./types.js";

// ---------------------------------------------------------------------------
// Type guards
// ---------------------------------------------------------------------------

function isLogLevel(v: unknown): v is LogLevel {
  return v === "debug" || v === "info" || v === "warn" || v === "error";
}

function isChannelType(v: unknown): v is ChannelType {
  return (
    v === "telegram" ||
    v === "discord" ||
    v === "slack" ||
    v === "whatsapp" ||
    v === "webchat"
  );
}

function isString(v: unknown): v is string {
  return typeof v === "string";
}

function isPositiveInt(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v) && v > 0;
}

function isBool(v: unknown): v is boolean {
  return typeof v === "boolean";
}

// ---------------------------------------------------------------------------
// Legacy field extraction helpers
// ---------------------------------------------------------------------------

/**
 * Attempts to read `key` from a legacy flat-config object.
 * Returns `undefined` when the key is absent or has an unexpected type.
 */
function pick<T>(
  raw: Record<string, unknown>,
  key: string,
  guard: (v: unknown) => v is T
): T | undefined {
  const v = raw[key];
  return guard(v) ? v : undefined;
}

// ---------------------------------------------------------------------------
// Public migration function
// ---------------------------------------------------------------------------

/**
 * Converts a legacy flat-config `Record<string, unknown>` into a full
 * `RlmConfig`, applying defaults for any fields not present in the old format.
 *
 * ### Handled legacy fields
 *
 * | Legacy key           | Maps to                    |
 * |----------------------|----------------------------|
 * | `agent_name`         | agent.name                 |
 * | `model`              | agent.model                |
 * | `max_tokens`         | agent.max_tokens           |
 * | `temperature`        | agent.temperature          |
 * | `memory_enabled`     | agent.memory_enabled       |
 * | `log_level`          | daemon.log_level           |
 * | `host`               | daemon.host                |
 * | `port`               | daemon.port                |
 * | `ws_path`            | daemon.ws_path             |
 * | `brain_url` / `brain_ws_url` | daemon.brain_ws_url |
 * | `telegram_token`     | → channel entry (telegram) |
 * | `discord_token`      | → channel entry (discord)  |
 * | `require_auth`       | security.require_auth      |
 * | `allowed_origins`    | security.allowed_origins   |
 * | `secret_rotation_days` | security.secret_rotation_days |
 *
 * @throws `ZodError` when the migrated config is still invalid after applying
 * all transformations and defaults.
 */
export function migrateLegacyConfig(
  raw: Record<string, unknown>
): RlmConfig {
  // ── agent ─────────────────────────────────────────────────────────────────
  const agentName =
    pick(raw, "agent_name", isString) ??
    DEFAULT_RLM_CONFIG.agent.name;

  const model =
    pick(raw, "model", isString) ??
    DEFAULT_RLM_CONFIG.agent.model;

  const maxTokens =
    pick(raw, "max_tokens", isPositiveInt) ??
    DEFAULT_RLM_CONFIG.agent.max_tokens;

  const temperature = (() => {
    const v = raw["temperature"];
    if (typeof v === "number" && v >= 0 && v <= 2) return v;
    return DEFAULT_RLM_CONFIG.agent.temperature;
  })();

  const memoryEnabled =
    pick(raw, "memory_enabled", isBool) ??
    DEFAULT_RLM_CONFIG.agent.memory_enabled;

  const toolsAllowed = (() => {
    const v = raw["tools_allowed"];
    if (Array.isArray(v) && v.every((x): x is string => typeof x === "string")) {
      return v;
    }
    return DEFAULT_RLM_CONFIG.agent.tools_allowed;
  })();

  // ── daemon ────────────────────────────────────────────────────────────────
  const host =
    pick(raw, "host", isString) ??
    DEFAULT_RLM_CONFIG.daemon.host;

  const port = (() => {
    const v = raw["port"];
    if (typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= 65535) {
      return v;
    }
    return DEFAULT_RLM_CONFIG.daemon.port;
  })();

  const wsPath =
    pick(raw, "ws_path", isString) ??
    DEFAULT_RLM_CONFIG.daemon.ws_path;

  // Support both old name `brain_url` and new `brain_ws_url`.
  const brainWsUrl =
    pick(raw, "brain_ws_url", isString) ??
    pick(raw, "brain_url", isString) ??
    DEFAULT_RLM_CONFIG.daemon.brain_ws_url;

  const logLevelRaw = pick(raw, "log_level", isString);
  const logLevel: LogLevel = logLevelRaw !== undefined && isLogLevel(logLevelRaw)
    ? logLevelRaw
    : DEFAULT_RLM_CONFIG.daemon.log_level;

  // ── security ──────────────────────────────────────────────────────────────
  const requireAuth =
    pick(raw, "require_auth", isBool) ??
    DEFAULT_RLM_CONFIG.security.require_auth;

  const allowedOrigins = (() => {
    const v = raw["allowed_origins"];
    if (Array.isArray(v) && v.every((x): x is string => typeof x === "string")) {
      return v;
    }
    // Legacy: comma-separated string
    if (typeof v === "string" && v.trim() !== "") {
      return v.split(",").map((o) => o.trim()).filter((o) => o.length > 0);
    }
    return DEFAULT_RLM_CONFIG.security.allowed_origins;
  })();

  const secretRotationDays = (() => {
    const v = raw["secret_rotation_days"];
    if (typeof v === "number" && Number.isInteger(v) && v >= 0) return v;
    return DEFAULT_RLM_CONFIG.security.secret_rotation_days;
  })();

  // ── channels — implied from legacy tokens ─────────────────────────────────
  const channels: ChannelConfig[] = [];

  const telegramToken = pick(raw, "telegram_token", isString);
  if (telegramToken !== undefined) {
    channels.push({
      channel_id: "telegram-legacy",
      channel_type: "telegram" satisfies ChannelType,
      enabled: true,
      rate_limit_rpm: 30,
    });
  }

  const discordToken = pick(raw, "discord_token", isString);
  if (discordToken !== undefined) {
    channels.push({
      channel_id: "discord-legacy",
      channel_type: "discord" satisfies ChannelType,
      enabled: true,
      rate_limit_rpm: 60,
    });
  }

  // ── assemble and validate ─────────────────────────────────────────────────
  const candidate: RlmConfig = {
    agent: {
      name: agentName,
      model,
      max_tokens: maxTokens,
      temperature,
      tools_allowed: toolsAllowed,
      memory_enabled: memoryEnabled,
    },
    channels,
    daemon: {
      host,
      port,
      ws_path: wsPath,
      brain_ws_url: brainWsUrl,
      log_level: logLevel,
    },
    security: {
      allowed_origins: allowedOrigins,
      require_auth: requireAuth,
      secret_rotation_days: secretRotationDays,
    },
  };

  // Validate through schema — throws ZodError with full detail on failure.
  return RlmConfigSchema.parse(candidate);
}

export { ZodError };
