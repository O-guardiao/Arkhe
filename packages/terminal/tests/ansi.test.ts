import { describe, it, expect } from "vitest";
import { ANSI, strip, visibleLength } from "../src/ansi.js";

describe("ANSI constants", () => {
  it("RESET is the SGR reset sequence", () => {
    expect(ANSI.RESET).toBe("\x1b[0m");
  });

  it("BOLD is the SGR bold sequence", () => {
    expect(ANSI.BOLD).toBe("\x1b[1m");
  });

  it("DIM is the SGR dim sequence", () => {
    expect(ANSI.DIM).toBe("\x1b[2m");
  });

  it("HIDE_CURSOR and SHOW_CURSOR are correct", () => {
    expect(ANSI.HIDE_CURSOR).toBe("\x1b[?25l");
    expect(ANSI.SHOW_CURSOR).toBe("\x1b[?25h");
  });
});

describe("ANSI cursor movement", () => {
  it("UP(1) produces ESC[1A", () => {
    expect(ANSI.UP(1)).toBe("\x1b[1A");
  });

  it("UP(3) produces ESC[3A", () => {
    expect(ANSI.UP(3)).toBe("\x1b[3A");
  });

  it("DOWN(2) produces ESC[2B", () => {
    expect(ANSI.DOWN(2)).toBe("\x1b[2B");
  });

  it("COLUMN(5) produces ESC[5G", () => {
    expect(ANSI.COLUMN(5)).toBe("\x1b[5G");
  });
});

describe("ANSI color functions", () => {
  it("fg(r,g,b) produces a 24-bit foreground sequence", () => {
    expect(ANSI.fg(255, 0, 0)).toBe("\x1b[38;2;255;0;0m");
  });

  it("bg(r,g,b) produces a 24-bit background sequence", () => {
    expect(ANSI.bg(0, 255, 0)).toBe("\x1b[48;2;0;255;0m");
  });

  it("fgCode(196) produces a 256-color foreground sequence", () => {
    expect(ANSI.fgCode(196)).toBe("\x1b[38;5;196m");
  });

  it("bgCode(21) produces a 256-color background sequence", () => {
    expect(ANSI.bgCode(21)).toBe("\x1b[48;5;21m");
  });

  it("fg(0,0,0) produces black", () => {
    expect(ANSI.fg(0, 0, 0)).toBe("\x1b[38;2;0;0;0m");
  });
});

describe("ANSI named colors", () => {
  it("red is the standard ANSI red", () => {
    expect(ANSI.red).toBe("\x1b[31m");
  });

  it("cyan is the standard ANSI cyan", () => {
    expect(ANSI.cyan).toBe("\x1b[36m");
  });

  it("gray uses the bright-black (90) code", () => {
    expect(ANSI.gray).toBe("\x1b[90m");
  });
});

describe("strip()", () => {
  it("removes a SGR foreground sequence", () => {
    expect(strip("\x1b[31mhello\x1b[0m")).toBe("hello");
  });

  it("removes bold + reset wrappers", () => {
    expect(strip("\x1b[1mBold\x1b[0m text")).toBe("Bold text");
  });

  it("removes nested SGR sequences", () => {
    expect(strip("\x1b[1m\x1b[32mGreen bold\x1b[0m")).toBe("Green bold");
  });

  it("removes OSC-8 hyperlink open+close sequences", () => {
    const link = "\x1b]8;;https://example.com\x1b\\click here\x1b]8;;\x1b\\";
    expect(strip(link)).toBe("click here");
  });

  it("returns plain text unchanged", () => {
    expect(strip("hello world")).toBe("hello world");
  });

  it("handles empty string", () => {
    expect(strip("")).toBe("");
  });
});

describe("visibleLength()", () => {
  it("counts visible chars after stripping SGR codes", () => {
    expect(visibleLength("\x1b[32mHello\x1b[0m World")).toBe(11);
  });

  it("returns 0 for empty string", () => {
    expect(visibleLength("")).toBe(0);
  });

  it("returns correct length for plain ASCII", () => {
    expect(visibleLength("abc")).toBe(3);
  });

  it("handles string with only escape codes", () => {
    expect(visibleLength("\x1b[1m\x1b[0m")).toBe(0);
  });

  it("counts multi-byte Unicode code points correctly", () => {
    // "Olá" = O + l + á (3 codepoints)
    expect(visibleLength("Olá")).toBe(3);
  });

  it("counts wrapped colorized text correctly", () => {
    const colored = ANSI.fg(0, 188, 212) + "Arkhe" + ANSI.RESET;
    expect(visibleLength(colored)).toBe(5);
  });
});
