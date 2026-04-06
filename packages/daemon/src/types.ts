/** Platform that manages the system service. */
export type ServicePlatform = "systemd" | "launchd" | "schtasks" | "unknown";

/** Lifecycle state of an installed service. */
export type ServiceStatus =
  | "running"
  | "stopped"
  | "failed"
  | "unknown"
  | "not-installed";

/** Full configuration required to install a service. */
export type ServiceConfig = {
  /** Short identifier used as the service/unit name, e.g. `rlm-brain`. */
  name: string;
  /** Human-readable display name. */
  displayName: string;
  /** One-line description shown in service managers. */
  description: string;
  /** Absolute path to the Node.js (or other) executable. */
  execPath: string;
  /** Absolute path to the entry-point script. */
  scriptPath: string;
  /** Additional arguments passed after scriptPath. */
  args: string[];
  /** Environment variables injected into the process. */
  env: Record<string, string>;
  /** Working directory for the service process. */
  workingDir: string;
  /** Unix user to run the service as (optional, systemd/launchd). */
  user?: string;
  /** Unix group to run the service as (optional, systemd). */
  group?: string;
  /** Restart strategy. */
  restartPolicy: "always" | "on-failure" | "never";
  /** Absolute path to a log file (optional). */
  logFile?: string;
  /** Absolute path to a PID file (optional). */
  pidFile?: string;
};

/** Runtime information about an installed service. */
export type ServiceInfo = {
  name: string;
  status: ServiceStatus;
  pid?: number;
  uptime_seconds?: number;
  memory_bytes?: number;
  exit_code?: number;
};

/** Cross-platform service lifecycle manager. */
export interface ServiceManager {
  install(cfg: ServiceConfig): Promise<void>;
  uninstall(name: string): Promise<void>;
  start(name: string): Promise<void>;
  stop(name: string): Promise<void>;
  restart(name: string): Promise<void>;
  status(name: string): Promise<ServiceInfo>;
  logs(name: string, lines?: number): Promise<string[]>;
}
