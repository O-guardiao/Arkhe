// ANSI escape code constants, cursor control, and color helpers.
// All sequences are written manually — no chalk dependency.

const CSI = "\x1b[";

// ---------------------------------------------------------------------------
// ANSI namespace — SGR attributes, cursor movement, colors
// ---------------------------------------------------------------------------

export namespace ANSI {
  // SGR text attributes
  export const RESET = `${CSI}0m`;
  export const BOLD = `${CSI}1m`;
  export const DIM = `${CSI}2m`;
  export const ITALIC = `${CSI}3m`;
  export const UNDERLINE = `${CSI}4m`;

  // Erase
  export const CLEAR_LINE = `${CSI}2K`;
  export const CLEAR_SCREEN = `${CSI}2J`;

  // Cursor visibility
  export const HIDE_CURSOR = `${CSI}?25l`;
  export const SHOW_CURSOR = `${CSI}?25h`;

  // Cursor movement
  export function UP(n: number): string {
    return `${CSI}${n}A`;
  }
  export function DOWN(n: number): string {
    return `${CSI}${n}B`;
  }
  export function COLUMN(n: number): string {
    return `${CSI}${n}G`;
  }

  // 24-bit (truecolor) foreground: ESC[38;2;r;g;bm
  export function fg(r: number, g: number, b: number): string {
    return `${CSI}38;2;${r};${g};${b}m`;
  }

  // 24-bit (truecolor) background: ESC[48;2;r;g;bm
  export function bg(r: number, g: number, b: number): string {
    return `${CSI}48;2;${r};${g};${b}m`;
  }

  // 256-color foreground: ESC[38;5;codem
  export function fgCode(code: number): string {
    return `${CSI}38;5;${code}m`;
  }

  // 256-color background: ESC[48;5;codem
  export function bgCode(code: number): string {
    return `${CSI}48;5;${code}m`;
  }

  // Named colors — standard ANSI 16-color palette
  export const red = `${CSI}31m`;
  export const green = `${CSI}32m`;
  export const yellow = `${CSI}33m`;
  export const blue = `${CSI}34m`;
  export const magenta = `${CSI}35m`;
  export const cyan = `${CSI}36m`;
  export const white = `${CSI}37m`;
  export const gray = `${CSI}90m`;
}

// ---------------------------------------------------------------------------
// Strip and measure
// ---------------------------------------------------------------------------

// Matches SGR escape sequences: ESC [ ... m
const ANSI_SGR_RE = /\x1b\[[0-9;]*m/g;
// Matches OSC-8 hyperlinks in both open and close forms
const OSC8_RE = /\x1b\]8;;.*?\x1b\\|\x1b\]8;;\x1b\\/g;

/** Remove all ANSI SGR codes and OSC-8 hyperlinks from a string. */
export function strip(text: string): string {
  return text.replace(OSC8_RE, "").replace(ANSI_SGR_RE, "");
}

/** Number of visible characters after stripping ANSI escape codes. */
export function visibleLength(text: string): number {
  return Array.from(strip(text)).length;
}
