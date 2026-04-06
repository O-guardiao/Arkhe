/**
 * service.ts — Facade do Service Manager do RLM.
 *
 * Porta fiel de rlm/cli/service.py
 * Delega para service-runtime, service-installers, service-update e service-wireguard.
 */

import { join } from "node:path";
import { c } from "./format.js";
import { buildCliJsonEnvelope } from "./json-output.js";
import { CliContext } from "./context.js";
import {
  buildRuntimeLayout,
  ServiceRuntimeLayout,
  runtimeMode,
  portAcceptingConnections,
  startRuntime,
  stopRuntime,
  showRuntimeStatus,
  servicesAreRunning,
} from "./service-runtime.js";
import {
  installSystemdService,
  installLaunchdService,
} from "./service-installers.js";
import { updateInstallation } from "./service-update.js";
import { addWireguardPeer as _addWireguardPeer } from "./service-wireguard.js";
import {
  markBootstrapSuccess,
  markDaemonInstalled,
  markRuntimeStatus,
  markStopped,
  markUpdate,
  summarizeLauncherState,
} from "./state/launcher.js";
import { buildLauncherStateDiagnosis } from "./state/diagnosis.js";

const SERVICE_NAME = "rlm";

// ---------------------------------------------------------------------------
// callbacks padrão (chalk)
// ---------------------------------------------------------------------------

const callbacks = {
  ok: (msg: string) => console.log(c.success(msg)),
  warn: (msg: string) => console.warn(c.warn(msg)),
  err: (msg: string) => console.error(c.error(msg)),
  info: (msg: string) => console.log(c.info(msg)),
};

// ---------------------------------------------------------------------------
// helpers internos
// ---------------------------------------------------------------------------

function getContext(ctx?: CliContext, loadEnv = true): CliContext {
  if (ctx) {
    if (loadEnv) ctx.loadEnvFile({ override: false });
    return ctx;
  }
  return CliContext.fromEnvironment({ loadEnv });
}

function getRuntimeLayout(ctx?: CliContext): ServiceRuntimeLayout {
  const context = ctx ?? CliContext.fromEnvironment({ loadEnv: false });
  return buildRuntimeLayout(context.paths.stateRoot, SERVICE_NAME);
}

// ---------------------------------------------------------------------------
// startServices
// ---------------------------------------------------------------------------

export async function startServices(opts: {
  foreground?: boolean;
  apiOnly?: boolean;
  wsOnly?: boolean;
  context?: CliContext;
} = {}): Promise<number> {
  const { foreground = false, apiOnly = false, wsOnly = false, context } = opts;
  const ctx = getContext(context, true);
  const layout = getRuntimeLayout(ctx);

  const rc = await startRuntime(ctx, layout, { foreground, apiOnly, wsOnly }, callbacks);
  if (rc === 0) {
    markBootstrapSuccess(ctx, {
      source: "start",
      mode: runtimeMode({ foreground, apiOnly, wsOnly }),
      apiEnabled: !wsOnly,
      wsEnabled: wsOnly || !apiOnly,
    });
  }
  return rc;
}

// ---------------------------------------------------------------------------
// stopServices
// ---------------------------------------------------------------------------

export async function stopServices(opts: { context?: CliContext } = {}): Promise<number> {
  const ctx = getContext(opts.context, false);
  const layout = getRuntimeLayout(ctx);
  const rc = await stopRuntime(layout, callbacks);
  if (rc === 0) markStopped(ctx);
  return rc;
}

// ---------------------------------------------------------------------------
// showStatus
// ---------------------------------------------------------------------------

