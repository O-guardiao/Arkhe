/**
 * Discord Channel Receiver (inbound)
 *
 * Analogia Python: rlm/server/discord_gateway.py
 *
 * Monta o roteador Hono para receber Interactions HTTP do Discord.
 * Verificação criptográfica via Ed25519 (Node 20+ tem suporte nativo em crypto.subtle).
 *
 * Variáveis de ambiente:
 *   RLM_DISCORD_PUBLIC_KEY   — hex da chave pública de app do Discord
 *   RLM_DISCORD_SKIP_VERIFY  — "true" para bypass em dev
 *
 * Funcionalidades:
 *   - PING → PONG (Discord envia isso para validar o endpoint)
 *   - APPLICATION_COMMAND /rlm <prompt> → Envelope → Brain
 *   - Resposta imediata (deferral) + follow-up via Discord REST API
 */

import { Hono } from "hono";
import { childLogger } from "../logger.js";
import { newEnvelope } from "../envelope.js";
import type { ChannelRegistry } from "../registry.js";

const log = childLogger({ component: "channel:discord" });

// ---------------------------------------------------------------------------
// Ed25519 signature verification (Node 20+ Web Crypto)
// ---------------------------------------------------------------------------

async function verifyDiscordSignature(
  publicKeyHex: string,
  timestamp: string,
  rawBody: string,
  signatureHex: string,
): Promise<boolean> {
  try {
    const pubKeyBytes = hexToBytes(publicKeyHex);
    const sigBytes = hexToBytes(signatureHex);
    const message = new TextEncoder().encode(timestamp + rawBody);

    const key = await crypto.subtle.importKey(
      "raw",
      pubKeyBytes,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    return await crypto.subtle.verify("Ed25519", key, sigBytes, message);
  } catch (err) {
    log.warn({ err }, "Ed25519 verification error");
    return false;
  }
}

function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
  }
  return bytes;
}

// ---------------------------------------------------------------------------
// Discord Interaction types (subset)
// ---------------------------------------------------------------------------

const INTERACTION_PING = 1;
const INTERACTION_APPLICATION_COMMAND = 2;

const RESPONSE_PONG = 1;
const RESPONSE_DEFERRED_CHANNEL_MESSAGE = 5;

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createDiscordChannelHandler(registry: ChannelRegistry): Hono {
  const router = new Hono();

  const publicKeyHex = process.env["RLM_DISCORD_PUBLIC_KEY"] ?? "";
  const skipVerify = process.env["RLM_DISCORD_SKIP_VERIFY"] === "true";

  if (!publicKeyHex && !skipVerify) {
    log.warn("RLM_DISCORD_PUBLIC_KEY not set — Discord handler will reject all requests");
  }

  router.post("/discord/interactions", async (c) => {
    const rawBody = await c.req.text();
    const signature = c.req.header("x-signature-ed25519") ?? "";
    const timestamp = c.req.header("x-signature-timestamp") ?? "";

    // Verifica assinatura Ed25519
    if (!skipVerify) {
      if (!publicKeyHex || !signature || !timestamp) {
        log.warn("Missing Discord signature headers");
        return c.json({ error: "unauthorized" }, 401);
      }
      const valid = await verifyDiscordSignature(publicKeyHex, timestamp, rawBody, signature);
      if (!valid) {
        log.warn({ signature }, "Invalid Discord Ed25519 signature");
        return c.json({ error: "invalid signature" }, 401);
      }
    }

    // Parse body
    let interaction: Record<string, unknown>;
    try {
      interaction = JSON.parse(rawBody) as Record<string, unknown>;
    } catch {
      return c.json({ error: "bad json" }, 400);
    }

    const interactionType = interaction["type"] as number;

    // PING – Discord valida o endpoint enviando um PING
    if (interactionType === INTERACTION_PING) {
      return c.json({ type: RESPONSE_PONG });
    }

    // APPLICATION_COMMAND – slash command /rlm
    if (interactionType === INTERACTION_APPLICATION_COMMAND) {
      const data = interaction["data"] as Record<string, unknown> | undefined;
      const commandName = (data?.["name"] as string | undefined)?.toLowerCase() ?? "";
      const member = interaction["member"] as Record<string, unknown> | undefined;
      const user = member?.["user"] as Record<string, unknown> | undefined;
      const userId = (user?.["id"] as string | undefined) ?? "unknown";
      const interactionId = (interaction["id"] as string | undefined) ?? crypto.randomUUID().replace(/-/g, "");

      if (commandName !== "rlm") {
        log.warn({ commandName }, "Unknown slash command");
        return c.json({ type: 4, data: { content: "Comando desconhecido.", flags: 64 } });
      }

      // Extrai o prompt do option "prompt"
      const options = (data?.["options"] as Array<Record<string, unknown>> | undefined) ?? [];
      const promptOption = options.find((o) => (o["name"] as string) === "prompt");
      const promptText = (promptOption?.["value"] as string | undefined)?.trim() ?? "";

      if (!promptText) {
        return c.json({ type: 4, data: { content: "Forneça um prompt. Ex: /rlm prompt:O que é AGI?", flags: 64 } });
      }

      const envelope = newEnvelope({
        source_channel: "discord",
        source_id: userId,
        source_client_id: `discord:${userId}`,
        direction: "inbound",
        message_type: "text",
        text: promptText,
        metadata: {
          discord_interaction_id: interactionId,
          discord_user_id: userId,
          discord_guild_id: interaction["guild_id"] ?? null,
          discord_channel_id: interaction["channel_id"] ?? null,
        },
      });

      const forwarded = registry.forwardToBrain(envelope);
      if (!forwarded) {
        log.error({ interactionId }, "Brain not available for Discord interaction");
        return c.json({ type: 4, data: { content: "Brain indisponível no momento.", flags: 64 } });
      }

      log.info({ interactionId, userId, prompt: promptText.slice(0, 80) }, "Discord interaction forwarded to brain");

      // Resposta diferida — o Brain responderá via adapter com follow-up
      return c.json({ type: RESPONSE_DEFERRED_CHANNEL_MESSAGE });
    }

    log.warn({ interactionType }, "Unhandled Discord interaction type");
    return c.json({ error: "unhandled" }, 400);
  });

  return router;
}
