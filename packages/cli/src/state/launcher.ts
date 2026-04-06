/**
 * state/launcher.ts — Estado persistido do launcher Arkhe.
 *
 * Porta fiel de rlm/cli/state/launcher.py
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { homedir, platform } from "node:os";

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

export interface LauncherMetadata {
  project_root: string;
  cwd: string;
  env_path: string;
  node_executable: string;
  platform: string;
}

export interface BootstrapRecord {
  source: string;
  mode: string;
  succeeded_at: string;
  api_enabled: boolean;
  ws_enabled: boolean;
}

export interface RuntimeArtifacts {
  runtime_dir: string;
  log_dir: string;
  api_pid_file: string;
  ws_pid_file: string;
  api_log_file: string;
  ws_log_file: string;
  daemon_manager: string;
  daemon_definition: string;
}

export interface LauncherState {
  schema_version: number;
  updated_at: string;
  last_known_status: string;
  last_launch_mode: string;
  last_operation: string;
  metadata: LauncherMetadata;
  last_valid_bootstrap: BootstrapRecord;
  runtime_artifacts: RuntimeArtifacts;
}

// ---------------------------------------------------------------------------
// Helpers internos
// ---------------------------------------------------------------------------

function utcNow(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function defaultMetadata(stateRoot: string): LauncherMetadata {
  return {
    project_root: resolve(process.cwd()),
    cwd: resolve(process.cwd()),
    env_path: resolve(join(process.cwd(), ".env")),
    node_executable: process.execPath,
    platform: `${platform()}-${process.arch}`,
  };
}

function defaultArtifacts(stateRoot: string): RuntimeArtifacts {
  const runDir = join(stateRoot, "run");
  const logDir = join(stateRoot, "logs");
  return {
    runtime_dir: resolve(runDir),
    log_dir: resolve(logDir),
    api_pid_file: resolve(join(runDir, "api.pid")),
    ws_pid_file: resolve(join(runDir, "ws.pid")),
    api_log_file: resolve(join(logDir, "api.log")),
    ws_log_file: resolve(join(logDir, "ws.log")),
    daemon_manager: "",
    daemon_definition: "",
  };
}

function defaultState(stateRoot: string): LauncherState {
  return {
    schema_version: 1,
    updated_at: utcNow(),
    last_known_status: "stopped",
    last_launch_mode: "",
    last_operation: "",
    metadata: defaultMetadata(stateRoot),
    last_valid_bootstrap: {
      source: "",
      mode: "",
      succeeded_at: "",
      api_enabled: false,
      ws_enabled: false,
    },
    runtime_artifacts: defaultArtifacts(stateRoot),
  };
}

function stateFromJson(raw: unknown, stateRoot: string): LauncherState {
  const p = (raw as Record<string, unknown>) ?? {};
  const meta = (p["metadata"] as Record<string, string>) ?? {};
  const bootstrap = (p["last_valid_bootstrap"] as Record<string, unknown>) ?? {};
  const artifacts = (p["runtime_artifacts"] as Record<string, string>) ?? {};

  const base = defaultState(stateRoot);
  return {
    schema_version: Number(p["schema_version"] ?? 1),
    updated_at: String(p["updated_at"] ?? utcNow()),
    last_known_status: String(p["last_known_status"] ?? "stopped"),
    last_launch_mode: String(p["last_launch_mode"] ?? ""),
    last_operation: String(p["last_operation"] ?? ""),
    metadata: { ...base.metadata, ...Object.fromEntries(Object.entries(meta).map(([k, v]) => [k, String(v)])) },
    last_valid_bootstrap: {
      source: String(bootstrap["source"] ?? ""),
      mode: String(bootstrap["mode"] ?? ""),
      succeeded_at: String(bootstrap["succeeded_at"] ?? ""),
      api_enabled: Boolean(bootstrap["api_enabled"] ?? false),
      ws_enabled: Boolean(bootstrap["ws_enabled"] ?? false),
    },
    runtime_artifacts: {
      ...base.runtime_artifacts,
      ...Object.fromEntries(Object.entries(artifacts).map(([k, v]) => [k, String(v)])),
    },
  };
}

// ---------------------------------------------------------------------------
// API pública
// ---------------------------------------------------------------------------

/** Caminho padrão do state root (~/.rlm). */
export function defaultStateRoot(): string {
  return join(homedir(), ".rlm");
}

