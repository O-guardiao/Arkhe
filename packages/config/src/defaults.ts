import type {
  AgentConfig,
  ChannelConfig,
  DaemonConfig,
  RlmConfig,
  SecurityConfig,
} from "./types.js";

// ---------------------------------------------------------------------------
// Default agent config
// ---------------------------------------------------------------------------

export const DEFAULT_AGENT_CONFIG: AgentConfig = {
  name: "arkhe-main",
  model: "gpt-4o",
  max_tokens: 4096,
  temperature: 0.7,
  tools_allowed: [],
  memory_enabled: true,
};

// ---------------------------------------------------------------------------
// Default daemon config
// ---------------------------------------------------------------------------

export const DEFAULT_DAEMON_CONFIG: DaemonConfig = {
  host: "0.0.0.0",
  port: 7860,
  ws_path: "/ws/gateway",
  brain_ws_url: "ws://localhost:8000/ws/brain",
  log_level: "info",
};

// ---------------------------------------------------------------------------
// Default security config
// ---------------------------------------------------------------------------

export const DEFAULT_SECURITY_CONFIG: SecurityConfig = {
  allowed_origins: ["http://localhost:3000", "http://localhost:7860"],
  require_auth: false,
  secret_rotation_days: 90,
};

// ---------------------------------------------------------------------------
// Default channel list (empty — channels are opt-in)
// ---------------------------------------------------------------------------

export const DEFAULT_CHANNELS: ChannelConfig[] = [];

// ---------------------------------------------------------------------------
// Root default config
// ---------------------------------------------------------------------------

export const DEFAULT_RLM_CONFIG: RlmConfig = {
  agent: DEFAULT_AGENT_CONFIG,
  channels: DEFAULT_CHANNELS,
  daemon: DEFAULT_DAEMON_CONFIG,
  security: DEFAULT_SECURITY_CONFIG,
};
