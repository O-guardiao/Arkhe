import { ANSI } from "./ansi.js";
import { pad } from "./safe-text.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type NoteStyle = "info" | "success" | "warning" | "error" | "debug";

export interface NoteRenderOptions {
  /** Custom title to display in the header row. Defaults to the style name. */
  title?: string;
  /** Total outer width including borders. Default: 72. */
  width?: number;
}

// ---------------------------------------------------------------------------
// Style configuration
// ---------------------------------------------------------------------------

interface NoteConfig {
  readonly icon: string;
  readonly color: string;
}

const NOTE_CONFIG: Record<NoteStyle, NoteConfig> = {
  info: { icon: "ℹ", color: ANSI.fg(33, 150, 243) },
  success: { icon: "✓", color: ANSI.fg(76, 175, 80) },
  warning: { icon: "⚠", color: ANSI.fg(255, 152, 0) },
  error: { icon: "✗", color: ANSI.fg(244, 67, 54) },
  debug: { icon: "◎", color: ANSI.fg(144, 164, 174) },
};

// ---------------------------------------------------------------------------
// renderNote
// ---------------------------------------------------------------------------

/**
 * Render a styled note box with an optional title and multi-line message.
 *
 * ```
 * ┌──────────────────────────────┐
 * │ ℹ Info                       │
 * ├──────────────────────────────┤
 * │ Your message goes here.      │
 * └──────────────────────────────┘
 * ```
 */
export function renderNote(
  style: NoteStyle,
  message: string,
  opts: NoteRenderOptions = {},
): string {
  const cfg = NOTE_CONFIG[style];
  const outerWidth = opts.width ?? 72;
  // Inner width = outer − 2 (borders) − 2 (padding spaces)
  const innerWidth = Math.max(4, outerWidth - 4);

  const h = cfg.color;
  const reset = ANSI.RESET;
  const lines: string[] = [];

  // Top border
  lines.push(h + "┌" + "─".repeat(outerWidth - 2) + "┐" + reset);

  // Title line (bold icon + title text)
  const titleText =
    opts.title !== undefined
      ? `${cfg.icon} ${opts.title}`
      : `${cfg.icon} ${style.charAt(0).toUpperCase() + style.slice(1)}`;
  const paddedTitle = pad(titleText, innerWidth, "left");
  lines.push(h + "│ " + ANSI.BOLD + paddedTitle + reset + h + " │" + reset);

  // Separator
  lines.push(h + "├" + "─".repeat(outerWidth - 2) + "┤" + reset);

  // Message body — each source line rendered separately
  for (const msgLine of message.split("\n")) {
    const paddedMsg = pad(msgLine, innerWidth, "left");
    lines.push(h + "│ " + reset + paddedMsg + h + " │" + reset);
  }

  // Bottom border
  lines.push(h + "└" + "─".repeat(outerWidth - 2) + "┘" + reset);

  return lines.join("\n");
}
