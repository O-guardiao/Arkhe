import { ANSI } from "./ansi.js";

// ---------------------------------------------------------------------------
// Theme interface
// ---------------------------------------------------------------------------

export interface Theme {
  /** Primary brand color (teal/cyan in RLM). */
  primary: string;
  /** Secondary brand color. */
  secondary: string;
  /** Success / OK color. */
  success: string;
  /** Warning color. */
  warning: string;
  /** Error color. */
  error: string;
  /** Informational color. */
  info: string;
  /** Muted / dimmed color for secondary text. */
  muted: string;
  /** Heading color (bold + primary). */
  heading: string;
  /** Code / monospace color. */
  code: string;
  /** Link color (underline + primary). */
  link: string;
}

// ---------------------------------------------------------------------------
// Built-in themes — RLM Arkhe branding (teal/cyan primary)
// ---------------------------------------------------------------------------

export const DEFAULT_THEME: Theme = {
  primary: ANSI.fg(0, 188, 212), //   #00BCD4
  secondary: ANSI.fg(38, 166, 154), // #26A69A
  success: ANSI.fg(76, 175, 80), //   #4CAF50
  warning: ANSI.fg(255, 152, 0), //   #FF9800
  error: ANSI.fg(244, 67, 54), //     #F44336
  info: ANSI.fg(33, 150, 243), //     #2196F3
  muted: ANSI.fg(144, 164, 174), //   #90A4AE
  heading: ANSI.BOLD + ANSI.fg(0, 188, 212),
  code: ANSI.fg(165, 214, 167), //    #A5D6A7
  link: ANSI.UNDERLINE + ANSI.fg(0, 188, 212),
};

export const DARK_THEME: Theme = {
  primary: ANSI.fg(0, 229, 255), //     #00E5FF
  secondary: ANSI.fg(100, 255, 218), // #64FFDA
  success: ANSI.fg(105, 240, 174), //   #69F0AE
  warning: ANSI.fg(255, 213, 79), //    #FFD54F
  error: ANSI.fg(255, 82, 82), //       #FF5252
  info: ANSI.fg(68, 138, 255), //       #448AFF
  muted: ANSI.fg(96, 125, 139), //      #607D8B
  heading: ANSI.BOLD + ANSI.fg(0, 229, 255),
  code: ANSI.fg(178, 255, 89), //       #B2FF59
  link: ANSI.UNDERLINE + ANSI.fg(0, 229, 255),
};

export const LIGHT_THEME: Theme = {
  primary: ANSI.fg(0, 96, 100), //    #006064
  secondary: ANSI.fg(0, 77, 64), //  #004D40
  success: ANSI.fg(27, 94, 32), //   #1B5E20
  warning: ANSI.fg(230, 81, 0), //   #E65100
  error: ANSI.fg(183, 28, 28), //    #B71C1C
  info: ANSI.fg(13, 71, 161), //     #0D47A1
  muted: ANSI.fg(69, 90, 100), //    #455A64
  heading: ANSI.BOLD + ANSI.fg(0, 96, 100),
  code: ANSI.fg(51, 105, 30), //     #33691E
  link: ANSI.UNDERLINE + ANSI.fg(0, 96, 100),
};

// ---------------------------------------------------------------------------
// Module-level theme management
// ---------------------------------------------------------------------------

let _currentTheme: Theme = DEFAULT_THEME;

/** Replace the active theme for the current process. */
export function applyTheme(theme: Theme): void {
  _currentTheme = theme;
}

/** Return the currently active theme. */
export function getTheme(): Theme {
  return _currentTheme;
}
