import { Hono } from "hono";
import { z } from "zod";

import { newEnvelope, type Envelope } from "../../gateway/src/envelope.js";
import type { ChannelAdapter, ChannelIdentity, ChannelInfo } from "../../gateway/src/adapters/interface.js";
import { requireInternalToken } from "./auth.js";

export interface ChannelRegistryLike {
  all(): ChannelAdapter[];
  get(channel: string): ChannelAdapter | undefined;
}

const SendBodySchema = z.object({
  target_client_id: z.string().min(1),
  message: z.string().min(1),
});

function snapshotFromChannelInfo(info: ChannelInfo): Record<string, unknown> {
  return {
    channel_id: info.id,
    account_id: "default",
    configured: info.status !== "disabled",
    running: info.status !== "disabled",
    healthy: info.status === "healthy",
    identity: {
      bot_id: info.id,
      username: info.id,
      display_name: info.name,
    },
    last_error: info.status === "healthy" ? null : `channel status=${info.status}`,
    reconnect_attempts: info.errors,
    last_probe_ms: 0,
    meta: {
      type: info.type,
      messages_sent: info.messagesSent,
      messages_received: info.messagesReceived,
      errors: info.errors,
      last_seen_ms: info.lastSeenMs ?? null,
    },
  };
}

function serializeIdentity(identity: ChannelIdentity | undefined): Record<string, unknown> | null {
  if (!identity) {
    return null;
  }

  return {
    bot_id: identity.botId ?? null,
    username: identity.username ?? null,
    display_name: identity.displayName ?? null,
  };
}

function splitTargetClientId(targetClientId: string): { channel: string; targetId: string } {
  const [channel, ...rest] = targetClientId.split(":");
  if (!channel || rest.length === 0) {
    throw new Error("target_client_id deve seguir o formato canal:id");
  }

  return {
    channel,
    targetId: rest.join(":"),
  };
}

function buildOutboundEnvelope(targetClientId: string, message: string): Envelope {
  const { channel, targetId } = splitTargetClientId(targetClientId);
  return newEnvelope({
    source_channel: "internal",
    source_id: "operator",
    source_client_id: "internal:operator",
    target_channel: channel,
    target_id: targetId,
    target_client_id: targetClientId,
    direction: "outbound",
    message_type: "text",
    text: message,
    metadata: { routing_key: targetClientId },
  });
}

export function createChannelAdminRouter(registry: ChannelRegistryLike): Hono {
  const router = new Hono();

  router.use("*", async (c, next) => {
    const unauthorized = requireInternalToken(c);
    if (unauthorized) {
      return unauthorized;
    }
    await next();
  });

  router.get("/api/channels/status", (c) => {
    const channels = Object.fromEntries(
      registry.all().map((adapter) => {
        const info = adapter.getChannelInfo();
        return [info.id, snapshotFromChannelInfo(info)];
      }),
    );

    return c.json({ channels });
  });

  router.post("/api/channels/:channel_id/probe", async (c) => {
    const channelId = c.req.param("channel_id");
    const adapter = registry.get(channelId);
    if (!adapter) {
      return c.json({ error: `Channel '${channelId}' not registered` }, 404);
    }
    if (!adapter.probe) {
      return c.json({ error: `Channel '${channelId}' does not support active probe` }, 501);
    }

    const result = await adapter.probe();
    return c.json({
      channel_id: channelId,
      probe: {
        ok: result.ok,
        elapsed_ms: Number(result.elapsedMs.toFixed(1)),
        error: result.error ?? null,
        identity: serializeIdentity(result.identity),
      },
      snapshot: snapshotFromChannelInfo(adapter.getChannelInfo()),
    });
  });

  router.post("/api/channels/send", async (c) => {
    const parsed = SendBodySchema.safeParse(await c.req.json());
    if (!parsed.success) {
      return c.json({ error: "target_client_id e message são obrigatórios" }, 400);
    }

    let target;
    try {
      target = splitTargetClientId(parsed.data.target_client_id);
    } catch (error) {
      return c.json({ error: String(error) }, 400);
    }

    const adapter = registry.get(target.channel);
    if (!adapter) {
      return c.json({ error: `Channel '${target.channel}' not registered` }, 404);
    }

    const envelope = buildOutboundEnvelope(parsed.data.target_client_id, parsed.data.message);
    const result = await adapter.sendMessage(target.targetId, parsed.data.message, envelope);
    if (!result.ok) {
      return c.json({ status: "failed", via: "registry", error: result.error ?? "unknown" }, 502);
    }

    return c.json({
      status: "sent",
      via: "registry",
      message_id: result.messageId ?? null,
      target_client_id: parsed.data.target_client_id,
    });
  });

  return router;
}