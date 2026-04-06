import { execFileSync } from "node:child_process";
import { LaunchdManager } from "./launchd.js";
import { SchtasksManager } from "./schtasks.js";
import { SystemdManager } from "./systemd.js";
import type { ServiceManager, ServicePlatform } from "./types.js";

/** Detects the service management platform available on the current system. */
export function detectPlatform(): ServicePlatform {
  if (process.platform === "darwin") {
    return "launchd";
  }

  if (process.platform === "win32") {
    return "schtasks";
  }

  if (process.platform === "linux") {
    try {
      execFileSync("systemctl", ["--version"], { stdio: "ignore", shell: false });
      return "systemd";
    } catch {
      // systemctl not available on this Linux installation
    }
  }

  return "unknown";
}

/**
 * Returns true if the current process has elevated privileges
 * (root on Unix, local administrator on Windows).
 */
export function isRoot(): boolean {
  if (process.platform === "win32") {
    try {
      execFileSync("net", ["session"], { stdio: "ignore", shell: false });
      return true;
    } catch {
      return false;
    }
  }

  if ("getuid" in process && typeof process.getuid === "function") {
    return process.getuid() === 0;
  }

  return false;
}

/** Throws on all service operations — used when no known platform is detected. */
class UnknownPlatformManager implements ServiceManager {
  readonly #msg = "Service management is not supported on this platform";

  async install(): Promise<void> {
    throw new Error(this.#msg);
  }
  async uninstall(): Promise<void> {
    throw new Error(this.#msg);
  }
  async start(): Promise<void> {
    throw new Error(this.#msg);
  }
  async stop(): Promise<void> {
    throw new Error(this.#msg);
  }
  async restart(): Promise<void> {
    throw new Error(this.#msg);
  }
  async status(): Promise<never> {
    throw new Error(this.#msg);
  }
  async logs(): Promise<never> {
    throw new Error(this.#msg);
  }
}

/** Returns an appropriate `ServiceManager` for the current platform. */
export function getPlatformManager(): ServiceManager {
  const platform = detectPlatform();
  switch (platform) {
    case "systemd":
      return new SystemdManager();
    case "launchd":
      return new LaunchdManager();
    case "schtasks":
      return new SchtasksManager();
    case "unknown":
      return new UnknownPlatformManager();
  }
}

/**
 * Convenience factory: detects the current platform and returns the
 * appropriate `ServiceManager`. Equivalent to `getPlatformManager()`.
 */
export function createServiceManager(): ServiceManager {
  return getPlatformManager();
}
