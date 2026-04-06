import { describe, it, expect } from "vitest";
import {
  sanitizeForTerminal,
  truncate,
  wrapWords,
  indent,
  pad,
} from "../src/safe-text.js";

// ---------------------------------------------------------------------------
// sanitizeForTerminal
// ---------------------------------------------------------------------------

describe("sanitizeForTerminal()", () => {
  it("removes SGR ANSI codes", () => {
    expect(sanitizeForTerminal("\x1b[31mhello\x1b[0m")).toBe("hello");
  });

  it("removes C0 control characters, keeping printable text", () => {
    expect(sanitizeForTerminal("hello\x01world")).toBe("helloworld");
  });

  it("preserves newline (\\n)", () => {
    expect(sanitizeForTerminal("line1\nline2")).toBe("line1\nline2");
  });

  it("preserves tab (\\t)", () => {
    expect(sanitizeForTerminal("col1\tcol2")).toBe("col1\tcol2");
  });

  it("removes DEL and C1 control chars", () => {
    expect(sanitizeForTerminal("a\x7fb\x9fc")).toBe("abc");
  });

  it("preserves printable Unicode", () => {
    expect(sanitizeForTerminal("Ação válida 🎯")).toBe("Ação válida 🎯");
  });

  it("handles empty string", () => {
    expect(sanitizeForTerminal("")).toBe("");
  });
});

// ---------------------------------------------------------------------------
// truncate
// ---------------------------------------------------------------------------

describe("truncate()", () => {
  it("does not truncate strings within maxLength", () => {
    expect(truncate("hello", 10)).toBe("hello");
  });

  it("returns string unchanged when visibleLength equals maxLength", () => {
    expect(truncate("hello", 5)).toBe("hello");
  });

  it("truncates with default '…' ellipsis", () => {
    expect(truncate("hello world", 8)).toBe("hello w…");
  });

  it("truncates with a custom suffix", () => {
    expect(truncate("hello world", 8, "...")).toBe("hello...");
  });

  it("handles maxLength 0 by returning the suffix", () => {
    expect(truncate("hello", 0)).toBe("…");
  });

  it("handles maxLength 1 by returning just the ellipsis", () => {
    expect(truncate("hello", 1)).toBe("…");
  });

  it("returns empty string when maxLength 0 and suffix is empty", () => {
    expect(truncate("hello", 0, "")).toBe("");
  });

  it("works correctly for Unicode code points", () => {
    // "Olá mundo" — 9 codepoints. maxLength=6 → target=5 chars content → "Olá m" + "…"
    const result = truncate("Olá mundo", 6);
    expect(result).toBe("Olá m…");
  });
});

// ---------------------------------------------------------------------------
// wrapWords
// ---------------------------------------------------------------------------

describe("wrapWords()", () => {
  it("returns unchanged text when shorter than width", () => {
    expect(wrapWords("hi there", 80)).toEqual(["hi there"]);
  });

  it("wraps at word boundaries", () => {
    expect(wrapWords("hello world foo bar", 10)).toEqual([
      "hello",
      "world foo",
      "bar",
    ]);
  });

  it("hard-splits words longer than width", () => {
    expect(wrapWords("abcdefgh", 4)).toEqual(["abcd", "efgh"]);
  });

  it("hard-splits a very long word with remainder", () => {
    expect(wrapWords("abcdefghij", 4)).toEqual(["abcd", "efgh", "ij"]);
  });

  it("preserves paragraph breaks from \\n", () => {
    const result = wrapWords("hello\nworld", 80);
    expect(result).toContain("hello");
    expect(result).toContain("world");
  });

  it("returns an empty-string entry for an empty input", () => {
    expect(wrapWords("", 10)).toEqual([""]);
  });

  it("handles multiple spaces between words", () => {
    const result = wrapWords("a   b   c", 10);
    expect(result).toEqual(["a b c"]);
  });
});

// ---------------------------------------------------------------------------
// indent
// ---------------------------------------------------------------------------

describe("indent()", () => {
  it("indents a single line", () => {
    expect(indent("hello", 4)).toBe("    hello");
  });

  it("indents all lines in multi-line text", () => {
    expect(indent("line1\nline2", 2)).toBe("  line1\n  line2");
  });

  it("handles zero spaces", () => {
    expect(indent("hello", 0)).toBe("hello");
  });

  it("handles negative spaces as zero", () => {
    expect(indent("hello", -1)).toBe("hello");
  });
});

// ---------------------------------------------------------------------------
// pad
// ---------------------------------------------------------------------------

describe("pad()", () => {
  it("left-pads by default (trailing spaces)", () => {
    expect(pad("hi", 5)).toBe("hi   ");
  });

  it("right-pads with leading spaces", () => {
    expect(pad("hi", 5, "right")).toBe("   hi");
  });

  it("center-pads with balanced spaces", () => {
    expect(pad("hi", 6, "center")).toBe("  hi  ");
  });

  it("center-pads with odd remainder leans right", () => {
    // extra=3: left=1, right=2
    expect(pad("hi", 5, "center")).toBe(" hi  ");
  });

  it("returns text unchanged when already at target width", () => {
    expect(pad("hello", 5)).toBe("hello");
  });

  it("returns text unchanged when wider than target", () => {
    expect(pad("hello world", 5)).toBe("hello world");
  });

  it("works with ANSI-colored text by measuring visible length", () => {
    const colored = "\x1b[32mAB\x1b[0m";
    const result = pad(colored, 5);
    // visible length of colored is 2, extra = 3
    expect(result.endsWith("   ")).toBe(true);
  });
});
