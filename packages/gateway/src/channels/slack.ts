/**
 * Slack Channel Receiver (inbound)
 *
 * Analogia Python: rlm/server/slack_gateway.py
 *
 * Verifica assinatura HMAC-SHA256 do Slack (x-slack-signature / x-slack-request-timestamp).
 * Trata Events API: url_verification, app_mention, message.im
 *
 * Variáveis de ambiente:
 *   RLM_SLACK_SIGNING_SECRET — signing secret da Slack App (obrigatório)
 *   RLM_SLACK_SKIP_VERIFY    — "true" para bypass em dev
 */

import { Hono } from "hono";
import { childLogger } from "../logger.js";
import { newEnvelope } from "../envelope.js";
import { hmacSha256Hex } from "../auth.js";
import { MessageDedup } from "../dedup.js";
import type { ChannelRegistry } from "../registry.js";

const log = childLogger({ component: "channel:slack" });

// ---------------------------------------------------------------------------
// Slack signature verification
// ---------------------------------------------------------------------------

async function verifySlackSignature(
  signingSecret: string,
  timestamp: string,
  rawBody: string,
  signature: string,
): Promise<boolean> {
  // Rejeita requests com mais de 5 minutos de diferença (replay attack protection)
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - parseInt(timestamp, 10)) > 300) return false;

  const baseString = `v0:${timestamp}:${rawBody}`;
  const expected = `v0=${await hmacSha256Hex(signingSecret, baseString)}`;

  // Constant-time comparison
  if (expected.length !== signature.length) return false;
  return Buffer.from(expected).equals(Buffer.from(signature));
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createSlackChannelHandler(registry: ChannelRegistry): Hono {
  const router = new Hono();

  const signingSecret = process.env["RLM_SLACK_SIGNING_SECRET"] ?? "";
  const skipVerify = process.env["RLM_SLACK_SKIP_VERIFY"] === "true";
  const dedup = new MessageDedup();

  if (!signingSecret && !skipVerify) {
    log.warn("RLM_SLACK_SIGNING_SECRET not set — Slack handler will reject all requests");
  }

  router.post("/slack/events", async (c) => {
    const rawBody = await c.req.text();
    const signature = c.req.header("x-slack-signature") ?? "";
    const timestamp = c.req.header("x-slack-request-timestamp") ?? "";

    // Verificação HMAC
    if (!skipVerify) {
      if (!signingSecret || !signature || !timestamp) {
        log.warn("Missing Slack signature headers");
        return c.json({ error: "unauthorized" }, 401);
      }
      const valid = await verifySlackSignature(signingSecret, timestamp, rawBody, signature);
      if (!valid) {
        log.warn("Invalid Slack HMAC signature");
        return c.json({ error: "invalid signature" }, 401);
      }
    }

    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(rawBody) as Record<string, unknown>;
    } catch {
      return c.json({ error: "bad json" }, 400);
    }

    const eventType = payload["type"] as string | undefined;

    // url_verification — Slack valida o endpoint
    if (eventType === "url_verification") {
      const challenge = payload["challenge"] as string;
      return c.json({ challenge });
    }

    // event_callback — mensagens e menções
    if (eventType === "event_callback") {
      const event = payload["event"] as Record<string, unknown> | undefined;
      if (!event) return c.json({ ok: true });

      const evType = event["type"] as string;
      const subtype = event["subtype"] as string | undefined;
      const botId = event["bot_id"] as string | undefined;

      // Ignora mensagens de bots (evita loop)
      if (botId || subtype === "bot_message") {
        return c.json({ ok: true });
      }

      const isMention = evType === "app_mention";
      const isDM = evType === "message" && (event["channel_type"] as string) === "im";

      if (!isMention && !isDM) {
        return c.json({ ok: true });
      }

      const msgId = (event["client_msg_id"] as string | undefined) ??
        `${event["channel"] as string}:${event["ts"] as string}`;
      if (dedup.seen(msgId)) {
        log.debug({ msgId }, "Duplicate Slack message, skipping");
        return c.json({ ok: true });
      }

      // Remove menção @bot do texto em app_mention
      let text = (event["text"] as string | undefined)?.trim() ?? "";
      if (isMention) text = text.replace(/<@[A-Z0-9]+>/g, "").trim();

      if (!text) return c.json({ ok: true });

      const userId = (event["user"] as string | undefined) ?? "unknown";
      const channelId = (event["channel"] as string | undefined) ?? "unknown";
      const threadTs = event["thread_ts"] as string | undefined;

      const envelope = newEnvelope({
        source_channel: "slack",
        source_id: userId,
        source_client_id: `slack:${userId}`,
        direction: "inbound",
        message_type: "text",
        text,
        metadata: {
          slack_user_id: userId,
          slack_channel_id: channelId,
          slack_thread_ts: threadTs ?? (event["ts"] as string),
          slack_event_type: evType,
        },
      });

      const forwarded = registry.forwardToBrain(envelope);
      if (!forwarded) {
        log.error({ userId, channelId }, "Brain not available for Slack event");
      } else {
        log.info({ userId, channelId, text: text.slice(0, 80) }, "Slack event forwarded to brain");
      }

      return c.json({ ok: true });
    }

    log.warn({ eventType }, "Unhandled Slack event type");
    return c.json({ ok: true });
  });

  return router;
}
