import { logger } from "./logger.js";
import type { GatewayAppLike } from "./app.js";
import { WsBridge } from "../../gateway/src/ws-bridge.js";
import { ChannelRegistry } from "../../gateway/src/registry.js";
import { HealthAggregator } from "../../gateway/src/health.js";
import { GatewayStateMachine } from "../../gateway/src/state-machine.js";
import { TelegramAdapter } from "../../gateway/src/adapters/telegram.js";
import { TelegramLongPoller } from "../../gateway/src/channels/telegram.js";
import { DiscordAdapter } from "../../gateway/src/adapters/discord.js";
import { SlackAdapter } from "../../gateway/src/adapters/slack.js";
import { WhatsAppAdapter } from "../../gateway/src/adapters/whatsapp.js";
import { createGatewayApp } from "../../gateway/src/server.js";
import { EnvelopeSchema } from "../../gateway/src/envelope.js";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Required environment variable missing: ${name}`);
  }
  return value;
}

function defaultBrainWsUrl(): string {
  const base = process.env["PYTHON_BRAIN_BASE_URL"] ?? "http://127.0.0.1:8000";
  const url = new URL(base.endsWith("/") ? base : `${base}/`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = url.pathname === "/" ? "/ws/gateway" : `${url.pathname.replace(/\/$/, "")}/ws/gateway`;
  url.search = "";
  url.hash = "";
  return url.toString();
}

function resolveBrainWsToken(): string | undefined {
  for (const envName of ["BRAIN_WS_TOKEN", "RLM_GATEWAY_TOKEN", "RLM_WS_TOKEN", "RLM_INTERNAL_TOKEN"]) {
    const value = process.env[envName]?.trim();
    if (value) {
      return value;
    }
  }
  return undefined;
}

export interface GatewayRuntime {
  gatewayApp: GatewayAppLike;
  registry: ChannelRegistry;
  health: HealthAggregator;
  bridge: WsBridge;
  stateMachine: GatewayStateMachine;
  start(): void;
  stop(): Promise<void>;
}

export function createGatewayRuntimeFromEnv(): GatewayRuntime {
  const brainWsUrl = process.env["BRAIN_WS_URL"] ?? defaultBrainWsUrl();
  const brainWsToken = resolveBrainWsToken();
  const gatewayId = process.env["GATEWAY_ID"] ?? `server-${process.pid}`;
  const telegramBotToken = process.env["TELEGRAM_BOT_TOKEN"];
  const telegramSecretToken = process.env["TELEGRAM_SECRET_TOKEN"];
  const telegramPollingMode = (process.env["TELEGRAM_POLLING_MODE"] ?? "polling").toLowerCase();

  const stateMachine = new GatewayStateMachine();
  const bridge = new WsBridge(brainWsUrl, brainWsToken, gatewayId);
  const registry = new ChannelRegistry(bridge);
  const health = new HealthAggregator(gatewayId, registry, bridge);

  let telegramPoller: TelegramLongPoller | undefined;
  if (telegramBotToken) {
    const tgAdapter = new TelegramAdapter({ botToken: telegramBotToken });
    registry.register(tgAdapter);

    if (telegramPollingMode !== "webhook") {
      telegramPoller = new TelegramLongPoller(
        telegramBotToken,
        tgAdapter,
        async (_chatId, envelopeJson) => {
          const envelope = EnvelopeSchema.parse(JSON.parse(envelopeJson) as unknown);
          registry.forwardToBrain(envelope);
        },
      );
    }
  }

  const discordAdapter = DiscordAdapter.fromEnv();
  if (discordAdapter) {
    registry.register(discordAdapter);
  }

  const slackAdapter = SlackAdapter.fromEnv();
  if (slackAdapter) {
    registry.register(slackAdapter);
  }

  const whatsappAdapter = WhatsAppAdapter.fromEnv();
  if (whatsappAdapter) {
    registry.register(whatsappAdapter);
  }

  registry.attachBridge();

  const gatewayApp = createGatewayApp(registry, health, stateMachine, bridge, {
    ...(telegramSecretToken !== undefined ? { telegramSecretToken } : {}),
    ...(process.env["WEBHOOK_SECRET"] !== undefined ? { webhookSecret: process.env["WEBHOOK_SECRET"] } : {}),
    ...(process.env["OPENAI_COMPAT_API_KEY"] !== undefined ? { openaiApiKey: process.env["OPENAI_COMPAT_API_KEY"] } : {}),
  });

  let started = false;
  let unsubscribe: (() => void) | undefined;

  return {
    gatewayApp,
    registry,
    health,
    bridge,
    stateMachine,
    start() {
      if (started) {
        return;
      }
      started = true;
      stateMachine.dispatch("bootstrap");
      bridge.start();
      telegramPoller?.start();
      health.startReporting(30_000);

      unsubscribe = bridge.onReply(() => {
        if (stateMachine.is("starting")) {
          stateMachine.dispatch("bridgeConnected");
          unsubscribe?.();
          unsubscribe = undefined;
        }
      });

      setTimeout(() => {
        if (stateMachine.is("starting") && bridge.getHealth().status === "connected") {
          stateMachine.dispatch("bridgeConnected");
          unsubscribe?.();
          unsubscribe = undefined;
        }
      }, 2_000).unref();
    },
    async stop() {
      unsubscribe?.();
      unsubscribe = undefined;
      health.stopReporting();
      registry.detachBridge();
      stateMachine.dispatch("gracefulStop");
      await telegramPoller?.stop();
      await bridge.stop();
      stateMachine.dispatch("drained");
      logger.info("Gateway runtime stopped");
      started = false;
    },
  };
}