export async function showStatus(opts: {
  context?: CliContext;
  jsonOutput?: boolean;
} = {}): Promise<number> {
  const { jsonOutput = false, context } = opts;
  const ctx = getContext(context, true);
  const layout = getRuntimeLayout(ctx);

  if (jsonOutput) {
    const apiPortOpen = await portAcceptingConnections(ctx.apiHost(), ctx.apiPort());
    const diagnosis = await buildLauncherStateDiagnosis(ctx, { healthOnline: apiPortOpen });

    const { readPidFile, pidAlive } = await import("./state/pid.js");
    const apiPid = readPidFile(layout.pidApi);
    const wsPid = readPidFile(layout.pidWs);
    const apiRunning = apiPid !== null && pidAlive(apiPid);
    const wsRunning = wsPid !== null && pidAlive(wsPid);
    const wsPortOpen = await portAcceptingConnections(ctx.wsHost(), ctx.wsPort());

    const state = markRuntimeStatus(ctx, { apiRunning, wsRunning });
    const payload = {
      runtime: {
        api: {
          pid: apiPid,
          running: apiRunning,
          portOpen: apiPortOpen,
          url: `${ctx.apiBaseUrl()}/`,
          docsUrl: ctx.docsUrl(),
          logFile: layout.apiLog,
        },
        ws: {
          pid: wsPid,
          running: wsRunning,
          portOpen: wsPortOpen,
          url: ctx.wsBaseUrl(),
          logFile: layout.wsLog,
        },
        webchatUrl: ctx.webchatUrl(),
      },
      launcherState: diagnosis,
      persistedStateSummary: summarizeLauncherState(state),
    };

    const envelope = buildCliJsonEnvelope("status", payload, diagnosis.severity === "warning" ? "warn" : "info");
    process.stdout.write(JSON.stringify(envelope, null, 2) + "\n");
    return 0;
  }

  const { exitCode, apiRunning, wsRunning } = await showRuntimeStatus(ctx, layout, callbacks);
  const state = markRuntimeStatus(ctx, { apiRunning, wsRunning });
  callbacks.info(`Launcher state: ${summarizeLauncherState(state)}`);
  return exitCode;
}

// ---------------------------------------------------------------------------
// installDaemon
// ---------------------------------------------------------------------------

export function installDaemon(opts: { context?: CliContext } = {}): number {
  const ctx = getContext(opts.context, true);
  const layout = getRuntimeLayout(ctx);
  const { projectRoot, envPath, logDir } = {
    projectRoot: ctx.paths.projectRoot,
    envPath: ctx.paths.envPath,
    logDir: layout.logDir,
  };

  if (process.platform === "linux") {
    const result = installSystemdService({
      projectRoot,
      envPath,
      logDir,
      serviceName: SERVICE_NAME,
      callbacks,
    });
    if (result.exitCode === 0) {
      markDaemonInstalled(ctx, {
        manager: "systemd",
        definitionPath: result.unitPath,
        projectRoot,
        envPath,
      });
    }
    return result.exitCode;
  }

  if (process.platform === "darwin") {
    const result = installLaunchdService({
      projectRoot,
      envPath,
      logDir,
      callbacks,
    });
    if (result.exitCode === 0) {
      markDaemonInstalled(ctx, {
        manager: "launchd",
        definitionPath: result.unitPath,
        projectRoot,
        envPath,
      });
    }
    return result.exitCode;
  }

  callbacks.err(`Instalação automática de daemon não suportada em ${process.platform}`);
  return 1;
}

// ---------------------------------------------------------------------------
// updateInstallationFacade
// ---------------------------------------------------------------------------

export async function updateInstallationFacade(opts: {
  checkOnly?: boolean;
  restart?: boolean;
  targetPath?: string;
  context?: CliContext;
} = {}): Promise<number> {
  const { checkOnly = false, restart = true, targetPath, context } = opts;
  const ctx = getContext(context, true);
  const layout = getRuntimeLayout(ctx);

  const rc = await updateInstallation(
    ctx,
    { checkOnly, restart, targetPath },
    callbacks,
    {
      servicesAreRunning: () => servicesAreRunning(layout),
      stopServices: () => stopServices({ context: ctx }),
      startServices: () => startServices({ context: ctx }),
    },
  );
  if (rc === 0 && !checkOnly) {
    markUpdate(ctx, { restarted: restart && servicesAreRunning(layout) });
  }
  return rc;
}

// ---------------------------------------------------------------------------
// addWireguardPeer
// ---------------------------------------------------------------------------

export function addWireguardPeerFacade(name: string, pubkey: string, ip: string): number {
  return _addWireguardPeer(name, pubkey, ip, "/etc/wireguard/wg0.conf", callbacks);
}
