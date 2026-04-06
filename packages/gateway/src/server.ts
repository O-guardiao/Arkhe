/**
 * Servidor Hono do Gateway.
 *
 * Registra:
 *  - POST /webhooks/telegram      — recebe updates do Telegram
 *  - POST /discord/interactions   — Discord Interactions API
 *  - POST /slack/events           — Slack Events API
 *  - GET|POST /whatsapp/webhook   — Meta Cloud API
 *  - GET /webchat + SSE           — webchat embed
 *  - /webhooks/*                  — webhooks genéricos
 *  - /v1/*                        — OpenAI compat
 *  - /operator/*                  — operações de ciclo de vida
 *  - GET  /health                 — retorna snapshot de saúde
 *  - GET  /ready                  — liveness/readiness probe
 */

import { Hono } from "hono";
import { childLogger } from "./logger.js";
import { verifyTelegramWebhook } from "./auth.js";
import { createTelegramWebhookHandler } from "./channels/telegram.js";
import { createDiscordChannelHandler } from "./channels/discord.js";
import { createSlackChannelHandler } from "./channels/slack.js";
import { createWhatsAppChannelHandler } from "./channels/whatsapp.js";
import { createWebChatHandler } from "./channels/webchat.js";
import { createWebhookRouter } from "./webhooks.js";
import { createOpenAICompatRouter } from "./openai-compat.js";
import { createOperatorRouter } from "./operator.js";
import type { ChannelRegistry } from "./registry.js";
import type { HealthAggregator } from "./health.js";
import type { TelegramAdapter } from "./adapters/telegram.js";
import type { GatewayStateMachine } from "./state-machine.js";
import type { WsBridge } from "./ws-bridge.js";
import type { Envelope } from "./envelope.js";
import { newEnvelope } from "./envelope.js";

const log = childLogger({ component: "server" });

export interface GatewayServerConfig {
  telegramSecretToken?: string;
  /** hookToken para webhooks genéricos (opcional) — mapeia para WebhookDispatchConfig.hookToken */
  webhookSecret?: string;
  /** apiToken para compatibilidade OpenAI (opcional) — mapeia para OpenAICompatConfig.apiToken */
  openaiApiKey?: string;
}

