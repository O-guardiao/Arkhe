// Public API for @arkhe/config
// All types, schemas, defaults, env helpers, I/O utilities and legacy migration.

// Types
export type {
  AgentConfig,
  ChannelConfig,
  ChannelType,
  DaemonConfig,
  LogLevel,
  RlmConfig,
  SecurityConfig,
} from "./types.js";

// Zod schemas
export {
  AgentConfigSchema,
  ChannelConfigSchema,
  DaemonConfigSchema,
  RlmConfigSchema,
  SecurityConfigSchema,
} from "./schema.js";
export type {
  AgentConfigInput,
  ChannelConfigInput,
  DaemonConfigInput,
  RlmConfigInput,
  SecurityConfigInput,
} from "./schema.js";

// Defaults
export {
  DEFAULT_AGENT_CONFIG,
  DEFAULT_CHANNELS,
  DEFAULT_DAEMON_CONFIG,
  DEFAULT_RLM_CONFIG,
  DEFAULT_SECURITY_CONFIG,
} from "./defaults.js";

// Environment variable helpers
export { getOptionalEnv, getRequiredEnv, loadEnvConfig } from "./env.js";

// Config I/O
export { loadConfig, mergeConfig, saveConfig } from "./io.js";

// Legacy migration
export { migrateLegacyConfig } from "./legacy.js";
