/**
 * Adapter do Telegram — implementa ChannelAdapter para envio via Bot API.
 */

import { childLogger } from "../logger.js";
import { chunkText } from "../chunker.js";
import type { ChannelAdapter, ChannelInfo, SendResult } from "./interface.js";
import type { Envelope } from "../envelope.js";

const TELEGRAM_API_BASE = "https://api.telegram.org";
const TELEGRAM_MAX_TEXT = 4_096;

export interface TelegramAdapterConfig {
  botToken: string;
  /** Timeout de cada chamada em ms (default: 10s) */
  timeoutMs?: number;
}

export class TelegramAdapter implements ChannelAdapter {
  readonly channelName = "telegram";
  private readonly log = childLogger({ adapter: "telegram" });
  private readonly config: Required<TelegramAdapterConfig>;

  private messagesSent = 0;
  private messagesReceived = 0;
  private errorCount = 0;
  private lastSeenMs: number | undefined;

  constructor(config: TelegramAdapterConfig) {
    this.config = {
      timeoutMs: 10_000,
      ...config,
    };
  }

  async sendMessage(chatId: string, text: string, _envelope: Envelope): Promise<SendResult> {
    // Divide mensagens longas automaticamente
    const chunks = chunkText(text, { maxLength: TELEGRAM_MAX_TEXT });
    let lastResult: SendResult = { ok: false };

    for (const chunk of chunks) {
      lastResult = await this.sendTextChunk(chatId, chunk);
      if (!lastResult.ok) break;
    }

    return lastResult;
  }

  async sendMedia(chatId: string, url: string, mime: string, caption?: string): Promise<SendResult> {
    const method = mimeToSendMethod(mime);
    const fieldName = mimeToFieldName(mime);

    try {
      const body = {
        chat_id: chatId,
        [fieldName]: url,
        ...(caption ? { caption: caption.slice(0, 1024) } : {}),
      };

      const data = await this.callApi<{ message_id: number }>(method, body);
      this.messagesSent++;
      return { ok: true, messageId: String(data.message_id) };
    } catch (err) {
      this.errorCount++;
      this.log.error({ err, chatId, url }, "Failed to send media");
      return { ok: false, error: String(err) };
    }
  }

  getChannelInfo(): ChannelInfo {
    return {
      id: "telegram",
      name: "Telegram",
      type: "messaging",
      status: "healthy",
      ...(this.lastSeenMs !== undefined ? { lastSeenMs: this.lastSeenMs } : {}),
      messagesSent: this.messagesSent,
      messagesReceived: this.messagesReceived,
      errors: this.errorCount,
    };
  }

  /** Registra uma mensagem recebida (chamado pelo channel handler) */
  trackInbound(): void {
    this.messagesReceived++;
    this.lastSeenMs = Date.now();
  }

  // ---------------------------------------------------------------------------
  // Internos
  // ---------------------------------------------------------------------------

  private async sendTextChunk(chatId: string, text: string): Promise<SendResult> {
    try {
      const data = await this.callApi<{ message_id: number }>("sendMessage", {
        chat_id: chatId,
        text,
        parse_mode: "Markdown",
      });
      this.messagesSent++;
      return { ok: true, messageId: String(data.message_id) };
    } catch (err) {
      this.errorCount++;
      this.log.error({ err, chatId }, "Failed to send text chunk");
      return { ok: false, error: String(err) };
    }
  }

  private async callApi<T>(method: string, body: Record<string, unknown>): Promise<T> {
    const url = `${TELEGRAM_API_BASE}/bot${this.config.botToken}/${method}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.config.timeoutMs);

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      const json = (await response.json()) as { ok: boolean; result?: T; description?: string };

      if (!json.ok) {
        throw new Error(`Telegram API error: ${json.description ?? "unknown"}`);
      }

      return json.result as T;
    } finally {
      clearTimeout(timer);
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mimeToSendMethod(mime: string): string {
  if (mime.startsWith("image/")) return "sendPhoto";
  if (mime.startsWith("video/")) return "sendVideo";
  if (mime.startsWith("audio/")) return "sendAudio";
  return "sendDocument";
}

function mimeToFieldName(mime: string): string {
  if (mime.startsWith("image/")) return "photo";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "document";
}
