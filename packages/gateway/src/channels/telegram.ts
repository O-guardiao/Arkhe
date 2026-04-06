/**
 * Canal Telegram — converte Updates do webhook em Envelopes e os encaminha ao Brain.
 */

import type { Context } from "hono";
import { childLogger } from "../logger.js";
import { newEnvelope } from "../envelope.js";
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
