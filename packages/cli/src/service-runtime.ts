/**
 * service-runtime.ts — Gerenciamento de processos de runtime do RLM.
 *
 * Porta fiel de rlm/cli/service_runtime.py
 * Controla start/stop/status dos processos API e WebSocket.
 */

import {
  existsSync,
  mkdirSync,
  appendFileSync,
  openSync,
  closeSync,
  readFileSync,
} from "node:fs";
import { join } from "node:path";
import {
  spawn,
  execSync,
  spawnSync,
} from "node:child_process";
import net from "node:net";
import { CliContext } from "./context.js";
import { readPidFile, writePid, removePid, pidAlive } from "./state/pid.js";

function resolveServerEntry(context: CliContext): string {
  const explicitEntry = process.env["RLM_SERVER_ENTRY"]?.trim();
  if (explicitEntry) {
    return explicitEntry;
  }

  const serverPackageDir = join(context.paths.projectRoot, "packages", "server");
  const distEntry = join(serverPackageDir, "dist", "index.js");
  const npmBinary = process.platform === "win32" ? "npm.cmd" : "npm";

  if (!existsSync(join(serverPackageDir, "package.json"))) {
    throw new Error(`Pacote packages/server não encontrado em ${serverPackageDir}`);
  }

  if (existsSync(distEntry)) {
    return distEntry;
  }

  if (!existsSync(join(serverPackageDir, "node_modules"))) {
    const install = spawnSync(npmBinary, ["install"], {
      cwd: serverPackageDir,
      stdio: "inherit",
    });
    if (install.status !== 0) {
      throw new Error("Falha ao instalar dependências de packages/server");
    }
  }

  const build = spawnSync(npmBinary, ["run", "build"], {
    cwd: serverPackageDir,
    stdio: "inherit",
  });
  if (build.status !== 0 || !existsSync(distEntry)) {
    throw new Error("Falha ao compilar packages/server");
  }

  return distEntry;
}

// ---------------------------------------------------------------------------
// ServiceRuntimeLayout
// ---------------------------------------------------------------------------

export interface ServiceRuntimeLayout {
  pidDir: string;
  pidApi: string;
  pidWs: string;
  logDir: string;
  serviceName: string;
  apiLog: string;
  wsLog: string;
}

export function buildRuntimeLayout(stateRoot: string, serviceName = "rlm"): ServiceRuntimeLayout {
  const pidDir = join(stateRoot, "run");
  const logDir = join(stateRoot, "logs");
  return {
    pidDir,
    pidApi: join(pidDir, "api.pid"),
    pidWs: join(pidDir, "ws.pid"),
    logDir,
    serviceName,
    apiLog: join(logDir, "api.log"),
    wsLog: join(logDir, "ws.log"),
  };
}

// ---------------------------------------------------------------------------
// runtimeMode
// ---------------------------------------------------------------------------

export type RuntimeMode =
  | "foreground-combined"
  | "foreground-api"
  | "foreground-ws"
  | "background-combined"
  | "background-api"
  | "background-ws";

export function runtimeMode(opts: {
  foreground: boolean;
  apiOnly: boolean;
  wsOnly: boolean;
}): RuntimeMode {
  if (opts.foreground && opts.wsOnly) return "foreground-ws";
  if (opts.foreground && opts.apiOnly) return "foreground-api";
  if (opts.foreground) return "foreground-combined";
  if (opts.wsOnly) return "background-ws";
  if (opts.apiOnly) return "background-api";
  return "background-combined";
}

// ---------------------------------------------------------------------------
// portAcceptingConnections
// ---------------------------------------------------------------------------

export function portAcceptingConnections(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(300);
    socket.connect(port, host, () => {
      socket.destroy();
      resolve(true);
    });
    socket.on("error", () => {
      socket.destroy();
      resolve(false);
    });
    socket.on("timeout", () => {
      socket.destroy();
      resolve(false);
    });
  });
}

// ---------------------------------------------------------------------------
// findPortPid — encontra PID usando fuser (Linux)
// ---------------------------------------------------------------------------

function findPortPid(port: number): number | null {
  if (process.platform === "win32") return null;
  try {
    const result = spawnSync("fuser", [`${port}/tcp`], { timeout: 5000 });
    const raw = (result.stderr?.toString() ?? "") + (result.stdout?.toString() ?? "");
    for (const token of raw.split(/\s+/)) {
      const t = token.trim();
      if (/^\d+$/.test(t)) return parseInt(t, 10);
    }
  } catch {
    // fuser não disponível
  }
  return null;
}

// ---------------------------------------------------------------------------
// killOrphanOnPort
// ---------------------------------------------------------------------------

