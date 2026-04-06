/**
 * state/diagnosis.ts — Diagnóstico de alinhamento do launcher-state.
 *
 * Porta fiel de rlm/cli/state/diagnosis.py
 */

import { existsSync } from "node:fs";
import { join } from "node:path";
import { CliContext } from "../context.js";
import { portAcceptingConnections } from "../service-runtime.js";
import {
  loadLauncherState,
  summarizeLauncherState,
  LauncherState,
} from "./launcher.js";
import { readPidFile, pidAlive } from "./pid.js";

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

export type DiagnosisClassification =
  | "aligned"
  | "external-process"
  | "stale-after-crash"
  | "no-state";

export type DiagnosisSeverity = "ok" | "warning" | "info";

export interface LauncherStateDiagnosis {
  statusSymbol: string;
  severity: DiagnosisSeverity;
  classification: DiagnosisClassification;
  detail: string;
  summary: string;
  signals: {
    healthOnline: boolean;
    apiPid: number | null;
    wsPid: number | null;
    apiPidAlive: boolean;
    wsPidAlive: boolean;
    apiPortOpen: boolean;
    wsPortOpen: boolean;
    stateExists: boolean;
    persistedRunning: boolean;
  };
  persisted: {
    lastKnownStatus: string;
    lastLaunchMode: string | null;
    lastOperation: string | null;
    lastValidBootstrapAt: string | null;
    daemonManager: string | null;
  };
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function diagnosisSeverity(classification: DiagnosisClassification): DiagnosisSeverity {
  if (classification === "external-process" || classification === "stale-after-crash") {
    return "warning";
  }
  if (classification === "no-state") return "info";
  return "ok";
}

function defaultState(context: CliContext): LauncherState {
  return {
    schema_version: 1,
    updated_at: "",
    last_known_status: "unknown",
    last_launch_mode: "",
    last_operation: "",
    last_valid_bootstrap: {
      source: "",
      mode: "",
      succeeded_at: "",
      api_enabled: false,
      ws_enabled: false,
    },
    runtime_artifacts: {
      runtime_dir: "",
      log_dir: "",
      api_pid_file: "",
      ws_pid_file: "",
      api_log_file: "",
      ws_log_file: "",
      daemon_manager: "",
      daemon_definition: "",
    },
    metadata: {
      project_root: "",
      cwd: "",
      env_path: "",
      node_executable: "",
      platform: "",
    },
  };
}

// ---------------------------------------------------------------------------
// buildLauncherStateDiagnosis
// ---------------------------------------------------------------------------

export async function buildLauncherStateDiagnosis(
  context: CliContext,
  opts: { healthOnline: boolean },
): Promise<LauncherStateDiagnosis> {
  const { healthOnline } = opts;

  const statePath = context.paths.launcherStatePath;
  const stateExists = existsSync(statePath);
  const state = stateExists ? loadLauncherState(context.paths.stateRoot) ?? defaultState(context) : defaultState(context);

  const pidDir = context.paths.runtimeDir;
  const apiPidPath = join(pidDir, "api.pid");
  const wsPidPath = join(pidDir, "ws.pid");
  const apiPid = readPidFile(apiPidPath);
  const wsPid = readPidFile(wsPidPath);
  const apiPidAlive = apiPid !== null && pidAlive(apiPid);
  const wsPidAlive = wsPid !== null && pidAlive(wsPid);
  const apiPortOpen = await portAcceptingConnections(context.apiHost(), context.apiPort());
  const wsPortOpen = await portAcceptingConnections(context.wsHost(), context.wsPort());
  const persistedRunning = state.last_known_status === "running";
  const localRuntimeActive = healthOnline || apiPidAlive || wsPidAlive || apiPortOpen || wsPortOpen;
  const summary = summarizeLauncherState(state);

  let classification: DiagnosisClassification;
  let statusSymbol: string;
  let detail: string;

  if (localRuntimeActive && !apiPidAlive && !wsPidAlive) {
    classification = "external-process";
    statusSymbol = "⚠";
    detail = `processo externo ao launcher: runtime local ativo sem PID vivo do launcher (${summary})`;
  } else if (!localRuntimeActive && persistedRunning) {
    classification = "stale-after-crash";
    statusSymbol = "⚠";
    detail = `estado stale após crash: launcher-state ainda marca running, mas PID/porta locais estão inativos (${summary})`;
  } else if (!stateExists && !localRuntimeActive) {
    classification = "no-state";
    statusSymbol = "·";
    detail = "sem launcher-state local ainda";
  } else {
    classification = "aligned";
    statusSymbol = "✓";
    detail = summary;
  }

  return {
    statusSymbol,
    severity: diagnosisSeverity(classification),
    classification,
    detail,
    summary,
    signals: {
      healthOnline,
      apiPid,
      wsPid,
      apiPidAlive,
      wsPidAlive,
      apiPortOpen,
      wsPortOpen,
      stateExists,
      persistedRunning,
    },
    persisted: {
      lastKnownStatus: state.last_known_status,
      lastLaunchMode: state.last_launch_mode,
      lastOperation: state.last_operation,
      lastValidBootstrapAt: state.last_valid_bootstrap.succeeded_at,
      daemonManager: state.runtime_artifacts.daemon_manager,
    },
  };
}

// ---------------------------------------------------------------------------
// diagnoseLauncherStateAlignment — retorna [símbolo, detalhe]
// ---------------------------------------------------------------------------

export async function diagnoseLauncherStateAlignment(
  context: CliContext,
  opts: { serverOnline: boolean },
): Promise<[string, string]> {
  const d = await buildLauncherStateDiagnosis(context, { healthOnline: opts.serverOnline });
  return [d.statusSymbol, d.detail];
}
