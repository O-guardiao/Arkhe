import { describe, expect, it } from "vitest";
import { ANSI } from "../src/ansi.js";
import {
  stylePromptHint,
  stylePromptMessage,
  stylePromptTitle,
} from "../src/prompt-style.js";

describe("prompt-style", () => {
  it("styles prompt messages with a reset suffix", () => {
    const styled = stylePromptMessage("Mensagem");
    expect(styled).toContain("Mensagem");
    expect(styled.endsWith(ANSI.RESET)).toBe(true);
  });

  it("returns undefined titles and hints untouched", () => {
    expect(stylePromptTitle()).toBeUndefined();
    expect(stylePromptHint()).toBeUndefined();
  });
});