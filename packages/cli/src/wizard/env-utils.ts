/**
 * Utilitários de ambiente para o wizard — detecção de sistema, leitura/escrita
 * de .env, catálogo de modelos e helpers de coleta interativa.
 *
 * Migrado de rlm/cli/wizard/env_utils.py
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as net from "node:net";
import { execFileSync } from "node:child_process";
import type { WizardPrompter, SelectOption } from "./prompter.js";

// ─────────────────────────────────────────────── Detecção de ambiente

export class EnvDetector {
  readonly system: string;
  readonly isWsl: boolean;
  readonly isLinux: boolean;
  readonly isMacos: boolean;
  readonly isWindows: boolean;
  readonly hasSystemd: boolean;
  readonly hasLaunchd: boolean;
  readonly uvPath: string | null;
  readonly nodePath: string;

  private constructor() {
    this.system = process.platform === "win32" ? "Windows"
      : process.platform === "darwin" ? "Darwin"
      : "Linux";

    // WSL detection via /proc/version
    let isWsl = false;
    if (process.platform === "linux") {
      try {
        const procVersion = fs.readFileSync("/proc/version", "utf8").toLowerCase();
        isWsl = procVersion.includes("microsoft");
      } catch {
        // not WSL or no /proc/version
      }
    }
    this.isWsl = isWsl;
    this.isLinux = process.platform === "linux";
    this.isMacos = process.platform === "darwin";
    this.isWindows = process.platform === "win32";

    // systemd detection
    this.hasSystemd = this.isLinux && (
      fs.existsSync("/run/systemd/system") ||
      fs.existsSync("/sys/fs/cgroup/systemd")
    );
    this.hasLaunchd = this.isMacos;

    // uv path
    this.uvPath = _which("uv");

    // node path
    this.nodePath = process.execPath;
  }

  static detect(): EnvDetector {
    return new EnvDetector();
  }
}

function _which(cmd: string): string | null {
  try {
    const result = execFileSync(
      process.platform === "win32" ? "where" : "which",
      [cmd],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }
    ).trim().split(/\r?\n/)[0] ?? "";
    return result || null;
  } catch {
    return null;
  }
}

// ─────────────────────────────────────────────── Chaves de .env

export const LLM_SECTION_KEYS = [
  "OPENAI_API_KEY",
  "ANTHROPIC_API_KEY",
  "GOOGLE_API_KEY",
  "RLM_MODEL",
  "RLM_MODEL_PLANNER",
  "RLM_MODEL_WORKER",
  "RLM_MODEL_EVALUATOR",
  "RLM_MODEL_FAST",
  "RLM_MODEL_MINIREPL",
] as const;

export const SERVER_SECTION_KEYS = [
  "RLM_API_HOST",
  "RLM_API_PORT",
  "RLM_WS_HOST",
  "RLM_WS_PORT",
] as const;

export const SECURITY_SECTION_KEYS = [
  "RLM_WS_TOKEN",
  "RLM_INTERNAL_TOKEN",
  "RLM_ADMIN_TOKEN",
  "RLM_HOOK_TOKEN",
  "RLM_API_TOKEN",
] as const;

export const MANAGED_ENV_SECTIONS: Array<[string, readonly string[]]> = [
  ["LLM", LLM_SECTION_KEYS],
  ["Server", SERVER_SECTION_KEYS],
  ["Security", SECURITY_SECTION_KEYS],
];

export const MANAGED_ENV_KEYS = new Set<string>([
  ...LLM_SECTION_KEYS,
  ...SERVER_SECTION_KEYS,
  ...SECURITY_SECTION_KEYS,
]);

// ─────────────────────────────────────────────── Especificações de papéis

/** [envKey, shortLabel, description] */
export const MODEL_ROLE_SPECS: Array<[string, string, string]> = [
  ["RLM_MODEL_PLANNER", "planner", "planejamento de tarefas"],
  ["RLM_MODEL_WORKER", "worker", "execução de subtarefas"],
  ["RLM_MODEL_EVALUATOR", "evaluator", "avaliação de resultados"],
  ["RLM_MODEL_FAST", "fast", "respostas rápidas"],
  ["RLM_MODEL_MINIREPL", "minirepl", "REPL interativo"],
];

// ─────────────────────────────────────────────── Catálogos de modelos

