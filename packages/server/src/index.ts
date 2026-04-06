import { spawn, type ChildProcess } from "node:child_process";
import { serve } from "@hono/node-server";

import { createServerApp } from "./app.js";
import { createGatewayRuntimeFromEnv } from "./bootstrap-gateway.js";
import { logger } from "./logger.js";

const port = Number.parseInt(process.env["PORT"] ?? "3000", 10);
const pythonBaseUrl = process.env["PYTHON_BRAIN_BASE_URL"] ?? "http://127.0.0.1:8000";

function envFlagEnabled(name: string, defaultValue: boolean): boolean {
  const value = process.env[name]?.trim().toLowerCase();
  if (!value) {
    return defaultValue;
  }
  return !["0", "false", "no", "off"].includes(value);
}

function resolveMode(): "server" | "api" | "ws" {
  const requested = (process.argv[2] ?? "server").toLowerCase();
  if (requested === "api" || requested === "ws" || requested === "server") {
    return requested;
  }
  logger.warn({ requestedMode: requested }, "Unknown mode requested; using combined server mode");
  return "server";
}

function buildBrainHealthUrl(baseUrl: string): string {
  const url = new URL(baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  url.pathname = url.pathname === "/" ? "/health" : `${url.pathname.replace(/\/$/, "")}/health`;
  url.search = "";
  url.hash = "";
  return url.toString();
}

async function isBrainReachable(baseUrl: string): Promise<boolean> {
  try {
    const response = await fetch(buildBrainHealthUrl(baseUrl), {
      method: "GET",
      signal: AbortSignal.timeout(1_000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

function buildBrainSpawnOptions(baseUrl: string): {
  command: string;
  args: string[];
  env: NodeJS.ProcessEnv;
} {
  const url = new URL(baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  const portValue = url.port || (url.protocol === "https:" ? "443" : "80");
  const command =
    process.env["PYTHON_EXECUTABLE"]?.trim() ||
    process.env["PYTHON"]?.trim() ||
    process.env["RLM_PYTHON_EXECUTABLE"]?.trim() ||
    "python";
  const moduleName = process.env["PYTHON_BRAIN_MODULE"]?.trim() || "rlm.server.api";
  const env = {
    ...process.env,
    PYTHONUNBUFFERED: process.env["PYTHONUNBUFFERED"] ?? "1",
    RLM_API_HOST: process.env["RLM_API_HOST"] ?? url.hostname,
    RLM_API_PORT: process.env["RLM_API_PORT"] ?? portValue,
    RLM_GATEWAY_MODE: process.env["RLM_GATEWAY_MODE"] ?? "typescript",
    RLM_WS_DISABLED: process.env["RLM_WS_DISABLED"] ?? "true",
  };
  return {
    command,
    args: ["-m", moduleName],
    env,
  };
}

async function maybeStartPythonBrain(baseUrl: string): Promise<ChildProcess | null> {
  if (!envFlagEnabled("PYTHON_BRAIN_AUTOSTART", false)) {
    return null;
  }

  if (await isBrainReachable(baseUrl)) {
    logger.info({ pythonBaseUrl: baseUrl }, "Reusing existing Python brain process");
    return null;
  }

  const spawnOptions = buildBrainSpawnOptions(baseUrl);
  const child = spawn(spawnOptions.command, spawnOptions.args, {
    env: spawnOptions.env,
    stdio: "inherit",
  });

  child.on("exit", (code, signal) => {
    logger.warn({ code, signal }, "Managed Python brain process exited");
  });

  logger.info(
    {
      pythonBaseUrl: baseUrl,
      command: spawnOptions.command,
      args: spawnOptions.args,
      pid: child.pid,
    },
    "Managed Python brain process started",
  );

  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (await isBrainReachable(baseUrl)) {
      logger.info({ pythonBaseUrl: baseUrl }, "Python brain is reachable");
      return child;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  logger.warn({ pythonBaseUrl: baseUrl }, "Python brain did not become reachable before frontdoor startup");
  return child;
}

const mode = resolveMode();
const managedBrain = await maybeStartPythonBrain(pythonBaseUrl);

const runtime = createGatewayRuntimeFromEnv();
const app = createServerApp(runtime, { pythonBaseUrl });

const server = serve(
  {
    fetch: app.fetch,
    port,
  },
  (info) => {
    logger.info({ port: info.port, pythonBaseUrl, mode }, "Arkhe server started");
    runtime.start();
  },
);

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, "Shutdown signal received");
  await runtime.stop();
  if (managedBrain && managedBrain.exitCode === null && !managedBrain.killed) {
    managedBrain.kill("SIGTERM");
  }
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