export async function killOrphanOnPort(
  port: number,
  host: string,
  callbacks: {
    warn: (msg: string) => void;
    info: (msg: string) => void;
  },
): Promise<boolean> {
  if (!(await portAcceptingConnections(host, port))) return true; // porta livre

  const pid = findPortPid(port);
  if (pid === null) {
    callbacks.info(`Porta ${port} ocupada sem processo ativo — aguardando liberação...`);
    for (let i = 0; i < 10; i++) {
      await sleep(500);
      if (!(await portAcceptingConnections(host, port))) {
        callbacks.info(`Porta ${port} liberada.`);
        return true;
      }
    }
    return false;
  }

  if (pid === process.pid) return false;
  callbacks.warn(`Porta ${port} ocupada por processo órfão pid=${pid} — encerrando...`);

  try {
    process.kill(pid, "SIGTERM");
  } catch {
    return false;
  }

  for (let i = 0; i < 6; i++) {
    await sleep(500);
    if (!(await portAcceptingConnections(host, port))) {
      callbacks.info(`Processo órfão pid=${pid} encerrado, porta ${port} liberada.`);
      return true;
    }
  }

  try {
    process.kill(pid, "SIGKILL");
    await sleep(500);
  } catch {
    // ok
  }

  const freed = !(await portAcceptingConnections(host, port));
  if (freed) callbacks.info(`Processo órfão pid=${pid} forçadamente encerrado (SIGKILL).`);
  return freed;
}

// ---------------------------------------------------------------------------
// startRuntime — inicia os processos em background via spawn
// ---------------------------------------------------------------------------

export async function startRuntime(
  context: CliContext,
  layout: ServiceRuntimeLayout,
  opts: {
    foreground?: boolean;
    apiOnly?: boolean;
    wsOnly?: boolean;
  },
  callbacks: {
    ok: (msg: string) => void;
    warn: (msg: string) => void;
    err: (msg: string) => void;
    info: (msg: string) => void;
  },
): Promise<number> {
  const { foreground = false, apiOnly = false, wsOnly = false } = opts;
  const env: NodeJS.ProcessEnv = { ...process.env, ...context.env };
  const { ok, warn, err, info } = callbacks;
  const modeArg = wsOnly ? "ws" : apiOnly ? "api" : "server";

  mkdirSync(layout.logDir, { recursive: true });
  mkdirSync(layout.pidDir, { recursive: true });

  const node = process.execPath;
  const serverEntry = resolveServerEntry(context);
  const apiPort = parseInt(env["RLM_API_PORT"] ?? "5000", 10);
  const apiHost = env["RLM_API_HOST"] ?? "127.0.0.1";

  if (await portAcceptingConnections(apiHost, apiPort)) {
    const freed = await killOrphanOnPort(apiPort, apiHost, { warn, info });
    if (!freed) {
      err(
        `Porta ${apiPort} já está em uso e não foi possível liberar. ` +
        `Execute: fuser -k ${apiPort}/tcp`,
      );
      return 1;
    }
  }

  const spawnEnv = { ...env } as NodeJS.ProcessEnv;
  spawnEnv["PORT"] = String(apiPort);
  spawnEnv["PYTHON_BRAIN_AUTOSTART"] = spawnEnv["PYTHON_BRAIN_AUTOSTART"] ?? "true";
  spawnEnv["PYTHON_BRAIN_BASE_URL"] = spawnEnv["PYTHON_BRAIN_BASE_URL"] ?? "http://127.0.0.1:8000";
  if (apiOnly) spawnEnv["RLM_WS_DISABLED"] = "true";

  if (foreground) {
    info("Iniciando frontdoor TypeScript em foreground...");
    const foregroundProc = spawn(node, [serverEntry, modeArg], {
      env: spawnEnv,
      stdio: "inherit",
    });

    const exitCode = await new Promise<number>((resolve) => {
      foregroundProc.on("exit", (code) => resolve(code ?? 0));
    });
    return exitCode;
  }

  info("Iniciando frontdoor TypeScript...");
  const logFd = openSync(layout.apiLog, "a");
  const runtimeProc = spawn(node, [serverEntry, modeArg], {
    env: spawnEnv,
    detached: true,
    stdio: ["ignore", logFd, logFd],
  });
  runtimeProc.unref();
  closeSync(logFd);
  await sleep(1500);

  if (runtimeProc.exitCode !== null) {
    removePid(layout.pidApi);
    removePid(layout.pidWs);
    err(`Frontdoor TS falhou ao iniciar (exit=${runtimeProc.exitCode}). Ver log: ${layout.apiLog}`);
    try {
      const lines = readFileSync(layout.apiLog, "utf8").split("\n").slice(-15);
      for (const ln of lines) process.stderr.write(`  ${ln}\n`);
    } catch { /* ok */ }
    return 1;
  }

  if (wsOnly) {
    removePid(layout.pidApi);
    writePid(layout.pidWs, runtimeProc.pid!);
    ok(`Bridge iniciada  pid=${runtimeProc.pid}  log=${layout.apiLog}`);
  } else if (apiOnly) {
    writePid(layout.pidApi, runtimeProc.pid!);
    removePid(layout.pidWs);
    ok(`Frontdoor TS iniciado  pid=${runtimeProc.pid}  log=${layout.apiLog}`);
  } else {
    writePid(layout.pidApi, runtimeProc.pid!);
    writePid(layout.pidWs, runtimeProc.pid!);
    ok(`Frontdoor TS iniciado  pid=${runtimeProc.pid}  log=${layout.apiLog}`);
  }

  if (!apiOnly && !wsOnly) {
    ok(`RLM em execução — use \`rlm stop\` para encerrar`);
  } else if (apiOnly) {
    ok(`RLM API em execução — use \`rlm stop\` para encerrar`);
  } else {
    ok(`RLM WS em execução — use \`rlm stop\` para encerrar`);
  }
  return 0;
}

