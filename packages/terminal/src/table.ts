import { ANSI, strip, visibleLength } from "./ansi.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TableOptions {
  /** Column header labels. */
  headers: string[];
  /** Data rows — each row must have the same number of cells as headers. */
  rows: string[][];
  /** Maximum visible width of the entire table (auto-shrinks columns). */
  maxWidth?: number;
  /** Border drawing style. Default: `'single'`. */
  borderStyle?: "none" | "single" | "double";
}

// ---------------------------------------------------------------------------
// Border character sets
// ---------------------------------------------------------------------------

interface BorderChars {
  topLeft: string;
  topRight: string;
  bottomLeft: string;
  bottomRight: string;
  horizontal: string;
  vertical: string;
  cross: string;
  topT: string;
  bottomT: string;
  leftT: string;
  rightT: string;
}

const BORDER_CHARS: Record<"none" | "single" | "double", BorderChars> = {
  none: {
    topLeft: "",
    topRight: "",
    bottomLeft: "",
    bottomRight: "",
    horizontal: "",
    vertical: " ",
    cross: " ",
    topT: "",
    bottomT: "",
    leftT: "",
    rightT: "",
  },
  single: {
    topLeft: "┌",
    topRight: "┐",
    bottomLeft: "└",
    bottomRight: "┘",
    horizontal: "─",
    vertical: "│",
    cross: "┼",
    topT: "┬",
    bottomT: "┴",
    leftT: "├",
    rightT: "┤",
  },
  double: {
    topLeft: "╔",
    topRight: "╗",
    bottomLeft: "╚",
    bottomRight: "╝",
    horizontal: "═",
    vertical: "║",
    cross: "╬",
    topT: "╦",
    bottomT: "╩",
    leftT: "╠",
    rightT: "╣",
  },
};

// ---------------------------------------------------------------------------
// Cell helpers
// ---------------------------------------------------------------------------

function truncateCell(text: string, maxLen: number): string {
  if (maxLen <= 0) return "";
  const visible = visibleLength(text);
  if (visible <= maxLen) return text;
  if (maxLen === 1) return "…";
  const chars = Array.from(strip(text));
  return chars.slice(0, maxLen - 1).join("") + "…";
}

function padCell(text: string, width: number): string {
  const visible = visibleLength(text);
  const extra = Math.max(0, width - visible);
  return text + " ".repeat(extra);
}

// ---------------------------------------------------------------------------
// Column width computation
// ---------------------------------------------------------------------------

function computeColumnWidths(
  headers: string[],
  rows: string[][],
  maxWidth?: number,
): number[] {
  const colCount = headers.length;
  const widths: number[] = headers.map((h) => visibleLength(h));

  for (const row of rows) {
    for (let i = 0; i < colCount; i++) {
      const cell = row[i] ?? "";
      widths[i] = Math.max(widths[i] ?? 0, visibleLength(cell));
    }
  }

  if (maxWidth !== undefined) {
    // Overhead = │ prefix (2) + (colCount-1) × separator (3) + suffix (2) = 3*colCount+1
    const totalFixed = 3 * colCount + 1;
    const available = maxWidth - totalFixed;
    if (available > 0) {
      const total = widths.reduce((a, b) => a + b, 0);
      if (total > available) {
        const ratio = available / total;
        for (let i = 0; i < widths.length; i++) {
          widths[i] = Math.max(1, Math.floor((widths[i] ?? 1) * ratio));
        }
      }
    }
  }

  return widths;
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function renderHLine(
  widths: number[],
  left: string,
  middle: string,
  right: string,
  fill: string,
): string {
  const segments = widths.map((w) => fill.repeat(w + 2));
  return left + segments.join(middle) + right;
}

/**
 * Render a single data row with border pipes.
 * Each cell is truncated and padded to match `widths[i]`.
 */
export function renderRow(cells: string[], widths: number[]): string {
  const padded = cells.map((cell, i) => {
    const w = widths[i] ?? visibleLength(cell);
    return padCell(truncateCell(cell, w), w);
  });
  return "│ " + padded.join(" │ ") + " │";
}

// ---------------------------------------------------------------------------
// Main render
// ---------------------------------------------------------------------------

/**
 * Render a full table as a string with optional borders.
 * Headers are rendered in bold.
 */
export function renderTable(opts: TableOptions): string {
  const style = opts.borderStyle ?? "single";
  const b = BORDER_CHARS[style];
  const widths = computeColumnWidths(opts.headers, opts.rows, opts.maxWidth);
  const lines: string[] = [];

  // Top border
  if (style !== "none") {
    lines.push(renderHLine(widths, b.topLeft, b.topT, b.topRight, b.horizontal));
  }

  // Header row — bold + reset per cell
  const headerCells = opts.headers.map((h, i) => {
    const w = widths[i] ?? visibleLength(h);
    const truncated = truncateCell(h, w);
    const visible = visibleLength(truncated);
    const padWidth = Math.max(0, w - visible);
    return ANSI.BOLD + truncated + ANSI.RESET + " ".repeat(padWidth);
  });
  if (style === "none") {
    lines.push(headerCells.join("  "));
  } else {
    lines.push("│ " + headerCells.join(" │ ") + " │");
    lines.push(renderHLine(widths, b.leftT, b.cross, b.rightT, b.horizontal));
  }

  // Data rows
  for (const row of opts.rows) {
    const cells = opts.headers.map((_, i) => row[i] ?? "");
    if (style === "none") {
      lines.push(cells.map((c, i) => padCell(c, widths[i] ?? visibleLength(c))).join("  "));
    } else {
      lines.push(renderRow(cells, widths));
    }
  }

  // Bottom border
  if (style !== "none") {
    lines.push(
      renderHLine(widths, b.bottomLeft, b.bottomT, b.bottomRight, b.horizontal),
    );
  }

  return lines.join("\n");
}
