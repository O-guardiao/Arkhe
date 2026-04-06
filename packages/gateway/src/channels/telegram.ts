/**
 * Canal Telegram — converte Updates do webhook/long-polling em Envelopes e os encaminha ao Brain.
 *
 * Dois modos de operação:
 *  - Webhook: `createTelegramWebhookHandler` — recebe POST do Telegram (requer URL pública)
 *  - Long-polling: `TelegramLongPoller` — faz getUpdates em loop (padrão; não requer URL pública)
 *
 * A variável `TELEGRAM_POLLING_MODE` determina o modo.
 * Se omitida, `index.ts` ativa long-polling automaticamente quando não há `TELEGRAM_WEBHOOK_SECRET`.
 */

import type { Context } from "hono";
import { childLogger } from "../logger.js";
import { newEnvelope } from "../envelope.js";
import { ExponentialBackoff } from "../backoff.js";
import { TelegramAdapter } from "../adapters/telegram.js";

const log = childLogger({ channel: "telegram" });

// ---------------------------------------------------------------------------
// Tipos do Telegram Update (subconjunto mínimo)
// ---------------------------------------------------------------------------

interface TelegramUser {
  id: number;
  username?: string;
  first_name?: string;
  last_name?: string;
  is_bot: boolean;
}

interface TelegramChat {
  id: number;
  type: "private" | "group" | "supergroup" | "channel";
  title?: string;
  username?: string;
}

interface TelegramMessage {
  message_id: number;
  from?: TelegramUser;
  chat: TelegramChat;
  date: number;
  text?: string;
  caption?: string;
  photo?: Array<{ file_id: string; width: number; height: number; file_size?: number }>;
  voice?: { file_id: string; duration: number; mime_type?: string };
  document?: { file_id: string; file_name?: string; mime_type?: string };
  reply_to_message?: TelegramMessage;
}

interface TelegramCallbackQuery {
  id: string;
  from: TelegramUser;
  message?: TelegramMessage;
  data?: string;
}

export interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessage;
  edited_message?: TelegramMessage;
  callback_query?: TelegramCallbackQuery;
}

// ---------------------------------------------------------------------------
// Conversor Update → Envelope
// ---------------------------------------------------------------------------