// ---------------------------------------------------------------------------
// stopRuntime
// ---------------------------------------------------------------------------

export async function stopRuntime(
  layout: ServiceRuntimeLayout,
  callbacks: {
    ok: (msg: string) => void;
    warn: (msg: string) => void;
    err: (msg: string) => void;
    info: (msg: string) => void;
  },
): Promise<number> {
  const { ok, warn, err, info } = callbacks;

  // Tenta parar via systemd se disponível
  try {
    const result = spawnSync("systemctl", ["--user", "is-active", layout.serviceName], {
      timeout: 2000,
    });
    if (result.status === 0) {
      spawnSync("systemctl", ["--user", "stop", layout.serviceName]);
      ok("Daemon systemd parado");
      return 0;
    }
  } catch { /* systemctl não disponível */ }

  let stopped = false;
  const terminatedPids = new Set<number>();

  for (const [label, pidFile] of [
    ["API", layout.pidApi] as const,
    ["WebSocket", layout.pidWs] as const,
  ]) {
    const pid = readPidFile(pidFile);
    if (pid === null) continue;
    if (terminatedPids.has(pid)) {
      removePid(pidFile);
      continue;
    }
    if (pidAlive(pid)) {
      try {
        process.kill(pid, "SIGTERM");
        ok(`${label} encerrado (pid=${pid})`);
        stopped = true;
        terminatedPids.add(pid);
      } catch (e) {
        err(`Não foi possível encerrar ${label} pid=${pid}: ${e}`);
      }
    } else {
      info(`${label} pid=${pid} não estava rodando`);
    }
    removePid(pidFile);
  }

  if (!stopped) {
    warn("Nenhum processo RLM encontrado.");
  }
  return 0;
}

// ---------------------------------------------------------------------------
// showRuntimeStatus
// ---------------------------------------------------------------------------

export async function showRuntimeStatus(
  context: CliContext,
  layout: ServiceRuntimeLayout,
  callbacks: {
    info: (msg: string) => void;
    warn: (msg: string) => void;
  },
): Promise<{ exitCode: number; apiRunning: boolean; wsRunning: boolean }> {
  const { info, warn } = callbacks;
  const apiPid = readPidFile(layout.pidApi);
  const wsPid = readPidFile(layout.pidWs);
  const apiRunning = apiPid !== null && pidAlive(apiPid);
  const wsRunning = wsPid !== null && pidAlive(wsPid);

  const apiStatus =
    apiRunning ? "● ativo" : apiPid !== null ? "✗ morto (pid inválido)" : "● parado";
  const wsStatus =
    wsRunning
      ? wsPid === apiPid
        ? "● ativo (embutido na API)"
        : "● ativo"
      : wsPid !== null
      ? "✗ morto (pid inválido)"
      : "● parado";

  console.log(`  Frontdoor TS       pid=${apiPid ?? "—"}  ${apiStatus}  log=${layout.apiLog}`);
  console.log(`  Bridge Embutida    pid=${wsPid ?? "—"}  ${wsStatus}  log=${layout.wsLog}`);

  info(`API:  ${context.apiBaseUrl()}/`);
  info(`WS:   ${context.wsBaseUrl()}/`);
  info(`Docs: ${context.docsUrl()}`);
  info(`Chat: ${context.webchatUrl()}`);

  if ((await portAcceptingConnections(context.apiHost(), context.apiPort())) && !apiPid) {
    warn("Porta da API responde, mas não há PID file. Pode haver processo externo ao gerenciador.");
  }

  return { exitCode: 0, apiRunning, wsRunning };
}

// ---------------------------------------------------------------------------
// servicesAreRunning
// ---------------------------------------------------------------------------

export function servicesAreRunning(layout: ServiceRuntimeLayout): boolean {
  for (const pidFile of [layout.pidApi, layout.pidWs]) {
    const pid = readPidFile(pidFile);
    if (pid !== null && pidAlive(pid)) return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
