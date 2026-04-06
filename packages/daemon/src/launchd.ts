import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFileUtf8 } from "./exec-file.js";
import type {
  ServiceConfig,
  ServiceInfo,
  ServiceManager,
  ServiceStatus,
} from "./types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Resolves the LaunchAgent plist path for a given service label. */
function plistPath(name: string): string {
  return path.join(os.homedir(), "Library", "LaunchAgents", `${name}.plist`);
}

/** Escapes a string for inclusion in an XML text node. */
function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Public pure functions
// ---------------------------------------------------------------------------

/**
 * Generates a macOS LaunchAgent plist XML string from a `ServiceConfig`.
 */
export function generatePlist(cfg: ServiceConfig): string {
  const label = cfg.name;
  const programArgs = [cfg.execPath, cfg.scriptPath, ...cfg.args]
    .map((a) => `    <string>${escapeXml(a)}</string>`)
    .join("\n");

  const envLines = Object.entries(cfg.env)
    .map(
      ([k, v]) =>
        `    <key>${escapeXml(k)}</key>\n    <string>${escapeXml(v)}</string>`,
    )
    .join("\n");

  const keepAlive =
    cfg.restartPolicy === "never" ? "<false/>" : "<true/>";

  const lines: string[] = [
    `<?xml version="1.0" encoding="UTF-8"?>`,
    `<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">`,
    `<plist version="1.0">`,
    `<dict>`,
    `  <key>Label</key>`,
    `  <string>${escapeXml(label)}</string>`,
    `  <key>ProgramArguments</key>`,
    `  <array>`,
    programArgs,
    `  </array>`,
  ];

  if (cfg.workingDir !== "") {
    lines.push(`  <key>WorkingDirectory</key>`);
    lines.push(`  <string>${escapeXml(cfg.workingDir)}</string>`);
  }

  if (Object.keys(cfg.env).length > 0) {
    lines.push(`  <key>EnvironmentVariables</key>`);
    lines.push(`  <dict>`);
    lines.push(envLines);
    lines.push(`  </dict>`);
  }

  lines.push(`  <key>RunAtLoad</key>`);
  lines.push(`  <true/>`);
  lines.push(`  <key>KeepAlive</key>`);
  lines.push(`  ${keepAlive}`);

  if (cfg.logFile !== undefined) {
    lines.push(`  <key>StandardOutPath</key>`);
    lines.push(`  <string>${escapeXml(cfg.logFile)}</string>`);
    lines.push(`  <key>StandardErrorPath</key>`);
    lines.push(`  <string>${escapeXml(cfg.logFile)}</string>`);
  }

  lines.push(`</dict>`);
  lines.push(`</plist>`);
  lines.push("");

  return lines.join("\n");
}

/**
 * Parses the output of `launchctl list` to find a service by label and
 * returns its `ServiceInfo`.
 *
 * `launchctl list` emits tab-separated lines:
 * ```
 * PID\tStatus\tLabel
 * 1234\t0\tcom.arkhe.rlm-brain
 * -\t0\tcom.other.agent
 * ```
 */
export function parseLaunchctlList(output: string, name: string): ServiceInfo {
  for (const rawLine of output.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("PID")) continue; // skip empty/header

    const parts = line.split(/\t/);
    const pidPart = parts[0];
    const labelPart = parts[2];

    if (labelPart === undefined || labelPart.trim() !== name) continue;

    if (pidPart !== undefined && pidPart.trim() !== "-") {
      const pid = parseInt(pidPart.trim(), 10);
      if (!isNaN(pid) && pid > 0) {
        return { name, status: "running", pid };
      }
    }
    return { name, status: "stopped" };
  }

  return { name, status: "not-installed" };
}

// ---------------------------------------------------------------------------
// LaunchdManager — implements ServiceManager
// ---------------------------------------------------------------------------

export class LaunchdManager implements ServiceManager {
  async install(cfg: ServiceConfig): Promise<void> {
    const dest = plistPath(cfg.name);
    await fs.mkdir(path.dirname(dest), { recursive: true });
    await fs.writeFile(dest, generatePlist(cfg), "utf8");
    await execFileUtf8("launchctl", ["load", dest]);
  }

  async uninstall(name: string): Promise<void> {
    const dest = plistPath(name);
    await execFileUtf8("launchctl", ["unload", dest]).catch(() => undefined);
    await fs.unlink(dest).catch(() => undefined);
  }

  async start(name: string): Promise<void> {
    const result = await execFileUtf8("launchctl", ["start", name]);
    if (result.code !== 0) {
      throw new Error(
        `launchctl start ${name} failed (code ${result.code}): ${result.stderr}`,
      );
    }
  }

  async stop(name: string): Promise<void> {
    const result = await execFileUtf8("launchctl", ["stop", name]);
    if (result.code !== 0) {
      throw new Error(
        `launchctl stop ${name} failed (code ${result.code}): ${result.stderr}`,
      );
    }
  }

  async restart(name: string): Promise<void> {
    await this.stop(name).catch(() => undefined);
    await this.start(name);
  }

  async status(name: string): Promise<ServiceInfo> {
    const result = await execFileUtf8("launchctl", ["list"]);
    if (result.code !== 0) {
      return { name, status: "unknown" };
    }
    return parseLaunchctlList(result.stdout, name);
  }

  async logs(name: string, lines = 100): Promise<string[]> {
    // Use `log show` on macOS (available since 10.12)
    const result = await execFileUtf8("log", [
      "show",
      "--predicate",
      `process == "${name}"`,
      "--last",
      "1h",
    ]);
    if (result.code !== 0) return [];
    const all = result.stdout.split("\n").filter((l) => l.length > 0);
    return all.slice(-lines);
  }
}

/** Map `ServiceStatus` to a launchd status string for tests. */
export function statusFromLaunchdCode(code: number): ServiceStatus {
  if (code === 0) return "stopped";
  return "failed";
}
