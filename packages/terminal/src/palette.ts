import { ANSI } from "./ansi.js";

// ---------------------------------------------------------------------------
// Gradient
// ---------------------------------------------------------------------------

/**
 * Return an array of `steps` ANSI truecolor foreground escape strings
 * interpolated linearly from `startRgb` to `endRgb`.
 */
export function gradient(
  startRgb: [number, number, number],
  endRgb: [number, number, number],
  steps: number,
): string[] {
  if (steps <= 0) return [];
  if (steps === 1) return [ANSI.fg(startRgb[0], startRgb[1], startRgb[2])];

  const result: string[] = [];
  for (let i = 0; i < steps; i++) {
    const t = i / (steps - 1);
    const r = Math.round(startRgb[0] + (endRgb[0] - startRgb[0]) * t);
    const g = Math.round(startRgb[1] + (endRgb[1] - startRgb[1]) * t);
    const b = Math.round(startRgb[2] + (endRgb[2] - startRgb[2]) * t);
    result.push(ANSI.fg(r, g, b));
  }
  return result;
}

// ---------------------------------------------------------------------------
// RGB ↔ xterm-256 conversion
// ---------------------------------------------------------------------------

/**
 * Convert an RGB triple to the nearest index in the xterm 256-color palette.
 *
 * - Indices 232–255 are the grayscale ramp.
 * - Indices 16–231 are the 6×6×6 color cube.
 */
export function rgbToAnsi256(r: number, g: number, b: number): number {
  // Grayscale ramp
  if (r === g && g === b) {
    if (r < 8) return 16;
    if (r > 248) return 231;
    return Math.round(((r - 8) / 247) * 24) + 232;
  }
  // 6×6×6 color cube
  const ri = Math.round((r / 255) * 5);
  const gi = Math.round((g / 255) * 5);
  const bi = Math.round((b / 255) * 5);
  return 16 + 36 * ri + 6 * gi + bi;
}

/**
 * Parse a CSS hex color (`#rrggbb` or `#rgb`) into an `[r, g, b]` triple.
 * Throws on invalid input.
 */
export function hexToRgb(hex: string): [number, number, number] {
  const cleaned = hex.startsWith("#") ? hex.slice(1) : hex;
  if (cleaned.length === 3) {
    const r = parseInt((cleaned[0] ?? "0") + (cleaned[0] ?? "0"), 16);
    const g = parseInt((cleaned[1] ?? "0") + (cleaned[1] ?? "0"), 16);
    const b = parseInt((cleaned[2] ?? "0") + (cleaned[2] ?? "0"), 16);
    return [r, g, b];
  }
  if (cleaned.length === 6) {
    const r = parseInt(cleaned.slice(0, 2), 16);
    const g = parseInt(cleaned.slice(2, 4), 16);
    const b = parseInt(cleaned.slice(4, 6), 16);
    return [r, g, b];
  }
  throw new Error(`Invalid hex color: "${hex}"`);
}

// ---------------------------------------------------------------------------
// Named color lookup
// ---------------------------------------------------------------------------

const NAMED_COLORS: Record<string, string> = {
  black: ANSI.fg(0, 0, 0),
  red: ANSI.red,
  green: ANSI.green,
  yellow: ANSI.yellow,
  blue: ANSI.blue,
  magenta: ANSI.magenta,
  cyan: ANSI.cyan,
  white: ANSI.white,
  gray: ANSI.gray,
  grey: ANSI.gray,
  orange: ANSI.fg(255, 165, 0),
  pink: ANSI.fg(255, 105, 180),
  purple: ANSI.fg(128, 0, 128),
  teal: ANSI.fg(0, 128, 128),
  lime: ANSI.fg(0, 255, 0),
  indigo: ANSI.fg(75, 0, 130),
  violet: ANSI.fg(238, 130, 238),
  brown: ANSI.fg(139, 69, 19),
  gold: ANSI.fg(255, 215, 0),
  silver: ANSI.fg(192, 192, 192),
};

/**
 * Return the ANSI foreground escape string for a named color.
 * Throws if the name is not recognized.
 */
export function namedColor(name: string): string {
  const lower = name.toLowerCase();
  const color = NAMED_COLORS[lower];
  if (color === undefined) {
    throw new Error(`Unknown color name: "${name}"`);
  }
  return color;
}
