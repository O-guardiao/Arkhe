import fs from "node:fs/promises";
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

/** Returns the systemd unit file path for `name` in the system directory. */
function systemUnitPath(name: string): string {
  return `/etc/systemd/system/${name}.service`;
}

/** Maps a restartPolicy to a systemd Restart= value. */
function mapRestartPolicy(policy: ServiceConfig["restartPolicy"]): string {
  switch (policy) {
    case "always":
      return "always";
    case "on-failure":
      return "on-failure";
    case "never":
      return "no";
  }
}

/**
 * Escapes a single value for use inside a systemd `Environment=` directive.
 * Wraps in quotes and escapes backslashes and embedded double-quotes.
 */
function escapeSystemdEnvValue(value: string): string {
  const escaped = value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return `"${escaped}"`;
}

/** Parses a human-readable `systemctl status` Memory line such as `50.3M`. */
function parseMemoryBytes(raw: string): number | undefined {
  const m = /^([\d.]+)\s*([BKMGT]?)/i.exec(raw.trim());
  if (!m) return undefined;
  const numStr = m[1];
  const unitStr = m[2];
  if (numStr === undefined) return undefined;
  const num = parseFloat(numStr);
  if (isNaN(num)) return undefined;

  const unit = (unitStr ?? "").toUpperCase();
  const multipliers: Record<string, number> = {
    B: 1,
    K: 1024,
    M: 1024 ** 2,
    G: 1024 ** 3,
    T: 1024 ** 4,
  };
  const rawMult = multipliers[unit];
  const mult = rawMult !== undefined ? rawMult : 1;
  return Math.floor(num * mult);
}

// ---------------------------------------------------------------------------
// Public pure functions
// ---------------------------------------------------------------------------

/**
 * Generates the content of a systemd `.service` unit file from a config.
 * Does NOT write the file; use `SystemdManager.install()` for that.
 */
export function generateUnitFile(cfg: ServiceConfig): string {
  const lines: string[] = [];

  lines.push("[Unit]");
  lines.push(`Description=${cfg.description}`);
  lines.push("After=network.target");
  lines.push("");

  lines.push("[Service]");
  lines.push("Type=simple");

  if (cfg.user !== undefined) {
    lines.push(`User=${cfg.user}`);
  }
  if (cfg.group !== undefined) {
    lines.push(`Group=${cfg.group}`);
  }

  lines.push(`WorkingDirectory=${cfg.workingDir}`);

  const execArgs = [cfg.scriptPath, ...cfg.args].join(" ");
  lines.push(`ExecStart=${cfg.execPath} ${execArgs}`);

  lines.push(`Restart=${mapRestartPolicy(cfg.restartPolicy)}`);
  lines.push("RestartSec=5");

  if (cfg.logFile !== undefined) {
    lines.push(`StandardOutput=append:${cfg.logFile}`);
    lines.push(`StandardError=append:${cfg.logFile}`);
  } else {
    lines.push("StandardOutput=journal");
    lines.push("StandardError=journal");
  }

  if (cfg.pidFile !== undefined) {
    lines.push(`PIDFile=${cfg.pidFile}`);
  }

  for (const [key, value] of Object.entries(cfg.env)) {
    lines.push(`Environment=${escapeSystemdEnvValue(`${key}=${value}`)}`);
  }

  lines.push("");
  lines.push("[Install]");
  lines.push("WantedBy=multi-user.target");
  lines.push("");

  return lines.join("\n");
}

/**
 * Parses the human-readable output of `systemctl status <name>` into a
 * `ServiceInfo` object.
 *
 * Handles:
 *  - `active (running)` → `running`
 *  - `inactive (dead)` / `deactivating` → `stopped`
 *  - `failed` → `failed`
 *  - "could not be found" / "No files found" → `not-installed`
 *  - anything else → `unknown`
 */
