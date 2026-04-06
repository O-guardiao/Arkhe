import fs from "node:fs/promises";
import { detectPlatform } from "./detect.js";
import { execFileUtf8 } from "./exec-file.js";
import { getRlmConfigDir } from "./paths.js";
import type { ServicePlatform } from "./types.js";

export type DiagnosticReport = {
  /** Detected service management platform. */
  platform: ServicePlatform;
  /** Node.js version string, e.g. `v20.9.0`. */
  node_version: string;
  /** RLM daemon package version. */
  rlm_version: string;
  /** Whether the RLM config directory exists on disk. */
  config_path_exists: boolean;
  /** Resolved path to the RLM config directory. */
  log_path: string;
  /** Availability of platform-specific service management commands. */
  available_commands: {
    systemctl: boolean;
    launchctl: boolean;
    schtasks: boolean;
  };
};

/**
 * Checks whether a CLI command responds to `--version` without error.
 * Returns `false` on any spawn/exec failure (command not found, permission
 * denied, etc.).
 */
async function commandExists(command: string): Promise<boolean> {
  const result = await execFileUtf8(command, ["--version"]).catch(
    () => ({ code: 1 } as const),
  );
  return result.code === 0;
}

/**
 * Collects diagnostic information about the current RLM daemon environment.
 * Safe to call on any platform — never throws.
 */
export async function runDiagnostics(): Promise<DiagnosticReport> {
  const platform = detectPlatform();
  const configDir = getRlmConfigDir();

  let configPathExists = false;
  try {
    await fs.access(configDir);
    configPathExists = true;
  } catch {
    configPathExists = false;
  }

  const [systemctlExists, launchctlExists, schtasksExists] = await Promise.all(
    [
      commandExists("systemctl"),
      commandExists("launchctl"),
      commandExists("schtasks"),
    ],
  );

  return {
    platform,
    node_version: process.version,
    rlm_version: "0.1.0",
    config_path_exists: configPathExists,
    log_path: configDir,
    available_commands: {
      systemctl: systemctlExists,
      launchctl: launchctlExists,
      schtasks: schtasksExists,
    },
  };
}
