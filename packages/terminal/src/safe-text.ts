import { strip, visibleLength } from "./ansi.js";

// ---------------------------------------------------------------------------
// Sanitize
// ---------------------------------------------------------------------------

/**
 * Remove ANSI escape codes and C0/C1 control characters from `text`,
 * preserving `\n` and `\t` as they are intentional whitespace.
 */
export function sanitizeForTerminal(text: string): string {
  const stripped = strip(text);
  let result = "";
  for (const char of stripped) {
    const code = char.codePointAt(0) ?? 0;
    if (char === "\n" || char === "\t") {
      result += char;
      continue;
    }
    // Drop C0 (0x00–0x1F) and C1 (0x7F–0x9F) control characters
    if ((code >= 0x00 && code <= 0x1f) || (code >= 0x7f && code <= 0x9f)) {
      continue;
    }
    result += char;
  }
  return result;
}

// ---------------------------------------------------------------------------
// Truncate
// ---------------------------------------------------------------------------

/**
 * Truncate `text` to at most `maxLength` visible characters, appending
 * `suffix` (default `"…"`) when truncation occurs.
 *
 * Works correctly with Unicode code points (no mid-surrogate splits).
 */
export function truncate(text: string, maxLength: number, suffix = "…"): string {
  if (maxLength <= 0) {
    return suffix.length > 0 ? suffix : "";
  }
  const vis = visibleLength(text);
  if (vis <= maxLength) return text;

  const suffixChars = Array.from(suffix);
  const target = maxLength - suffixChars.length;
  if (target <= 0) {
    return suffixChars.slice(0, maxLength).join("");
  }
  const chars = Array.from(strip(text));
  return chars.slice(0, target).join("") + suffix;
}

// ---------------------------------------------------------------------------
// Word wrap
// ---------------------------------------------------------------------------

/**
 * Wrap `text` at word boundaries so that no line exceeds `width` characters.
 * Lines in the input are preserved as paragraph breaks.
 * Words longer than `width` are hard-split.
 *
 * Returns an array of lines (each without a trailing newline).
 */
export function wrapWords(text: string, width: number): string[] {
  if (width <= 0) return [text];

  const lines: string[] = [];

  for (const paragraph of text.split("\n")) {
    const words = paragraph.split(/\s+/).filter((w) => w.length > 0);
    if (words.length === 0) {
      lines.push("");
      continue;
    }

    let currentLine = "";
    let currentLen = 0;

    for (const word of words) {
      const wordLen = Array.from(word).length;

      // Hard-split words that exceed the entire width
      if (wordLen > width) {
        if (currentLine.length > 0) {
          lines.push(currentLine);
          currentLine = "";
          currentLen = 0;
        }
        const chars = Array.from(word);
        let pos = 0;
        while (pos < chars.length) {
          lines.push(chars.slice(pos, pos + width).join(""));
          pos += width;
        }
        continue;
      }

      if (currentLen === 0) {
        currentLine = word;
        currentLen = wordLen;
      } else if (currentLen + 1 + wordLen <= width) {
        currentLine += " " + word;
        currentLen += 1 + wordLen;
      } else {
        lines.push(currentLine);
        currentLine = word;
        currentLen = wordLen;
      }
    }

    if (currentLine.length > 0) {
      lines.push(currentLine);
    }
  }

  return lines;
}

// ---------------------------------------------------------------------------
// Indent
// ---------------------------------------------------------------------------

/**
 * Prepend `spaces` space characters to every line in `text`.
 */
export function indent(text: string, spaces: number): string {
  const pad = " ".repeat(Math.max(0, spaces));
  return text
    .split("\n")
    .map((line) => pad + line)
    .join("\n");
}

// ---------------------------------------------------------------------------
// Pad
// ---------------------------------------------------------------------------

/**
 * Pad `text` with spaces to reach `width` visible characters.
 *
 * - `'left'`   (default) — trailing spaces
 * - `'right'`  — leading spaces
 * - `'center'` — balanced leading/trailing spaces
 *
 * Returns `text` unchanged when `visibleLength(text) >= width`.
 */
export function pad(
  text: string,
  width: number,
  align: "left" | "right" | "center" = "left",
): string {
  const vis = visibleLength(text);
  const extra = Math.max(0, width - vis);
  if (extra === 0) return text;
  if (align === "right") return " ".repeat(extra) + text;
  if (align === "center") {
    const left = Math.floor(extra / 2);
    const right = extra - left;
    return " ".repeat(left) + text + " ".repeat(right);
  }
  return text + " ".repeat(extra);
}