/** Caminho do arquivo launcher-state.json. */
export function launcherStatePath(stateRoot = defaultStateRoot()): string {
  return join(stateRoot, "launcher-state.json");
}

/** Carrega o estado do disco, retornando estado padrão se ausente/corrompido. */
export function loadLauncherState(stateRoot = defaultStateRoot()): LauncherState {
  const path = launcherStatePath(stateRoot);
  if (!existsSync(path)) return defaultState(stateRoot);
  try {
    const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
    const state = stateFromJson(raw, stateRoot);
    state.updated_at = utcNow();
    return state;
  } catch {
    return defaultState(stateRoot);
  }
}

/** Persiste o estado no disco. */
export function saveLauncherState(state: LauncherState, stateRoot = defaultStateRoot()): string {
  const path = launcherStatePath(stateRoot);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(state, null, 2), "utf8");
  return path;
}

/** Aplica um mutador ao estado e salva. */
export function updateLauncherState(
  mutator: (s: LauncherState) => void,
  stateRoot = defaultStateRoot()
): LauncherState {
  const state = loadLauncherState(stateRoot);
  mutator(state);
  state.updated_at = utcNow();
  saveLauncherState(state, stateRoot);
  return state;
}

/** Marca bootstrap bem-sucedido. */
export function markBootstrapSuccess(
  stateRoot: string,
  opts: { source: string; mode: string; api_enabled: boolean; ws_enabled: boolean }
): LauncherState {
  return updateLauncherState((s) => {
    s.last_known_status = "running";
    s.last_launch_mode = opts.mode;
    s.last_operation = "start";
    s.last_valid_bootstrap = {
      source: opts.source,
      mode: opts.mode,
      succeeded_at: utcNow(),
      api_enabled: opts.api_enabled,
      ws_enabled: opts.ws_enabled,
    };
  }, stateRoot);
}

/** Marca runtime como parado. */
export function markStopped(stateRoot = defaultStateRoot()): LauncherState {
  return updateLauncherState((s) => {
    s.last_known_status = "stopped";
    s.last_operation = "stop";
  }, stateRoot);
}

/** Marca status de runtime (string livre: running, stopped, error). */
export function markRuntimeStatus(status: string, stateRoot = defaultStateRoot()): LauncherState {
  return updateLauncherState((s) => { s.last_known_status = status; }, stateRoot);
}

/** Marca instalação de daemon. */
export function markDaemonInstalled(
  daemonManager: string,
  daemonDefinition: string,
  stateRoot = defaultStateRoot()
): LauncherState {
  return updateLauncherState((s) => {
    s.runtime_artifacts.daemon_manager = daemonManager;
    s.runtime_artifacts.daemon_definition = daemonDefinition;
    s.last_operation = "install-daemon";
  }, stateRoot);
}

/** Marca operação de update. */
export function markUpdate(stateRoot = defaultStateRoot()): LauncherState {
  return updateLauncherState((s) => { s.last_operation = "update"; }, stateRoot);
}

/** Resumo legível do estado atual. */
export function summarizeLauncherState(state: LauncherState): string {
  const parts: string[] = [];
  if (state.last_known_status) parts.push(`status=${state.last_known_status}`);
  if (state.last_launch_mode) parts.push(`modo=${state.last_launch_mode}`);
  if (state.last_valid_bootstrap.succeeded_at) {
    parts.push(`bootstrap=${state.last_valid_bootstrap.succeeded_at}`);
  }
  if (state.last_operation) parts.push(`op=${state.last_operation}`);
  return parts.join(" | ") || "(sem estado)";
}
