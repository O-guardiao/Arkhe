/**
 * Core TypeScript types and enums for RLM/Arkhe configuration.
 */

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

export type LogLevel = "debug" | "info" | "warn" | "error";

export type ChannelType =
  | "telegram"
  | "discord"
  | "slack"
  | "whatsapp"
  | "webchat";

// ---------------------------------------------------------------------------
// Agent configuration
// ---------------------------------------------------------------------------

/**
 * Settings that govern a single agent instance: model selection,
 * inference parameters and capability flags.
 */
export interface AgentConfig {
  /** Human-readable identifier for the agent, e.g. "arkhe-main". */
  name: string;
  /** LLM model identifier, e.g. "gpt-4o" or "claude-3-7-sonnet". */
  model: string;
  /** Maximum number of tokens the model may generate per turn. */
  max_tokens: number;
  /**
   * Sampling temperature in [0, 2]. Lower values are more deterministic;
   * higher values are more creative.
   */
  temperature: number;
  /**
   * Explicit allow-list of tool names the agent may invoke.
   * An empty array means no tools are allowed.
   */
  tools_allowed: string[];
  /** Whether episodic memory retrieval is active for this agent. */
  memory_enabled: boolean;
}

// ---------------------------------------------------------------------------
// Channel configuration
// ---------------------------------------------------------------------------

/**
 * Per-channel connectivity and rate-limit settings.
 */
export interface ChannelConfig {
  /** Stable unique identifier for this channel instance, e.g. "telegram-main". */
  channel_id: string;
  /** Protocol / platform this channel connects to. */
  channel_type: ChannelType;
  /** Whether the channel adapter should be started on boot. */
  enabled: boolean;
  /**
   * Maximum inbound messages per minute the adapter will accept before
   * rate-limiting the sender.
   */
  rate_limit_rpm: number;
}

// ---------------------------------------------------------------------------
// Daemon configuration
// ---------------------------------------------------------------------------

/**
 * Network and transport settings for the gateway daemon process.
 */
export interface DaemonConfig {
  /** Bind address, e.g. "0.0.0.0" or "127.0.0.1". */
  host: string;
  /** TCP port the HTTP/WebSocket server listens on. */
  port: number;
  /** URL path for the inbound WebSocket endpoint from channels. */
  ws_path: string;
  /**
   * WebSocket URL of the Python Brain service.
   * E.g. "ws://localhost:8000/ws/brain"
   */
  brain_ws_url: string;
  /** Minimum severity level emitted by the structured logger. */
  log_level: LogLevel;
}

// ---------------------------------------------------------------------------
// Security configuration
// ---------------------------------------------------------------------------

/**
 * Origin filtering, authentication requirements and key-rotation policy.
 */
export interface SecurityConfig {
  /**
   * CORS / WebSocket allowed origins.
   * Use ["*"] to allow all origins (not recommended in production).
   */
  allowed_origins: string[];
  /**
   * When true, every inbound request must carry a valid bearer token.
   * Set to false only in local/dev environments.
   */
  require_auth: boolean;
  /**
   * How many days before a shared secret is considered stale and should be
   * rotated. 0 disables automatic rotation reminders.
   */
  secret_rotation_days: number;
}

// ---------------------------------------------------------------------------
// Root config
// ---------------------------------------------------------------------------

/**
 * Top-level RLM configuration object that aggregates all subsystems.
 */
export interface RlmConfig {
  /** Core agent inference settings. */
  agent: AgentConfig;
  /**
   * One entry per active messaging channel.
   * The array may be empty when running in API-only mode.
   */
  channels: ChannelConfig[];
  /** Gateway daemon network configuration. */
  daemon: DaemonConfig;
  /** Security policies for the API surface. */
  security: SecurityConfig;
}
