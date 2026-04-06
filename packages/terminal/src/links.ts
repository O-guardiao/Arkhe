// OSC 8 hyperlink support — https://gist.github.com/egmontkob/eb114294efbcd5adb1944c9f3cb5feda

const OSC8_OPEN = "\x1b]8;;";
const ST = "\x1b\\";

/**
 * Wrap `text` in an OSC 8 hyperlink pointing to `url`.
 *
 * Terminals that do not support OSC 8 will display `text` verbatim.
 */
export function hyperlink(url: string, text: string): string {
  return `${OSC8_OPEN}${url}${ST}${text}${OSC8_OPEN}${ST}`;
}

/**
 * Return `true` when the current terminal is known to support OSC 8 hyperlinks.
 *
 * Detection is based on `$TERM_PROGRAM`, `$TERM`, and `$COLORTERM`.
 * This is a best-effort heuristic — false negatives are possible for
 * less common terminals.
 */
export function isHyperlinkSupported(): boolean {
  const termProgram = process.env["TERM_PROGRAM"] ?? "";
  const term = process.env["TERM"] ?? "";
  const colorTerm = process.env["COLORTERM"] ?? "";
  const wtSession = process.env["WT_SESSION"] ?? "";

  // Explicitly known supporting terminals
  const knownSupported = new Set([
    "iTerm.app",
    "WezTerm",
    "Hyper",
    "vscode",
    "Tabby",
    "alacritty",
    "Ghostty",
  ]);

  if (knownSupported.has(termProgram)) return true;

  // Windows Terminal
  if (wtSession.length > 0) return true;

  // Truecolor xterm-compatible terminals
  if (
    colorTerm === "truecolor" &&
    (term.startsWith("xterm") || term.startsWith("screen"))
  ) {
    return true;
  }

  return false;
}