export function createGatewayApp(
  registry: ChannelRegistry,
  health: HealthAggregator,
  stateMachine: GatewayStateMachine,
  bridge: WsBridge,
  config: GatewayServerConfig = {},
): { app: Hono; operatorStore: import("./operator.js").OperatorSessionStore } {
  const app = new Hono();

  // -------------------------------------------------------------------------
  // Middleware global: rejeita requisições quando não estamos em "running"
  // -------------------------------------------------------------------------
  app.use("*", async (c, next) => {
    if (stateMachine.is("failed") || stateMachine.is("stopped")) {
      return c.json({ ok: false, error: "gateway unavailable" }, 503);
    }
    await next();
  });

  // -------------------------------------------------------------------------
  // Health & Readiness
  // -------------------------------------------------------------------------
  app.get("/health", (c) => {
    const report = health.snapshot();
    const statusCode = report.status === "healthy" ? 200 : report.status === "degraded" ? 207 : 503;
    return c.json(report, statusCode);
  });

  app.get("/ready", (c) => {
    if (stateMachine.is("running")) {
      return c.json({ ok: true, state: "running" });
    }
    return c.json({ ok: false, state: stateMachine.state }, 503);
  });

  // -------------------------------------------------------------------------
  // Webhook Telegram
  // -------------------------------------------------------------------------
  const telegramAdapter = registry.get("telegram") as TelegramAdapter | undefined;

  if (telegramAdapter) {
    const sendToBrain = async (_chatId: string, envelopeJson: string) => {
      try {
        const parsed = JSON.parse(envelopeJson) as { envelope?: Envelope; type?: string };
        const envelope = parsed.envelope ?? (parsed as unknown as Envelope);
        registry.forwardToBrain(envelope);
      } catch (err) {
        log.error({ err }, "Failed to parse envelope json for brain forwarding");
      }
    };

    const webhookHandler = createTelegramWebhookHandler(telegramAdapter, sendToBrain);

    app.post("/webhooks/telegram", async (c) => {
      if (config.telegramSecretToken) {
        const secretHeader = c.req.header("X-Telegram-Bot-Api-Secret-Token") ?? "";
        const valid = verifyTelegramWebhook(config.telegramSecretToken, secretHeader);
        if (!valid) {
          log.warn({ ip: c.req.header("cf-connecting-ip") }, "Telegram webhook auth failure");
          return c.json({ ok: false }, 401);
        }
      }
      return webhookHandler(c);
    });

    log.info("Telegram webhook route registered at POST /webhooks/telegram");
  } else {
    log.info("Telegram adapter not registered; skipping webhook route");
  }

  // -------------------------------------------------------------------------
  // Discord Interactions
  // -------------------------------------------------------------------------
  app.route("/", createDiscordChannelHandler(registry));
  log.info("Discord interactions route registered at POST /discord/interactions");

  // -------------------------------------------------------------------------
  // Slack Events
  // -------------------------------------------------------------------------
  app.route("/", createSlackChannelHandler(registry));
  log.info("Slack events route registered at POST /slack/events");

  // -------------------------------------------------------------------------
  // WhatsApp / Meta Cloud API
  // -------------------------------------------------------------------------
  app.route("/", createWhatsAppChannelHandler(registry));
  log.info("WhatsApp webhook routes registered at /whatsapp/webhook");

  // -------------------------------------------------------------------------
  // WebChat embed
  // -------------------------------------------------------------------------
  app.route("/", createWebChatHandler(registry, bridge));
  log.info("WebChat routes registered at /webchat");

  // -------------------------------------------------------------------------
  // Webhooks genéricos
  // -------------------------------------------------------------------------
  app.route("/", createWebhookRouter(registry, config.webhookSecret ? { hookToken: config.webhookSecret } : {}));
  log.info("Generic webhook router registered at /webhooks/*");

  // -------------------------------------------------------------------------
  // OpenAI compatibility
  // -------------------------------------------------------------------------
  app.route("/", createOpenAICompatRouter(registry, bridge, config.openaiApiKey ? { apiToken: config.openaiApiKey } : {}));
  log.info("OpenAI compat router registered at /v1/*");

  // -------------------------------------------------------------------------
  // Operator
  // -------------------------------------------------------------------------
  const { router: operatorRouter, store: operatorStore } = createOperatorRouter(registry, bridge);
  app.route("/", operatorRouter);
  log.info("Operator router registered at /operator/*");

  // -------------------------------------------------------------------------
  // Channel status API (/api/channels/*)
  // -------------------------------------------------------------------------
  app.get("/api/channels/status", (c) => {
    const adapters = registry.all();
    const channels: Record<string, unknown> = {};
    for (const adapter of adapters) {
      const info = adapter.getChannelInfo();
      channels[info.id] = {
        channel_id: info.id,
        account_id: "default",
        configured: true,
        running: info.status === "healthy" || info.status === "degraded",
        healthy: info.status === "healthy",
        identity: { display_name: info.name },
        last_error: info.status === "down" ? "channel down" : null,
        reconnect_attempts: info.errors,
        last_probe_ms: info.lastSeenMs ?? 0,
        meta: {},
      };
    }
    return c.json({ channels });
  });

  app.post("/api/channels/:id/probe", async (c) => {
    const channelId = c.req.param("id");
    const adapter = registry.get(channelId);
    if (!adapter) return c.json({ error: `Channel ${channelId} not found` }, 404);
    if (!adapter.probe) return c.json({ status: "ok", message: "Probe not supported" });
    const result = await adapter.probe(5000);
    return c.json({ status: result.ok ? "ok" : "error", elapsed_ms: result.elapsedMs, error: result.error ?? null });
  });

  app.post("/api/channels/send", async (c) => {
    let body: { target_client_id: string; message: string };
    try {
      body = (await c.req.json()) as { target_client_id: string; message: string };
    } catch {
      return c.json({ error: "Invalid body" }, 400);
    }
    const envelope = newEnvelope({
      source_channel: "internal",
      source_id: "tui-operator",
      source_client_id: "internal:tui-operator",
      direction: "outbound",
      message_type: "text",
      text: body.message,
      target_client_id: body.target_client_id,
      metadata: {},
    });
    const forwarded = registry.forwardToBrain(envelope);
    if (!forwarded) return c.json({ error: "Brain unavailable" }, 503);
    return c.json({ status: "ok", envelope_id: envelope.id });
  });

  // -------------------------------------------------------------------------
  // Wire Brain replies to OperatorSessionStore
  // -------------------------------------------------------------------------
  bridge.onReply((envelope) => {
    if (envelope.direction === "outbound" || envelope.source_channel === "internal") {
      const sessions = operatorStore.all();
      for (const session of sessions) {
        const eventType = envelope.direction === "outbound" ? "brain.reply" : "inbound_event";
        operatorStore.logEvent(session.sessionId, eventType, {
          channel: envelope.target_channel ?? envelope.source_channel,
          text: envelope.text ?? "",
          envelope_id: envelope.id,
          source_client_id: envelope.source_client_id,
          target_client_id: envelope.target_client_id ?? null,
          message_type: envelope.message_type,
        });
      }
    }
  });

  // -------------------------------------------------------------------------
  // 404 default
  // -------------------------------------------------------------------------
  app.notFound((c) => c.json({ ok: false, error: "not found" }, 404));

  return { app, operatorStore };
}
