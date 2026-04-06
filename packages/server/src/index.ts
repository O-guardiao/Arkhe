import { serve } from "@hono/node-server";

import { createServerApp } from "./app.js";
import { createGatewayRuntimeFromEnv } from "./bootstrap-gateway.js";
import { logger } from "./logger.js";

const port = Number.parseInt(process.env["PORT"] ?? "3000", 10);
const pythonBaseUrl = process.env["PYTHON_BRAIN_BASE_URL"] ?? "http://127.0.0.1:8000";

const runtime = createGatewayRuntimeFromEnv();
const app = createServerApp(runtime, { pythonBaseUrl });

const server = serve(
  {
    fetch: app.fetch,
    port,
  },
  (info) => {
    logger.info({ port: info.port, pythonBaseUrl }, "Arkhe server started");
    runtime.start();
  },
);

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, "Shutdown signal received");
  await runtime.stop();
  server.close(() => {
    logger.info("Arkhe server shutdown complete");
    process.exit(0);
  });

  setTimeout(() => {
    logger.warn("Shutdown timeout — forcing exit");
    process.exit(1);
  }, 30_000).unref();
}

process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));

process.on("uncaughtException", (error) => {
  logger.fatal({ error }, "Uncaught exception");
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  logger.fatal({ reason }, "Unhandled rejection");
  process.exit(1);
});