export const PROVIDER_MODEL_OPTIONS: Record<string, SelectOption<string>[]> = {
  openai: [
    { value: "gpt-5.4-mini", label: "gpt-5.4-mini", hint: "equilíbrio custo/qualidade (recomendado)" },
    { value: "gpt-5.4", label: "gpt-5.4", hint: "mais capaz, maior custo" },
    { value: "gpt-5.4-nano", label: "gpt-5.4-nano", hint: "mais barato e rápido" },
    { value: "gpt-5-nano", label: "gpt-5-nano", hint: "mini-tarefas e mini-REPL" },
    { value: "gpt-4o-mini", label: "gpt-4o-mini", hint: "legado — custo baixo" },
    { value: "gpt-4o", label: "gpt-4o", hint: "legado — alto desempenho" },
    { value: "o3-mini", label: "o3-mini", hint: "raciocínio avançado" },
  ],
  anthropic: [
    { value: "claude-sonnet-4-5", label: "claude-sonnet-4-5", hint: "equilíbrio (recomendado)" },
    { value: "claude-3-5-haiku-latest", label: "claude-3-5-haiku-latest", hint: "rápido e barato" },
    { value: "claude-opus-4", label: "claude-opus-4", hint: "mais capaz" },
  ],
  google: [
    { value: "gemini-2.5-flash", label: "gemini-2.5-flash", hint: "rápido e gratuito" },
    { value: "gemini-2.5-pro", label: "gemini-2.5-pro", hint: "mais capaz" },
  ],
  custom: [],
};

export const PROVIDER_ROLE_DEFAULTS: Record<string, Record<string, string>> = {
  openai: {
    RLM_MODEL_WORKER: "gpt-5.4-mini",
    RLM_MODEL_EVALUATOR: "gpt-5.4-mini",
    RLM_MODEL_FAST: "gpt-5.4-nano",
    RLM_MODEL_MINIREPL: "gpt-5-nano",
  },
  anthropic: {
    RLM_MODEL_WORKER: "claude-3-5-haiku-latest",
    RLM_MODEL_EVALUATOR: "claude-3-5-haiku-latest",
    RLM_MODEL_FAST: "claude-3-5-haiku-latest",
    RLM_MODEL_MINIREPL: "claude-3-5-haiku-latest",
  },
  google: {
    RLM_MODEL_WORKER: "gemini-2.5-flash",
    RLM_MODEL_EVALUATOR: "gemini-2.5-flash",
    RLM_MODEL_FAST: "gemini-2.5-flash",
    RLM_MODEL_MINIREPL: "gemini-2.5-flash",
  },
};

// ─────────────────────────────────────────────── Resolução e leitura do .env

export function resolveEnvPath(projectRoot: string): string {
  const candidate = path.join(projectRoot, ".env");
  if (fs.existsSync(candidate)) return candidate;
  // Walk up looking for .env
  let dir = projectRoot;
  for (let i = 0; i < 5; i++) {
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
    const p2 = path.join(dir, ".env");
    if (fs.existsSync(p2)) return p2;
  }
  return candidate; // return default even if not yet created
}

