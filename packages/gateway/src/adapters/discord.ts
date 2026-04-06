/**
 * Discord Adapter (outbound) — envia respostas via Discord REST API
 *
 * Analogia Python: adapters/discord_adapter no rlm/plugins/
 *
 * Estratégia de envio:
 *   1. Se o routing_key contiver o interaction_id → usa follow-up de interaction
 *   2. Caso contrário → envia via webhook de canal (channel message)
 *
 * Variáveis de ambiente:
 *   RLM_DISCORD_BOT_TOKEN    — token do bot (Bot <token>)
 *   RLM_DISCORD_APP_ID       — application ID para follow-up de interactions
 */

import { childLogger } from "../logger.js";
import { chunkText } from "../chunker.js";
import type { ChannelAdapter, ChannelInfo, ProbeResult, SendResult } from "./interface.js";
import type { Envelope } from "../envelope.js";

const DISCORD_API = "https://discord.com/api/v10";
const DISCORD_MAX_CONTENT = 2_000;

export interface DiscordAdapterConfig {
  botToken: string;
  appId: string;
  timeoutMs?: number;
}

export class DiscordAdapter implements ChannelAdapter {
  readonly channelName = "discord";

  private readonly log = childLogger({ adapter: "discord" });
  private readonly config: Required<DiscordAdapterConfig>;

  private messagesSent = 0;
  private messagesReceived = 0;
  private errorCount = 0;
  private lastSeenMs: number | undefined;

  constructor(config: DiscordAdapterConfig) {
    this.config = { timeoutMs: 15_000, ...config };
  }

  async sendMessage(targetId: string, text: string, envelope: Envelope): Promise<SendResult> {
    this.lastSeenMs = Date.now();

    // targetId pode ser "channelId" ou "interactionId:token"
    const interactionToken = (envelope.metadata["discord_interaction_token"] as string | undefined);
    const interactionId = (envelope.metadata["discord_interaction_id"] as string | undefined);

    const chunks = chunkText(text, { maxLength: DISCORD_MAX_CONTENT });
    let lastResult: SendResult = { ok: false };

    for (let i = 0; i < chunks.length; i++) {
      const chunk = chunks[i]!;

      if (interactionId && interactionToken && i === 0) {
        lastResult = await this.sendInteractionFollowup(interactionId, interactionToken, chunk);
      } else if (interactionId && interactionToken && i > 0) {
        lastResult = await this.sendInteractionFollowup(interactionId, interactionToken, chunk);
      } else {
        lastResult = await this.sendChannelMessage(targetId, chunk);
      }

      if (!lastResult.ok) break;
    }

    return lastResult;
  }

  async sendMedia(targetId: string, url: string, _mime: string, caption?: string): Promise<SendResult> {
    this.lastSeenMs = Date.now();
    // Discord suporta embeds com URL imagem
    const payload: Record<string, unknown> = {
      embeds: [{ image: { url }, description: caption ?? "" }],
    };
    return this.apiPost(`/channels/${targetId}/messages`, payload);
  }

  getChannelInfo(): ChannelInfo {
    return {
      id: "discord",
      name: "Discord",
      type: "bot",
      status: this.config.botToken ? "healthy" : "disabled",
      ...(this.lastSeenMs !== undefined ? { lastSeenMs: this.lastSeenMs } : {}),
      messagesSent: this.messagesSent,
      messagesReceived: this.messagesReceived,
      errors: this.errorCount,
    };
  }

  async probe(timeoutMs = this.config.timeoutMs): Promise<ProbeResult> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const startedAt = Date.now();

    try {
      const response = await fetch(`${DISCORD_API}/users/@me`, {
        method: "GET",
        headers: {
          "Authorization": `Bot ${this.config.botToken}`,
          "User-Agent": "RLM-Gateway/1.0",
        },
        signal: controller.signal,
      });

      if (!response.ok) {
        const errText = await response.text();
        this.errorCount++;
        return {
          ok: false,
          elapsedMs: Date.now() - startedAt,
          error: `HTTP ${response.status}: ${errText}`,
        };
      }

      const data = await response.json() as {
        id?: string;
        username?: string;
        global_name?: string | null;
      };

      this.lastSeenMs = Date.now();
      return {
        ok: true,
        elapsedMs: Date.now() - startedAt,
        identity: {
          ...(data.id !== undefined ? { botId: data.id } : {}),
          ...(data.username !== undefined ? { username: data.username } : {}),
          ...((data.global_name ?? data.username) !== undefined ? { displayName: data.global_name ?? data.username } : {}),
        },
      };
    } catch (err) {
      this.errorCount++;
      return {
        ok: false,
        elapsedMs: Date.now() - startedAt,
        error: String(err),
      };
    } finally {
      clearTimeout(timer);
    }
  }

  // --------------------------------------------------------------------------
  // Discord REST helpers
  // --------------------------------------------------------------------------

  private async sendChannelMessage(channelId: string, content: string): Promise<SendResult> {
    return this.apiPost(`/channels/${channelId}/messages`, { content });
  }

  private async sendInteractionFollowup(
    appId: string,
    interactionToken: string,
    content: string,
  ): Promise<SendResult> {
    return this.apiPost(`/webhooks/${appId}/${interactionToken}`, { content });
  }

  private async apiPost(path: string, body: Record<string, unknown>): Promise<SendResult> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.timeoutMs);

    try {
      const res = await fetch(`${DISCORD_API}${path}`, {
        method: "POST",
        headers: {
          "Authorization": `Bot ${this.config.botToken}`,
          "Content-Type": "application/json",
          "User-Agent": "RLM-Gateway/1.0",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errText = await res.text();
        this.errorCount++;
        this.log.error({ status: res.status, body: errText, path }, "Discord API error");
        return { ok: false, error: `HTTP ${res.status}: ${errText}` };
      }

      const data = await res.json() as { id?: string };
      this.messagesSent++;
      return { ok: true, ...(data.id !== undefined ? { messageId: data.id } : {}) };
    } catch (err) {
      this.errorCount++;
      this.log.error({ err, path }, "Discord fetch error");
      return { ok: false, error: String(err) };
    } finally {
      clearTimeout(timeout);
    }
  }

  static fromEnv(): DiscordAdapter | null {
    const token = process.env["RLM_DISCORD_BOT_TOKEN"];
    const appId = process.env["RLM_DISCORD_APP_ID"];
    if (!token || !appId) return null;
    return new DiscordAdapter({ botToken: token, appId });
  }
}
