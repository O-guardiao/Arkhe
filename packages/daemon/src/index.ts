// Types
export type {
  ServicePlatform,
  ServiceStatus,
  ServiceConfig,
  ServiceInfo,
  ServiceManager,
} from "./types.js";

// exec helper
export { execFileUtf8 } from "./exec-file.js";
export type { ExecResult } from "./exec-file.js";

// Paths
export {
  getServiceFilePath,
  getLogDir,
  getPidDir,
  getRlmConfigDir,
} from "./paths.js";

// Platform detection
export {
  detectPlatform,
  getPlatformManager,
  isRoot,
  createServiceManager,
} from "./detect.js";

// Platform managers + pure helpers
export {
  SystemdManager,
  generateUnitFile,
  parseSystemctlStatus,
} from "./systemd.js";

export {
  LaunchdManager,
  generatePlist,
  parseLaunchctlList,
} from "./launchd.js";

export {
  SchtasksManager,
  generateXmlTask,
  parseSchtasksQuery,
} from "./schtasks.js";

// Audit
export { ServiceAudit } from "./audit.js";
export type { ServiceAuditEntry } from "./audit.js";

// Diagnostics
export { runDiagnostics } from "./diagnostics.js";
export type { DiagnosticReport } from "./diagnostics.js";
