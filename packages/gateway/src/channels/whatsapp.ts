/**
 * WhatsApp Channel Receiver (inbound) — Meta Cloud API
 *
 * Analogia Python: rlm/server/whatsapp_gateway.py
 *
 * GET  /whatsapp/webhook — hub challenge verification
 * POST /whatsapp/webhook — recebe mensagens do Meta, verifica HMAC, encaminha ao Brain
 *
 * Variáveis de ambiente:
 *   RLM_WHATSAPP_VERIFY_TOKEN — token de verificação do webhook (GET)
 *   RLM_WHATSAPP_APP_SECRET   — app secret para verificação HMAC (POST)
 *   RLM_WHATSAPP_SKIP_VERIFY  — "true" para bypass em dev
 */

import { Hono } from "hono";
import { childLogger } from "../logger.js";
import { newEnvelope } from "../envelope.js";
import { hmacSha256Hex } from "../auth.js";
import { MessageDedup } from "../dedup.js";
import type { ChannelRegistry } from "../registry.js";

const log = childLogger({ component: "channel:whatsapp" });

// ---------------------------------------------------------------------------
// HMAC verification
// ---------------------------------------------------------------------------

async function verifyMetaSignature(
  appSecret: string,
  rawBody: string,
  signatureHeader: string,
): Promise<boolean> {
  // X-Hub-Signature-256: sha256=<hex>
  const prefix = "sha256=";
  if (!signatureHeader.startsWith(prefix)) return false;
  const receivedHex = signatureHeader.slice(prefix.length);
  const expectedHex = await hmacSha256Hex(appSecret, rawBody);
  if (expectedHex.length !== receivedHex.length) return false;
  return Buffer.from(expectedHex).equals(Buffer.from(receivedHex));
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createWhatsAppChannelHandler(registry: ChannelRegistry): Hono {
  const router = new Hono();

  const verifyToken = process.env["RLM_WHATSAPP_VERIFY_TOKEN"] ?? "";
  const appSecret = process.env["RLM_WHATSAPP_APP_SECRET"] ?? "";
  const skipVerify = process.env["RLM_WHATSAPP_SKIP_VERIFY"] === "true";
  const dedup = new MessageDedup();

  if (!verifyToken) {
    log.warn("RLM_WHATSAPP_VERIFY_TOKEN not set — GET webhook challenge will always fail");
  }

  // GET — Meta envia este request para verificar o webhook endpoint
  router.get("/whatsapp/webhook", (c) => {
    const mode = c.req.query("hub.mode");
    const token = c.req.query("hub.verify_token");
    const challenge = c.req.query("hub.challenge");

    if (mode === "subscribe" && token === verifyToken && verifyToken) {
      log.info("WhatsApp webhook verified successfully");
      return c.text(challenge ?? "");
    }

    log.warn({ mode, token }, "WhatsApp webhook verification failed");
    return c.json({ error: "forbidden" }, 403);
  });

  // POST — recebe eventos de mensagens do Meta
  router.post("/whatsapp/webhook", async (c) => {
    const rawBody = await c.req.text();
    const sigHeader = c.req.header("x-hub-signature-256") ?? "";

    // Verificação HMAC (X-Hub-Signature-256)
    if (!skipVerify) {
      if (!appSecret || !sigHeader) {
        log.warn("Missing HMAC signature for WhatsApp webhook");
        return c.json({ error: "unauthorized" }, 401);
      }
      const valid = await verifyMetaSignature(appSecret, rawBody, sigHeader);
      if (!valid) {
        log.warn("Invalid WhatsApp HMAC signature");
        return c.json({ error: "invalid signature" }, 401);
      }
    }

    let body: Record<string, unknown>;
    try {
      body = JSON.parse(rawBody) as Record<string, unknown>;
    } catch {
      return c.json({ error: "bad json" }, 400);
    }

    // Navega na estrutura hierárquica do payload Meta
    // body.entry[].changes[].value.messages[]
    const entries = (body["entry"] as Array<Record<string, unknown>> | undefined) ?? [];

    for (const entry of entries) {
      const changes = (entry["changes"] as Array<Record<string, unknown>> | undefined) ?? [];
      for (const change of changes) {
        const value = change["value"] as Record<string, unknown> | undefined;
        if (!value) continue;

        const messages = (value["messages"] as Array<Record<string, unknown>> | undefined) ?? [];
        const metadata = value["metadata"] as Record<string, unknown> | undefined;
        const phoneNumberId = (metadata?.["phone_number_id"] as string | undefined) ?? "unknown";

        for (const msg of messages) {
          const msgType = msg["type"] as string;
          if (msgType !== "text") {
            log.debug({ msgType }, "Non-text WhatsApp message, skipping");
            continue;
          }

          const msgId = msg["id"] as string;
          if (dedup.seen(msgId)) {
            log.debug({ msgId }, "Duplicate WhatsApp message, skipping");
            continue;
          }

          const from = (msg["from"] as string | undefined) ?? "unknown";
          const textPayload = msg["text"] as Record<string, unknown> | undefined;
          const text = (textPayload?.["body"] as string | undefined)?.trim() ?? "";

          if (!text) continue;

          const envelope = newEnvelope({
            source_channel: "whatsapp",
            source_id: from,
            source_client_id: `whatsapp:${from}`,
            direction: "inbound",
            message_type: "text",
            text,
            metadata: {
              whatsapp_from: from,
              whatsapp_message_id: msgId,
              whatsapp_phone_number_id: phoneNumberId,
              whatsapp_timestamp: msg["timestamp"] ?? null,
            },
          });

          const forwarded = registry.forwardToBrain(envelope);
          if (!forwarded) {
            log.error({ from }, "Brain not available for WhatsApp message");
          } else {
            log.info({ from, text: text.slice(0, 80) }, "WhatsApp message forwarded to brain");
          }
        }
      }
    }

    // Meta espera 200 OK imediato, mesmo que processamento esteja pendente
    return c.json({ status: "ok" });
  });

  return router;
}
