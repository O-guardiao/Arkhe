/**
 * @arkhe/channels — ponto de entrada público.
 *
 * Re-exporta todos os tipos, classes e funções do pacote:
 *   - Tipos base (ChannelAdapter, ChannelHealth, envelopes, eventos)
 *   - ChannelRegistry
 *   - ChannelStateMachine
 *   - Allowlist (matchAllowlist, createAllowlist, AllowlistRule)
 *   - TypingIndicator
 *   - Adapters: DiscordAdapter, SlackAdapter, WhatsAppAdapter, WebchatAdapter
 */

// Core types
export type {
  ChannelAdapter,
  ChannelHealth,
  ChannelEvent,
  ChannelEventType,
  InboundEnvelope,
  OutboundEnvelope,
} from "./types.js";

// Registry
export { ChannelRegistry } from "./registry.js";

// State machine
export type { ChannelState, ChannelStateEvent, StateChangeListener } from "./state-machine.js";
export { ChannelStateMachine } from "./state-machine.js";

// Allowlist
export type { AllowlistRule } from "./allowlist.js";
export { AllowlistRuleSchema, matchAllowlist, createAllowlist } from "./allowlist.js";

// Typing indicators
export { TypingIndicator } from "./typing.js";

// Adapters
export type { DiscordAdapterOptions, FetchLike as DiscordFetchLike } from "./adapters/discord.js";
export { DiscordAdapter } from "./adapters/discord.js";

export type { SlackAdapterOptions, FetchLike as SlackFetchLike } from "./adapters/slack.js";
export { SlackAdapter } from "./adapters/slack.js";

export type { WhatsAppAdapterOptions, FetchLike as WhatsAppFetchLike } from "./adapters/whatsapp.js";
export { WhatsAppAdapter } from "./adapters/whatsapp.js";

export type { WebchatAdapterOptions, SseWriter } from "./adapters/webchat.js";
export { WebchatAdapter } from "./adapters/webchat.js";
