/**
 * context.ts — Caminhos e contexto do CLI Arkhe.
 *
 * Porta fiel de rlm/cli/context.py
 */

import {
  existsSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import { join, resolve, dirname } from "node:path";
import { homedir } from "node:os";

// ---------------------------------------------------------------------------
// CliPaths
// ---------------------------------------------------------------------------

export interface CliPaths {
  cwd: string;
  home: string;
  projectRoot: string;
  stateRoot: string;
  launcherStatePath: string;
  runtimeDir: string;
  logDir: string;
  envPath: string;
  skillsDir: string;
}

function discoverProjectRoot(start: string): string {
  let current = resolve(start);
  // Percorre até a raiz procurando pyproject.toml ou .git
  while (true) {
    if (
      existsSync(join(current, "pyproject.toml")) ||
      existsSync(join(current, "package.json")) ||
      existsSync(join(current, ".git"))
    ) {
      return current;
    }
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return resolve(start);
}

export function discoverCliPaths(opts: {
  cwd?: string;
  home?: string;
  env?: NodeJS.ProcessEnv;
}): CliPaths {
  const cwd = resolve(opts.cwd ?? process.cwd());
  const home = opts.home ?? homedir();
  const env = opts.env ?? process.env;
  const projectRoot = discoverProjectRoot(cwd);
  const stateRoot = join(home, ".rlm");

  // Resolve .env: primeiro tenta cwd/.env, depois stateRoot/.env
  let envPath = join(cwd, ".env");
  if (!existsSync(envPath)) {
    envPath = join(stateRoot, ".env");
  }

  const configuredSkillsDir = (env["RLM_SKILLS_DIR"] ?? "").trim();
  const skillsDir = configuredSkillsDir
    ? resolve(configuredSkillsDir)
    : join(projectRoot, "rlm", "skills");

  return {
    cwd,
    home,
    projectRoot,
    stateRoot,
    launcherStatePath: join(stateRoot, "launcher-state.json"),
    runtimeDir: join(stateRoot, "run"),
    logDir: join(stateRoot, "logs"),
    envPath,
    skillsDir,
  };
}

// ---------------------------------------------------------------------------
// CliContext
// ---------------------------------------------------------------------------

export class CliContext {
  env: NodeJS.ProcessEnv;
  cwd: string;
  home: string;
  paths: CliPaths;

  constructor(opts: { cwd?: string; home?: string; env?: NodeJS.ProcessEnv } = {}) {
    this.cwd = resolve(opts.cwd ?? process.cwd());
    this.home = opts.home ?? homedir();
    this.env = { ...process.env, ...(opts.env ?? {}) };
    this.paths = discoverCliPaths({ cwd: this.cwd, home: this.home, env: this.env });
  }

  static fromEnvironment(opts: { loadEnv?: boolean } = {}): CliContext {
    const ctx = new CliContext();
    if (opts.loadEnv !== false) {
      ctx.loadEnvFile({ override: false });
    }
    return ctx;
  }

  refreshPaths(): CliPaths {
    this.paths = discoverCliPaths({ cwd: this.cwd, home: this.home, env: this.env });
    return this.paths;
  }

  /** Carrega variáveis de um .env sem depender de dotenv (zero-dep fallback). */
  loadEnvFile(opts: { override?: boolean } = {}): string | null {
    const envPath = this.paths.envPath;
    if (!existsSync(envPath)) return null;

    const lines = readFileSync(envPath, "utf8").split("\n");
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#") || !line.includes("=")) continue;
      const eqIdx = line.indexOf("=");
      const key = line.slice(0, eqIdx).trim();
      let value = line.slice(eqIdx + 1).trim();
      // Remove aspas envolventes
      if ((value.startsWith('"') && value.endsWith('"')) ||
          (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      if (opts.override || !(key in this.env)) {
        this.env[key] = value;
        process.env[key] = value;
      }
    }

    this.refreshPaths();
    return envPath;
  }

  hasTool(name: string): boolean {
    // Detecta se o binário está no PATH
    const { execSync } = require("node:child_process") as typeof import("node:child_process");
    const cmd = process.platform === "win32" ? `where ${name}` : `which ${name}`;
    try {
      execSync(cmd, { stdio: "pipe" });
      return true;
    } catch {
      return false;
    }
  }

  apiHost(): string {
    return this.env["RLM_API_HOST"] ?? "127.0.0.1";
  }

  apiPort(): number {
    return parseInt(this.env["RLM_API_PORT"] ?? "5000", 10);
  }

  wsHost(): string {
    return this.env["RLM_WS_HOST"] ?? this.apiHost();
  }

  wsPort(): number {
    return parseInt(this.env["RLM_WS_PORT"] ?? "8765", 10);
  }

  apiBaseUrl(): string {
    return `http://${this.apiHost()}:${this.apiPort()}`;
  }

  wsBaseUrl(): string {
    return `ws://${this.wsHost()}:${this.wsPort()}`;
  }

  docsUrl(): string {
    return `${this.apiBaseUrl()}/docs`;
  }

  webchatUrl(): string {
    return `${this.apiBaseUrl()}/webchat`;
  }
}
