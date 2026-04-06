import { ANSI } from "./ansi.js";
import { renderTable } from "./table.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type HealthStatus = "up" | "down" | "degraded" | "unknown";

interface HealthConfig {
  readonly icon: string;
  readonly color: string;
  readonly label: string;
}

// ---------------------------------------------------------------------------
// Configuration per status
// ---------------------------------------------------------------------------

const HEALTH_CONFIG: Record<HealthStatus, HealthConfig> = {
  up: { icon: "●", color: ANSI.fg(76, 175, 80), label: "UP" },
  down: { icon: "●", color: ANSI.fg(244, 67, 54), label: "DOWN" },
  degraded: { icon: "●", color: ANSI.fg(255, 152, 0), label: "DEGRADED" },
  unknown: { icon: "●", color: ANSI.fg(144, 164, 174), label: "UNKNOWN" },
};

// ---------------------------------------------------------------------------
// Render functions
// ---------------------------------------------------------------------------

/**
 * Render a colored status badge: `● UP`, `● DOWN`, etc.
 */
export function renderHealthBadge(status: HealthStatus): string {
  const cfg = HEALTH_CONFIG[status];
  return `${cfg.color}${cfg.icon}${ANSI.RESET} ${cfg.label}`;
}

/**
 * Render a table of service → health status entries.
 *
 * ```
 * ┌─────────────┬──────────┐
 * │ Service     │ Status   │
 * ├─────────────┼──────────┤
 * │ api         │ ● UP     │
 * │ database    │ ● DOWN   │
 * └─────────────┴──────────┘
 * ```
 */
export function renderHealthTable(statuses: Record<string, HealthStatus>): string {
  const rows: string[][] = Object.entries(statuses).map(([name, status]) => [
    name,
    renderHealthBadge(status),
  ]);

  return renderTable({
    headers: ["Service", "Status"],
    rows,
    borderStyle: "single",
  });
}
