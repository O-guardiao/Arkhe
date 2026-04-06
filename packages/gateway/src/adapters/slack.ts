/**
 * Slack Adapter (outbound) — envia respostas via Slack Web API
 *
 * Analogia Python: adapters/slack_adapter no rlm/plugins/
 *
 * Usa `chat.postMessage` para enviar mensagens.
 * Respeita thread_ts se presente no envelope (reply em threads).
 *
 * Variáveis de ambiente:
 *   RLM_SLACK_BOT_TOKEN — xoxb-... Bot User OAuth Token
 */

import { childLogger } from "../logger.js";
import { chunkText } from "../chunker.js";
import type { ChannelAdapter, ChannelInfo, ProbeResult, SendResult } from "./interface.js";
import type { Envelope } from "../envelope.js";

const SLACK_API = "https://slack.com/api";
const SLACK_MAX_TEXT = 3_000;

export interface SlackAdapterConfig {
  botToken: string;
  timeoutMs?: number;
}

export class SlackAdapter implements ChannelAdapter {
  readonly channelName = "slack";

  private readonly log = childLogger({ adapter: "slack" });
  private readonly config: Required<SlackAdapterConfig>;

  private messagesSent = 0;
  private messagesReceived = 0;
  private errorCount = 0;
  private lastSeenMs: number | undefined;

  constructor(config: SlackAdapterConfig) {
    this.config = { timeoutMs: 15_000, ...config };
  }

  async sendMessage(targetId: string, text: string, envelope: Envelope): Promise<SendResult> {
    this.lastSeenMs = Date.now();

    const threadTs = envelope.metadata["slack_thread_ts"] as string | undefined;
    const chunks = chunkText(text, { maxLength: SLACK_MAX_TEXT });
    let lastResult: SendResult = { ok: false };

    for (const chunk of chunks) {
      lastResult = await this.postMessage(targetId, chunk, threadTs);
      if (!lastResult.ok) break;
    }

    return lastResult;
  }

  async sendMedia(targetId: string, url: string, _mime: string, caption?: string): Promise<SendResult> {
    this.lastSeenMs = Date.now();
    // Usa blocks para renderizar imagem (unfurl ou block kit)
    const payload = {
      channel: targetId,
      blocks: [
        {
          type: "image",
          image_url: url,
          alt_text: caption ?? "image",
        },
        ...(caption ? [{ type: "section", text: { type: "mrkdwn", text: caption } }] : []),
      ],
    };
    return this.callApi("chat.postMessage", payload);
  }

  getChannelInfo(): ChannelInfo {
    return {
      id: "slack",
      name: "Slack",
      type: "workspace",
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
      const response = await fetch(`${SLACK_API}/auth.test`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${this.config.botToken}`,
          "Content-Type": "application/json; charset=utf-8",
        },
        body: JSON.stringify({}),
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
        ok: boolean;
        user_id?: string;
        user?: string;
        bot_id?: string;
        error?: string;
      };

      if (!data.ok) {
        this.errorCount++;
        return {
          ok: false,
          elapsedMs: Date.now() - startedAt,
          error: data.error ?? "Slack auth.test failed",
        };
      }

      this.lastSeenMs = Date.now();
      return {
        ok: true,
        elapsedMs: Date.now() - startedAt,
        identity: {
          ...((data.bot_id ?? data.user_id) !== undefined ? { botId: data.bot_id ?? data.user_id } : {}),
          ...(data.user !== undefined ? { username: data.user } : {}),
          ...(data.user !== undefined ? { displayName: data.user } : {}),
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
  // Slack API helpers
  // --------------------------------------------------------------------------

  private async postMessage(
    channel: string,
    text: string,
    threadTs?: string,
  ): Promise<SendResult> {
    const payload: Record<string, unknown> = { channel, text };
    if (threadTs) payload["thread_ts"] = threadTs;
    return this.callApi("chat.postMessage", payload);
  }

  private async callApi(method: string, payload: Record<string, unknown>): Promise<SendResult> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.timeoutMs);

    try {
      const res = await fetch(`${SLACK_API}/${method}`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${this.config.botToken}`,
          "Content-Type": "application/json; charset=utf-8",
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errText = await res.text();
        this.errorCount++;
        this.log.error({ status: res.status, body: errText, method }, "Slack HTTP error");
        return { ok: false, error: `HTTP ${res.status}: ${errText}` };
      }

      const data = await res.json() as { ok: boolean; ts?: string; error?: string };

      if (!data.ok) {
        this.errorCount++;
        this.log.error({ slackError: data.error, method }, "Slack API error");
        return { ok: false, ...(data.error !== undefined ? { error: data.error } : {}) };
      }

      this.messagesSent++;
      return { ok: true, ...(data.ts !== undefined ? { messageId: data.ts } : {}) };
    } catch (err) {
      this.errorCount++;
      this.log.error({ err, method }, "Slack fetch error");
      return { ok: false, error: String(err) };
    } finally {
      clearTimeout(timeout);
    }
  }

  static fromEnv(): SlackAdapter | null {
    const token = process.env["RLM_SLACK_BOT_TOKEN"];
    if (!token) return null;
    return new SlackAdapter({ botToken: token });
  }
}
