import { ANSI } from "./ansi.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ProgressLineOptions {
  /** Total number of units (100% = total). */
  total: number;
  /** Bar width in characters. Default: 40. */
  width?: number;
  /** Optional label prepended to the bar. */
  label?: string;
  /** Append percentage after bar. Default: true. */
  showPercent?: boolean;
  /** Append ETA estimate after percentage. Default: false. */
  showEta?: boolean;
}

// ---------------------------------------------------------------------------
// Block character helpers
// ---------------------------------------------------------------------------

// Eight sub-character widths for smooth fractional fill
const PARTIAL_CHARS = ["▏", "▎", "▍", "▌", "▋", "▊", "▉"] as const;

function getPartialChar(fraction: number): string {
  // fraction in [0, 1)
  const idx = Math.min(
    Math.floor(fraction * PARTIAL_CHARS.length),
    PARTIAL_CHARS.length - 1,
  );
  return PARTIAL_CHARS[idx] ?? "▌";
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "?";
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.ceil(seconds % 60);
  return `${mins}m${secs}s`;
}

// ---------------------------------------------------------------------------
// ProgressLine class
// ---------------------------------------------------------------------------

/**
 * Renders a progress bar string with optional label, percentage, and ETA.
 *
 * ```ts
 * const bar = new ProgressLine({ total: 100, label: "Downloading" });
 * bar.update(42);
 * process.stdout.write("\r" + bar.render());
 * ```
 */
export class ProgressLine {
  private readonly total: number;
  private readonly barWidth: number;
  private readonly label: string | undefined;
  private readonly showPercent: boolean;
  private readonly showEta: boolean;

  private current = 0;
  private readonly startTime: number;
  private done = false;

  constructor(opts: ProgressLineOptions) {
    this.total = opts.total;
    this.barWidth = opts.width ?? 40;
    this.label = opts.label;
    this.showPercent = opts.showPercent ?? true;
    this.showEta = opts.showEta ?? false;
    this.startTime = Date.now();
  }

  /** Update the current progress value (clamped to [0, total]). */
  update(current: number): void {
    this.current = Math.max(0, Math.min(current, this.total));
  }

  /** Mark the bar as complete (sets current = total). */
  complete(): void {
    this.current = this.total;
    this.done = true;
  }

  /** Render the progress bar as a string (no newline). */
  render(): string {
    const fraction = this.total > 0 ? this.current / this.total : 0;
    const filled = fraction * this.barWidth;
    const fullBlocks = Math.floor(filled);
    const remainder = filled - fullBlocks;
    const emptyBlocks = Math.max(
      0,
      this.barWidth - fullBlocks - (remainder > 0.0625 ? 1 : 0),
    );

    // Teal fill, dim empty
    let bar = ANSI.fg(0, 188, 212) + "█".repeat(fullBlocks);
    if (remainder > 0.0625 && fullBlocks < this.barWidth) {
      bar += getPartialChar(remainder);
    }
    bar +=
      ANSI.fg(64, 64, 64) + "░".repeat(emptyBlocks) + ANSI.RESET;

    const parts: string[] = [];
    if (this.label !== undefined) {
      parts.push(this.label);
    }
    parts.push("[" + bar + "]");

    if (this.showPercent) {
      const pct = Math.min(100, Math.floor(fraction * 100));
      parts.push(`${pct}%`);
    }

    if (this.showEta && !this.done && this.current > 0) {
      const elapsed = (Date.now() - this.startTime) / 1000;
      const rate = this.current / elapsed;
      const remaining = (this.total - this.current) / rate;
      parts.push(`ETA: ${formatDuration(remaining)}`);
    }

    return parts.join(" ");
  }
}