export function parseSystemctlStatus(
  output: string,
  name: string,
): ServiceInfo {
  const lower = output.toLowerCase();

  if (
    lower.includes("could not be found") ||
    lower.includes("no files found for") ||
    lower.includes("unit not found")
  ) {
    return { name, status: "not-installed" };
  }

  let status: ServiceStatus = "unknown";
  let pid: number | undefined;
  let memoryBytes: number | undefined;
  let exitCode: number | undefined;

  for (const rawLine of output.split("\n")) {
    const line = rawLine.trim();

    if (line.startsWith("Active:")) {
      const activePart = line.slice("Active:".length).trim();
      if (activePart.startsWith("active (running)")) {
        status = "running";
      } else if (
        activePart.startsWith("inactive") ||
        activePart.startsWith("deactivating")
      ) {
        status = "stopped";
      } else if (activePart.startsWith("failed")) {
        status = "failed";
      } else if (activePart.startsWith("activating")) {
        status = "unknown";
      }
      continue;
    }

    if (line.startsWith("Main PID:")) {
      const rest = line.slice("Main PID:".length).trim();
      const pidToken = rest.split(/\s/)[0];
      if (pidToken !== undefined && pidToken !== "" && pidToken !== "(") {
        const parsed = parseInt(pidToken, 10);
        if (!isNaN(parsed) && parsed > 0) {
          pid = parsed;
        }
      }
      continue;
    }

    if (line.startsWith("Memory:")) {
      const memStr = line.slice("Memory:".length).trim();
      const parsed = parseMemoryBytes(memStr);
      if (parsed !== undefined) {
        memoryBytes = parsed;
      }
      continue;
    }

    // Parse exit code from lines like:
    // Process: 1234 ExecStart=... (code=exited, status=1/FAILURE)
    if (line.startsWith("Process:") && line.includes("status=")) {
      const m = /status=(\d+)/.exec(line);
      if (m !== null) {
        const codeStr = m[1];
        if (codeStr !== undefined) {
          const parsed = parseInt(codeStr, 10);
          if (!isNaN(parsed)) {
            exitCode = parsed;
          }
        }
      }
      continue;
    }
  }

  return {
    name,
    status,
    ...(pid !== undefined ? { pid } : {}),
    ...(memoryBytes !== undefined ? { memory_bytes: memoryBytes } : {}),
    ...(exitCode !== undefined ? { exit_code: exitCode } : {}),
  };
}

// ---------------------------------------------------------------------------
// SystemdManager — implements ServiceManager
// ---------------------------------------------------------------------------

export class SystemdManager implements ServiceManager {
  async install(cfg: ServiceConfig): Promise<void> {
    const unitPath = systemUnitPath(cfg.name);
    const content = generateUnitFile(cfg);
    await fs.mkdir("/etc/systemd/system", { recursive: true });
    await fs.writeFile(unitPath, content, "utf8");
    await execFileUtf8("systemctl", ["daemon-reload"]);
    await execFileUtf8("systemctl", ["enable", cfg.name]);
  }

  async uninstall(name: string): Promise<void> {
    await execFileUtf8("systemctl", ["stop", name]).catch(() => undefined);
    await execFileUtf8("systemctl", ["disable", name]).catch(() => undefined);
    const unitPath = systemUnitPath(name);
    await fs.unlink(unitPath).catch(() => undefined);
    await execFileUtf8("systemctl", ["daemon-reload"]);
  }

  async start(name: string): Promise<void> {
    const result = await execFileUtf8("systemctl", ["start", name]);
    if (result.code !== 0) {
      throw new Error(
        `systemctl start ${name} failed (code ${result.code}): ${result.stderr}`,
      );
    }
  }

  async stop(name: string): Promise<void> {
    const result = await execFileUtf8("systemctl", ["stop", name]);
    if (result.code !== 0) {
      throw new Error(
        `systemctl stop ${name} failed (code ${result.code}): ${result.stderr}`,
      );
    }
  }

  async restart(name: string): Promise<void> {
    const result = await execFileUtf8("systemctl", ["restart", name]);
    if (result.code !== 0) {
      throw new Error(
        `systemctl restart ${name} failed (code ${result.code}): ${result.stderr}`,
      );
    }
  }

  async status(name: string): Promise<ServiceInfo> {
    const result = await execFileUtf8("systemctl", [
      "status",
      name,
      "--no-pager",
    ]);
    return parseSystemctlStatus(result.stdout + "\n" + result.stderr, name);
  }

  async logs(name: string, lines = 100): Promise<string[]> {
    const result = await execFileUtf8("journalctl", [
      "-u",
      name,
      "-n",
      String(lines),
      "--no-pager",
      "--output=short",
    ]);
    if (result.code !== 0) return [];
    return result.stdout.split("\n").filter((l) => l.length > 0);
  }
}
