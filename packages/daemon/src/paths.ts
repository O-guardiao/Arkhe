import os from "node:os";
import path from "node:path";
import type { ServicePlatform } from "./types.js";

/**
 * Returns the canonical path for the service definition file for a given
 * platform (unit file, plist, or XML task).
 */
export function getServiceFilePath(
  name: string,
  platform: ServicePlatform,
): string {
  switch (platform) {
    case "systemd":
      return `/etc/systemd/system/${name}.service`;
    case "launchd":
      return path.join(
        os.homedir(),
        "Library",
        "LaunchAgents",
        `${name}.plist`,
      );
    case "schtasks":
      return path.join(os.tmpdir(), `${name}.xml`);
    case "unknown":
      return path.join(os.tmpdir(), name);
  }
}

/** Platform-specific directory for service log files. */
export function getLogDir(): string {
  switch (process.platform) {
    case "linux":
      return "/var/log/rlm";
    case "darwin":
      return path.join(os.homedir(), "Library", "Logs", "rlm");
    case "win32": {
      const localAppData = process.env["LOCALAPPDATA"];
      const base = localAppData !== undefined ? localAppData : os.homedir();
      return path.join(base, "rlm", "logs");
    }
    default:
      return path.join(os.homedir(), ".rlm", "logs");
  }
}

/** Platform-specific directory for PID files. */
export function getPidDir(): string {
  switch (process.platform) {
    case "linux":
      return "/run/rlm";
    case "darwin":
      return path.join(
        os.homedir(),
        "Library",
        "Application Support",
        "rlm",
        "run",
      );
    case "win32": {
      const localAppData = process.env["LOCALAPPDATA"];
      const base = localAppData !== undefined ? localAppData : os.homedir();
      return path.join(base, "rlm", "run");
    }
    default:
      return path.join(os.homedir(), ".rlm", "run");
  }
}

/**
 * Platform-specific RLM configuration directory.
 * - Linux:   `~/.config/rlm`
 * - macOS:   `~/Library/Application Support/rlm`
 * - Windows: `%APPDATA%\rlm`
 */
export function getRlmConfigDir(): string {
  switch (process.platform) {
    case "linux":
      return path.join(os.homedir(), ".config", "rlm");
    case "darwin":
      return path.join(os.homedir(), "Library", "Application Support", "rlm");
    case "win32": {
      const appData = process.env["APPDATA"];
      const base = appData !== undefined ? appData : os.homedir();
      return path.join(base, "rlm");
    }
    default:
      return path.join(os.homedir(), ".config", "rlm");
  }
}
