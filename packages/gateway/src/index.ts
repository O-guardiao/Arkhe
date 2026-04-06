/**
 * Ponto de entrada do Gateway Arkhe.
 *
 * Ordem de inicialização:
 *   1. Valida variáveis de ambiente
 *   2. Cria componentes (StateMachine, Bridge, Registry, Adapters, Health)
 *   3. Registra adaptadores de canal
 *   4. Cria app Hono e inicia servidor HTTP
 *   5. Conecta ao Brain (WsBridge.start())
 *   6. Registra handlers de shutdown gracioso (SIGTERM/SIGINT)
 */

import { serve } from "@hono/node-server";
import { logger } from "./logger.js";
import { WsBridge } from "./ws-bridge.js";
import { ChannelRegistry } from "./registry.js";
import { HealthAggregator } from "./health.js";
import { GatewayStateMachine } from "./state-machine.js";
import { TelegramAdapter } from "./adapters/telegram.js";
import { DiscordAdapter } from "./adapters/discord.js";
import { SlackAdapter } from "./adapters/slack.js";
import { WhatsAppAdapter } from "./adapters/whatsapp.js";
import { createGatewayApp } from "./server.js";

// ---------------------------------------------------------------------------
// Env vars
// ---------------------------------------------------------------------------

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    logger.fatal({ envVar: name }, `Required environment variable missing: ${name}`);
    process.exit(1);
  }
  return value;
}

const BRAIN_WS_URL = requireEnv("BRAIN_WS_URL");
// Token de autenticação enviado ao Brain como ?token=<valor>.
// Deve bater com RLM_GATEWAY_TOKEN (ou RLM_WS_TOKEN) configurado no servidor Python.
const BRAIN_WS_TOKEN = process.env["BRAIN_WS_TOKEN"];
const PORT = parseInt(process.env["PORT"] ?? "3000", 10);
const GATEWAY_ID = process.env["GATEWAY_ID"] ?? `gateway-${process.pid}`;
const TELEGRAM_BOT_TOKEN = process.env["TELEGRAM_BOT_TOKEN"];
const TELEGRAM_SECRET_TOKEN = process.env["TELEGRAM_SECRET_TOKEN"];

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

const stateMachine = new GatewayStateMachine();
const bridge = new WsBridge(BRAIN_WS_URL, BRAIN_WS_TOKEN);
const registry = new ChannelRegistry(bridge);
const health = new HealthAggregator(GATEWAY_ID, registry, bridge);

// Registra adaptadores conforme presença dos tokens
if (TELEGRAM_BOT_TOKEN) {
  registry.register(new TelegramAdapter({ botToken: TELEGRAM_BOT_TOKEN }));
  logger.info("Telegram adapter enabled");
} else {
  logger.warn("TELEGRAM_BOT_TOKEN not set; Telegram adapter disabled");
}

const discordAdapter = DiscordAdapter.fromEnv();
if (discordAdapter) {
  registry.register(discordAdapter);
  logger.info("Discord adapter enabled");
} else {
  logger.info("Discord adapter disabled (RLM_DISCORD_BOT_TOKEN not set)");
}

const slackAdapter = SlackAdapter.fromEnv();
if (slackAdapter) {
  registry.register(slackAdapter);
  logger.info("Slack adapter enabled");
} else {
  logger.info("Slack adapter disabled (RLM_SLACK_BOT_TOKEN not set)");
}

const whatsappAdapter = WhatsAppAdapter.fromEnv();
if (whatsappAdapter) {
  registry.register(whatsappAdapter);
  logger.info("WhatsApp adapter enabled");
} else {
  logger.info("WhatsApp adapter disabled (RLM_WHATSAPP_PHONE_NUMBER_ID not set)");
}

// Conecta registry ao bridge para receber respostas do Brain
registry.attachBridge();

const app = createGatewayApp(registry, health, stateMachine, bridge, {
  ...(TELEGRAM_SECRET_TOKEN !== undefined ? { telegramSecretToken: TELEGRAM_SECRET_TOKEN } : {}),
  ...(process.env["WEBHOOK_SECRET"] !== undefined ? { webhookSecret: process.env["WEBHOOK_SECRET"] } : {}),
  ...(process.env["OPENAI_COMPAT_API_KEY"] !== undefined ? { openaiApiKey: process.env["OPENAI_COMPAT_API_KEY"] } : {}),
});

// ---------------------------------------------------------------------------
// Start server
// ---------------------------------------------------------------------------

stateMachine.dispatch("bootstrap");

const server = serve(
  {
    fetch: app.fetch,
    port: PORT,
  },
  (info) => {
    logger.info({ port: info.port, gatewayId: GATEWAY_ID }, "Gateway HTTP server started");
    bridge.start();
    health.startReporting(30_000);
  },
);

// Aguarda conexão Bridge para transitar para "running"
const unsubscribe = bridge.onReply(() => {
  // Qualquer mensagem do Brain significa que estamos conectados
  if (stateMachine.is("starting")) {
    stateMachine.dispatch("bridgeConnected");
    unsubscribe();
  }
});

// Transition running após curto delay se bridge já conectou (via onOpen)
setTimeout(() => {
  if (stateMachine.is("starting")) {
    const h = bridge.getHealth();
    if (h.status === "connected") {
      stateMachine.dispatch("bridgeConnected");
    }
  }
}, 2_000);

// ---------------------------------------------------------------------------
// Graceful shutdown
// ---------------------------------------------------------------------------

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, "Shutdown signal received");
  stateMachine.dispatch("gracefulStop");
  health.stopReporting();
  registry.detachBridge();

  await bridge.stop();

  server.close(() => {
    stateMachine.dispatch("drained");
    logger.info("Gateway shutdown complete");
    process.exit(0);
  });

  // Force exit after 30s
  setTimeout(() => {
    logger.warn("Shutdown timeout — forcing exit");
    process.exit(1);
  }, 30_000).unref();
}

process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));

process.on("uncaughtException", (err) => {
  logger.fatal({ err }, "Uncaught exception");
  stateMachine.dispatch("fatalError");
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  logger.fatal({ reason }, "Unhandled rejection");
  stateMachine.dispatch("fatalError");
  process.exit(1);
});
