/**
 * WhatsApp Adapter (outbound) — envia mensagens via Meta Cloud API
 *
 * Analogia Python: adapters/whatsapp_adapter no rlm/plugins/
 *
 * Usa a Graph API v18 do Meta para enviar mensagens de texto e mídia.
 * Respeita o rate-limit natural (não envia mais que 1 req/s por padrão).
 *
 * Variáveis de ambiente:
 *   RLM_WHATSAPP_PHONE_NUMBER_ID — ID do número de telefone business
 *   RLM_WHATSAPP_API_TOKEN       — Bearer token da Meta Graph API
 */

import { childLogger } from "../logger.js";
import { chunkText } from "../chunker.js";
import type { ChannelAdapter, ChannelInfo, SendResult } from "./interface.js";
import type { Envelope } from "../envelope.js";

const META_GRAPH_API = "https://graph.facebook.com/v18.0";
const WHATSAPP_MAX_TEXT = 4_096;

export interface WhatsAppAdapterConfig {
  phoneNumberId: string;
  apiToken: string;
  timeoutMs?: number;
}

export class WhatsAppAdapter implements ChannelAdapter {
  readonly channelName = "whatsapp";

  private readonly log = childLogger({ adapter: "whatsapp" });
  private readonly config: Required<WhatsAppAdapterConfig>;

  private messagesSent = 0;
  private messagesReceived = 0;
  private errorCount = 0;
  private lastSeenMs: number | undefined;

  constructor(config: WhatsAppAdapterConfig) {
    this.config = { timeoutMs: 15_000, ...config };
  }

  async sendMessage(targetId: string, text: string, _envelope: Envelope): Promise<SendResult> {
    this.lastSeenMs = Date.now();

    const chunks = chunkText(text, { maxLength: WHATSAPP_MAX_TEXT });
    let lastResult: SendResult = { ok: false };

    for (const chunk of chunks) {
      lastResult = await this.sendTextMessage(targetId, chunk);
      if (!lastResult.ok) break;
    }

    return lastResult;
  }

  async sendMedia(targetId: string, url: string, mime: string, caption?: string): Promise<SendResult> {
    this.lastSeenMs = Date.now();
    const mediaType = mimeToWhatsAppType(mime);

    const payload = {
      messaging_product: "whatsapp",
      to: targetId,
      type: mediaType,
      [mediaType]: {
        link: url,
        ...(caption ? { caption: caption.slice(0, 1024) } : {}),
      },
    };

    return this.callApi(payload);
  }

  getChannelInfo(): ChannelInfo {
    return {
      id: "whatsapp",
      name: "WhatsApp",
      type: "messaging",
      status: (this.config.phoneNumberId && this.config.apiToken) ? "healthy" : "disabled",
      ...(this.lastSeenMs !== undefined ? { lastSeenMs: this.lastSeenMs } : {}),
      messagesSent: this.messagesSent,
      messagesReceived: this.messagesReceived,
      errors: this.errorCount,
    };
  }

  // --------------------------------------------------------------------------
  // Meta Graph API helpers
  // --------------------------------------------------------------------------

  private async sendTextMessage(to: string, text: string): Promise<SendResult> {
    return this.callApi({
      messaging_product: "whatsapp",
      to,
      type: "text",
      text: { body: text, preview_url: false },
    });
  }

  private async callApi(payload: Record<string, unknown>): Promise<SendResult> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.timeoutMs);

    try {
      const res = await fetch(
        `${META_GRAPH_API}/${this.config.phoneNumberId}/messages`,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${this.config.apiToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
          signal: controller.signal,
        },
      );

      if (!res.ok) {
        const errText = await res.text();
        this.errorCount++;
        this.log.error({ status: res.status, body: errText }, "WhatsApp API error");
        return { ok: false, error: `HTTP ${res.status}: ${errText}` };
      }

      const data = await res.json() as { messages?: Array<{ id: string }> };
      const msgId = data.messages?.[0]?.id;
      this.messagesSent++;
      return { ok: true, ...(msgId !== undefined ? { messageId: msgId } : {}) };
    } catch (err) {
      this.errorCount++;
      this.log.error({ err }, "WhatsApp fetch error");
      return { ok: false, error: String(err) };
    } finally {
      clearTimeout(timeout);
    }
  }

  static fromEnv(): WhatsAppAdapter | null {
    const phoneId = process.env["RLM_WHATSAPP_PHONE_NUMBER_ID"];
    const token = process.env["RLM_WHATSAPP_API_TOKEN"];
    if (!phoneId || !token) return null;
    return new WhatsAppAdapter({ phoneNumberId: phoneId, apiToken: token });
  }
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function mimeToWhatsAppType(mime: string): string {
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "document";
}