export function updateToEnvelope(update: TelegramUpdate) {
  const msg = update.message ?? update.edited_message;
  const cb = update.callback_query;

  if (msg) {
    const senderId = msg.from ? String(msg.from.id) : "unknown";
    const chatId = String(msg.chat.id);
    const text = msg.text ?? msg.caption ?? "";

    const envelope = newEnvelope({
      source_channel: "telegram",
      source_id: chatId,
      source_client_id: `telegram:${senderId}`,
      direction: "inbound",
      message_type: "text",
      text,
      metadata: {
        chat_id: chatId,
        message_id: msg.message_id,
        username: msg.from?.username,
        first_name: msg.from?.first_name,
        reply_to_message_id: msg.reply_to_message?.message_id,
      },
    });

    return { envelope, chatId };
  }

  if (cb) {
    const senderId = String(cb.from.id);
    const chatId = cb.message ? String(cb.message.chat.id) : senderId;

    const envelope = newEnvelope({
      source_channel: "telegram",
      source_id: chatId,
      source_client_id: `telegram:${senderId}`,
      direction: "inbound",
      message_type: "action",
      text: cb.data ?? "",
      metadata: {
        callback_query_id: cb.id,
        data: cb.data ?? "",
        reply_to_message_id: cb.message?.message_id,
        username: cb.from.username,
        first_name: cb.from.first_name,
      },
    });

    return { envelope, chatId };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Handler do webhook Hono
// ---------------------------------------------------------------------------

export type BrainSender = (chatId: string, text: string) => Promise<void>;

export function createTelegramWebhookHandler(adapter: TelegramAdapter, sendToBrain: BrainSender) {
  return async (c: Context): Promise<Response> => {
    let raw: unknown;

    try {
      raw = await c.req.json();
    } catch {
      return c.json({ ok: false, error: "invalid json" }, 400);
    }

    const update = raw as TelegramUpdate;

    try {
      const result = updateToEnvelope(update);

      if (!result) {
        // Tipo de update não suportado — responder 200 para evitar retry do Telegram
        return c.json({ ok: true, skipped: true });
      }

      const { envelope, chatId } = result;
      adapter.trackInbound();

      log.info(
        { update_id: update.update_id, message_type: envelope.message_type, chatId },
        "Telegram update received",
      );

      // Envio ao Brain de forma assíncrona para responder imediatamente ao Telegram
      void sendToBrain(chatId, JSON.stringify(envelope)).catch((err) => {
        log.error({ err }, "Failed to forward envelope to brain");
      });

      return c.json({ ok: true });
    } catch (err) {
      log.error({ err, update_id: update.update_id }, "Error processing Telegram update");
      // Retornar 200 para evitar loop de retentativa do Telegram
      return c.json({ ok: true, error: "internal error" });
    }
  };
}

// ---------------------------------------------------------------------------
// Long-Polling (modo padrão — não requer URL pública)
// ---------------------------------------------------------------------------

const TELEGRAM_API_BASE = "https://api.telegram.org";

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export interface TelegramPollerOptions {
  /** Timeout do long-poll em segundos (default: 30). */
  pollTimeoutSec?: number;
  /** Máximo de erros consecutivos antes de parar (default: 10). */
  maxConsecutiveErrors?: number;
}

/**
 * TelegramLongPoller — substitui o `telegram_gateway.py` para ambientes sem URL pública.
 *
 * Faz `getUpdates` com long-polling perpetuo e encaminha cada update ao Brain
 * via `sendToBrain`.  Responde automaticamente ao usuário usando `adapter.sendMessage`.
 *
 * Uso:
 *   const poller = new TelegramLongPoller(botToken, adapter, sendToBrain);
 *   poller.start();
 *   // ... quando desligar:
 *   await poller.stop();
 */
export class TelegramLongPoller {
  private running = false;
  private offset = 0;
  private readonly log = childLogger({ component: "telegram-poller" });
  private readonly backoff: ExponentialBackoff;

  constructor(
    private readonly botToken: string,
    private readonly adapter: TelegramAdapter,
    private readonly sendToBrain: BrainSender,
    private readonly opts: TelegramPollerOptions = {},
  ) {
    this.backoff = new ExponentialBackoff({ initialMs: 1_000, maxMs: 30_000, multiplier: 2, jitter: true });
  }

  /** Inicia o loop de polling em background. Idempotente. */
  start(): void {
    if (this.running) return;
    this.running = true;
    this.log.info("Telegram long-polling started");
    void this.pollLoop();
  }

  /** Para o poller graciosamente. */
  async stop(): Promise<void> {
    this.running = false;
    this.log.info("Telegram long-polling stopped");
  }

  // -------------------------------------------------------------------------
  // Loop interno
  // -------------------------------------------------------------------------

  private async pollLoop(): Promise<void> {
    const maxErrors = this.opts.maxConsecutiveErrors ?? 10;
    const pollTimeout = this.opts.pollTimeoutSec ?? 30;
    let consecutiveErrors = 0;

    while (this.running) {
      try {
        const updates = await this.getUpdates(pollTimeout);
        consecutiveErrors = 0;
        this.backoff.reset();

        for (const update of updates) {
          // Atualiza offset ANTES de processar — garante progresso mesmo com erro individual
          this.offset = update.update_id + 1;

          try {
            const result = updateToEnvelope(update);
            if (!result) continue;

            const { envelope, chatId } = result;
            this.adapter.trackInbound();

            this.log.info(
              { update_id: update.update_id, message_type: envelope.message_type, chatId },
              "Telegram update via long-polling",
            );

            void this.sendToBrain(chatId, JSON.stringify(envelope)).catch((err) => {
              this.log.error({ err, update_id: update.update_id }, "Failed to forward envelope to brain");
            });
          } catch (err) {
            this.log.error({ err, update_id: update.update_id }, "Error processing individual update");
          }
        }
      } catch (err: unknown) {
        consecutiveErrors++;
        this.log.error({ err, consecutiveErrors, maxErrors }, "Long-polling error");

        if (consecutiveErrors >= maxErrors) {
          this.log.fatal({ maxErrors }, "Max consecutive errors reached; stopping poller");
          this.running = false;
          break;
        }

        const delay = this.backoff.next();
        this.log.warn({ delayMs: delay }, "Retrying after backoff");
        await sleepMs(delay);
      }
    }
  }

  private async getUpdates(timeoutSec: number): Promise<TelegramUpdate[]> {
    const url = `${TELEGRAM_API_BASE}/bot${this.botToken}/getUpdates`;
    const body: Record<string, unknown> = {
      offset: this.offset,
      timeout: timeoutSec,
      allowed_updates: ["message", "edited_message", "callback_query"],
    };

    // Timeout total = poll timeout + 10s de folga para latência de rede
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), (timeoutSec + 10) * 1_000);

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (!response.ok) {
        const text = await response.text().catch(() => "");
        throw new Error(`Telegram API HTTP ${response.status}: ${text.slice(0, 200)}`);
      }

      const data = (await response.json()) as { ok: boolean; result?: TelegramUpdate[]; description?: string };

      if (!data.ok) {
        throw new Error(`Telegram API error: ${data.description ?? "unknown"}`);
      }

      return data.result ?? [];
    } finally {
      clearTimeout(timer);
    }
  }
}
