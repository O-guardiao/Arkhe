import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { Hono } from "hono";

import { createServerApp, type GatewayRuntimeLike } from "../src/app.js";
import { replyEnvelope, type Envelope } from "../../gateway/src/envelope.js";
import type { ChannelAdapter, ChannelInfo, ProbeResult, SendResult } from "../../gateway/src/adapters/interface.js";

class FakeAdapter implements ChannelAdapter {
  readonly channelName = "telegram";
  sent: Array<{ targetId: string; text: string; envelope: Envelope }> = [];

  async sendMessage(targetId: string, text: string, envelope: Envelope): Promise<SendResult> {
    this.sent.push({ targetId, text, envelope });
    return { ok: true, messageId: "msg-1" };
  }

  getChannelInfo(): ChannelInfo {
    return {
      id: "telegram",
      name: "Telegram",
      type: "messaging",
      status: "healthy",
      messagesSent: this.sent.length,
      messagesReceived: 3,
      errors: 0,
      lastSeenMs: 123,
    };
  }

  async probe(): Promise<ProbeResult> {
    return {
      ok: true,
      elapsedMs: 12.4,
      identity: {
        botId: 42,
        username: "telegram_bot",
        displayName: "Telegram Bot",
      },
    };
  }
}

function buildRuntime(adapter: FakeAdapter, gatewayApp = new Hono()): GatewayRuntimeLike {
  const handlers = new Set<(envelope: Envelope) => void>();
  let lastEnvelope: Envelope | null = null;

  return {
    gatewayApp,
    bridge: {
      onReply(handler) {
        handlers.add(handler);
        return () => handlers.delete(handler);
      },
    },
    registry: {
      all() {
        return [adapter];
      },
      get(channel: string) {
        return channel === adapter.channelName ? adapter : undefined;
      },
      forwardToBrain(envelope: Envelope) {
        lastEnvelope = envelope;
        queueMicrotask(() => {
          if (!lastEnvelope) {
            return;
          }
          const reply = replyEnvelope(lastEnvelope, "resposta do brain");
          for (const handler of handlers) {
            handler(reply);
          }
        });
        return true;
      },
    },
  };
}

describe("packages/server frontdoor", () => {
  beforeEach(() => {
    process.env["RLM_INTERNAL_TOKEN"] = "segredo";
  });

  afterEach(() => {
    delete process.env["RLM_INTERNAL_TOKEN"];
  });

  it("returns channel snapshots in the shape expected by the TUI", async () => {
    const adapter = new FakeAdapter();
    const runtime = buildRuntime(adapter);
    const app = createServerApp(runtime, { pythonBaseUrl: "http://127.0.0.1:8000" });

    const response = await app.request("http://server.test/api/channels/status", {
      headers: { "x-rlm-token": "segredo" },
    });
    const payload = await response.json() as { channels: Record<string, Record<string, unknown>> };

    expect(response.status).toBe(200);
    expect(payload.channels["telegram"]?.["channel_id"]).toBe("telegram");
    expect(payload.channels["telegram"]?.["healthy"]).toBe(true);
  });

  it("sends cross-channel messages through the registered adapter", async () => {
    const adapter = new FakeAdapter();
    const runtime = buildRuntime(adapter);
    const app = createServerApp(runtime, { pythonBaseUrl: "http://127.0.0.1:8000" });

    const response = await app.request("http://server.test/api/channels/send", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-rlm-token": "segredo",
      },
      body: JSON.stringify({
        target_client_id: "telegram:12345",
        message: "olá do server",
      }),
    });
    const payload = await response.json() as { status: string; via: string };

    expect(response.status).toBe(200);
    expect(payload.status).toBe("sent");
    expect(payload.via).toBe("registry");
    expect(adapter.sent).toHaveLength(1);
    expect(adapter.sent[0]?.targetId).toBe("12345");
  });

  it("executes a native channel probe without proxying to Python", async () => {
    const adapter = new FakeAdapter();
    const runtime = buildRuntime(adapter);
    const app = createServerApp(runtime, { pythonBaseUrl: "http://127.0.0.1:8000" });

    const response = await app.request("http://server.test/api/channels/telegram/probe", {
      method: "POST",
      headers: { "x-rlm-token": "segredo" },
    });
    const payload = await response.json() as {
      channel_id: string;
      probe: { ok: boolean; elapsed_ms: number; identity: { bot_id: number; username: string } };
    };

    expect(response.status).toBe(200);
    expect(payload.channel_id).toBe("telegram");
    expect(payload.probe.ok).toBe(true);
    expect(payload.probe.elapsed_ms).toBe(12.4);
    expect(payload.probe.identity.bot_id).toBe(42);
    expect(payload.probe.identity.username).toBe("telegram_bot");
  });

  it("recreates the compatibility webhook and waits for the brain reply", async () => {
    const adapter = new FakeAdapter();
    const runtime = buildRuntime(adapter);
    const app = createServerApp(runtime, {
      pythonBaseUrl: "http://127.0.0.1:8000",
      webhookReplyTimeoutMs: 1_000,
    });

    const response = await app.request("http://server.test/webhook/telegram:42", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-rlm-token": "segredo",
      },
      body: JSON.stringify({
        text: "pergunta compatível",
        from_user: "tester",
      }),
    });
    const payload = await response.json() as { response: string; already_replied: boolean };

    expect(response.status).toBe(200);
    expect(payload.response).toBe("resposta do brain");
    expect(payload.already_replied).toBe(false);
  });

  it("delegates unmatched routes to the embedded gateway app", async () => {
    const adapter = new FakeAdapter();
    const gatewayApp = new Hono();
    gatewayApp.get("/health", (c) => c.json({ status: "healthy", gateway: true }));

    const runtime = buildRuntime(adapter, gatewayApp);
    const app = createServerApp(runtime, { pythonBaseUrl: "http://127.0.0.1:8000" });

    const response = await app.request("http://server.test/health", {
      headers: { "x-rlm-token": "segredo" },
    });
    const payload = await response.json() as { status: string; gateway: boolean };

    expect(response.status).toBe(200);
    expect(payload.status).toBe("healthy");
    expect(payload.gateway).toBe(true);
  });
});