export function loadExistingEnv(envPath: string): Record<string, string> {
  const result: Record<string, string> = {};
  if (!fs.existsSync(envPath)) return result;
  const lines = fs.readFileSync(envPath, "utf8").split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eqIdx = line.indexOf("=");
    if (eqIdx < 1) continue;
    const key = line.slice(0, eqIdx).trim();
    let val = line.slice(eqIdx + 1).trim();
    // Strip surrounding quotes
    if ((val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    result[key] = val;
  }
  return result;
}

export function writeEnv(envPath: string, values: Record<string, string>): void {
  const sections: string[] = [];

  for (const [sectionName, keys] of MANAGED_ENV_SECTIONS) {
    const lines: string[] = [`# ── ${sectionName} ──`];
    for (const key of keys) {
      if (Object.prototype.hasOwnProperty.call(values, key)) {
        lines.push(`${key}=${values[key]}`);
      }
    }
    if (lines.length > 1) {
      sections.push(lines.join("\n"));
    }
  }

  // Extra keys not in managed sections
  const extras: string[] = [];
  for (const [k, v] of Object.entries(values)) {
    if (!MANAGED_ENV_KEYS.has(k)) {
      extras.push(`${k}=${v}`);
    }
  }
  if (extras.length > 0) {
    sections.push("# ── Extra ──\n" + extras.join("\n"));
  }

  const content = sections.join("\n\n") + "\n";
  fs.mkdirSync(path.dirname(envPath), { recursive: true });
  fs.writeFileSync(envPath, content, "utf8");
}

// ─────────────────────────────────────────────── Teste de chave OpenAI

export async function testOpenAiKey(apiKey: string): Promise<boolean> {
  try {
    const resp = await fetch("https://api.openai.com/v1/models", {
      method: "GET",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      signal: AbortSignal.timeout(10_000),
    });
    return resp.ok;
  } catch {
    return false;
  }
}

// ─────────────────────────────────────────────── Helpers de seleção de modelo

export function getModelOptions(provider: string): SelectOption<string>[] {
  const catalog = PROVIDER_MODEL_OPTIONS[provider] ?? [];
  return [
    ...catalog,
    { value: "custom", label: "Outro (digitar manualmente)", hint: "qualquer identificador" },
  ];
}

export async function promptModelName(
  p: WizardPrompter,
  provider: string,
  message: string,
  defaultModel: string,
  options: SelectOption<string>[]
): Promise<string> {
  const inCatalog = options.some((o) => o.value === defaultModel && o.value !== "custom");

  const selected = await p.select(message, options, inCatalog ? defaultModel : undefined);
  if (selected === "custom") {
    return p.text(`Identificador do modelo (${provider})`, { default: defaultModel });
  }
  return selected;
}

// ─────────────────────────────────────────────── Defaults de papéis por modelo base

export function buildRoleModelDefaults(
  existing: Record<string, string>,
  provider: string,
  baseModel: string
): Record<string, string> {
  const providerDefaults = PROVIDER_ROLE_DEFAULTS[provider] ?? {};
  const workerDefault =
    existing["RLM_MODEL_WORKER"] ||
    providerDefaults["RLM_MODEL_WORKER"] ||
    baseModel;
  const fastDefault =
    existing["RLM_MODEL_FAST"] ||
    providerDefaults["RLM_MODEL_FAST"] ||
    workerDefault;

  return {
    RLM_MODEL_PLANNER: existing["RLM_MODEL_PLANNER"] || baseModel,
    RLM_MODEL_WORKER: workerDefault,
    RLM_MODEL_EVALUATOR:
      existing["RLM_MODEL_EVALUATOR"] ||
      providerDefaults["RLM_MODEL_EVALUATOR"] ||
      workerDefault,
    RLM_MODEL_FAST: fastDefault,
    RLM_MODEL_MINIREPL:
      existing["RLM_MODEL_MINIREPL"] ||
      providerDefaults["RLM_MODEL_MINIREPL"] ||
      fastDefault,
  };
}

// ─────────────────────────────────────────────── Formatação de resumo de papéis

export function formatRoleModelSummary(values: Record<string, string>): string {
  const parts: string[] = [];
  for (const [envName, label] of MODEL_ROLE_SPECS) {
    const modelName = values[envName];
    if (modelName) {
      parts.push(`${label}=${modelName}`);
    }
  }
  return parts.length > 0 ? "  • " + parts.join("\n  • ") : "";
}

// ─────────────────────────────────────────────── Resumo da config existente

export function summarizeExistingConfig(existing: Record<string, string>): string {
  const lines: string[] = [];
  if (existing["OPENAI_API_KEY"]) {
    const k = existing["OPENAI_API_KEY"];
    lines.push(`  • OpenAI API Key: sk-…${k.slice(-6)}`);
  }
  if (existing["ANTHROPIC_API_KEY"]) {
    lines.push(`  • Anthropic API Key: …${existing["ANTHROPIC_API_KEY"].slice(-6)}`);
  }
  if (existing["RLM_MODEL"]) {
    lines.push(`  • Modelo base: ${existing["RLM_MODEL"]}`);
  }
  const roleSummary = formatRoleModelSummary(existing);
  if (roleSummary) lines.push(roleSummary);
  if (existing["RLM_API_HOST"]) {
    lines.push(
      `  • API: ${existing["RLM_API_HOST"] ?? "?"}:${existing["RLM_API_PORT"] ?? "?"}`
    );
  }
  if (existing["RLM_WS_HOST"]) {
    lines.push(
      `  • WebSocket: ${existing["RLM_WS_HOST"] ?? "?"}:${existing["RLM_WS_PORT"] ?? "?"}`
    );
  }
  const tokenCount = Object.keys(existing).filter((k) => k.endsWith("_TOKEN")).length;
  if (tokenCount > 0) {
    lines.push(`  • Tokens de segurança: ${tokenCount} configurados`);
  }
  return lines.length > 0 ? lines.join("\n") : "  (vazio)";
}

// ─────────────────────────────────────────────── Probe ao servidor

export async function probeServer(host: string, port: string): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = net.createConnection({ host, port: parseInt(port, 10) });
    sock.setTimeout(2000);
    sock.once("connect", () => { sock.destroy(); resolve(true); });
    sock.once("error", () => resolve(false));
    sock.once("timeout", () => { sock.destroy(); resolve(false); });
  });
}
