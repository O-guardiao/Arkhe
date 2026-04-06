import { Hono } from "hono";

import { createChannelAdminRouter, type ChannelRegistryLike } from "./channel-admin.js";
import {
  buildCompatibilityWebhookEnvelope,
  type BridgeLike,
  waitForBrainReply,
} from "./compat.js";
import { requireInternalToken } from "./auth.js";
import { proxyToUpstream } from "./http-proxy.js";

export interface GatewayAppLike {
  fetch(request: Request): Response | Promise<Response>;
}

export interface GatewayRuntimeLike {
  gatewayApp: GatewayAppLike;
  registry: ChannelRegistryLike & {
    forwardToBrain(envelope: import("../../gateway/src/envelope.js").Envelope): boolean;
  };
  bridge: BridgeLike;
}

export interface ServerAppOptions {
  pythonBaseUrl: string;
  webhookReplyTimeoutMs?: number;
}

function registerProxyPrefix(app: Hono, prefix: string, pythonBaseUrl: string): void {
  app.all(prefix, (c) => proxyToUpstream(c, pythonBaseUrl));
  app.all(`${prefix}/*`, (c) => proxyToUpstream(c, pythonBaseUrl));
}

function registerWebhookCompatibilityRoute(
  app: Hono,
  runtime: GatewayRuntimeLike,
  timeoutMs: number,
): void {
  app.post("/webhook/:client_id", async (c) => {
    const unauthorized = requireInternalToken(c);
    if (unauthorized) {
      return unauthorized;
    }

    let payload: Record<string, unknown>;
    try {
      payload = await c.req.json();
    } catch {
      return c.json({ error: "Invalid JSON payload" }, 400);
    }

    const clientId = c.req.param("client_id");
    const envelope = buildCompatibilityWebhookEnvelope(clientId, payload);
    const forwarded = runtime.registry.forwardToBrain(envelope);
    if (!forwarded) {
      return c.json({ error: "Brain unavailable" }, 503);
    }

    try {
      const reply = await waitForBrainReply(runtime.bridge, envelope.id, timeoutMs);
      return c.json({
        response: reply.text,
        already_replied: false,
        envelope_id: envelope.id,
        reply_envelope_id: reply.id,
      });
    } catch {
      return c.json({
        error: "Brain response timeout",
        envelope_id: envelope.id,
      }, 504);
    }
  });
}

export function createServerApp(runtime: GatewayRuntimeLike, options: ServerAppOptions): Hono {
  const app = new Hono();
  const timeoutMs = options.webhookReplyTimeoutMs ?? 60_000;

  registerWebhookCompatibilityRoute(app, runtime, timeoutMs);
  app.route("/", createChannelAdminRouter(runtime.registry));

  for (const prefix of [
    "/brain",
    "/sessions",
    "/plugins",
    "/routes",
    "/skills",
    "/cron/jobs",
    "/hooks/stats",
    "/exec",
  ]) {
    registerProxyPrefix(app, prefix, options.pythonBaseUrl);
  }

  app.all("*", (c) => runtime.gatewayApp.fetch(c.req.raw));
  return app;
}