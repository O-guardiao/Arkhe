import { describe, it, expect } from "vitest";
import { chunkText } from "../src/chunker.js";

describe("chunkText", () => {
  it("retorna texto curto sem divisão", () => {
    const chunks = chunkText("oi mundo", { maxLength: 100 });
    expect(chunks).toEqual(["oi mundo"]);
  });

  it("divide por parágrafo quando há quebra dupla", () => {
    const text = "Parágrafo A\n\nParágrafo B";
    const chunks = chunkText(text, { maxLength: 20 });
    expect(chunks).toHaveLength(2);
    expect(chunks[0]).toBe("Parágrafo A");
    expect(chunks[1]).toBe("Parágrafo B");
  });

  it("never exceeds maxLength", () => {
    const text = "a".repeat(1000);
    const chunks = chunkText(text, { maxLength: 100 });
    for (const chunk of chunks) {
      expect(chunk.length).toBeLessThanOrEqual(100);
    }
  });

  it("reconstrói texto original ao juntar chunks (sem markdown)", () => {
    const text = "linha 1\nlinha 2\nlinha 3\nlinha 4\nlinha 5";
    const chunks = chunkText(text, { maxLength: 20 });
    const reconstructed = chunks.join("\n");
    // Cada linha deve estar presente
    expect(reconstructed).toContain("linha 1");
    expect(reconstructed).toContain("linha 5");
  });

  it("não corta dentro de bloco de código", () => {
    const code = "```\n" + "x".repeat(50) + "\n```";
    const long = "texto antes\n\n" + code + "\n\ntexto depois";
    const chunks = chunkText(long, { maxLength: 80 });
    // O bloco de código não deve ser separado no meio
    const codeChunk = chunks.find((c) => c.includes("```"));
    expect(codeChunk).toBeDefined();
    // número par de ``` (abertura e fechamento no mesmo chunk)
    const ticks = (codeChunk ?? "").match(/```/g) ?? [];
    expect(ticks.length % 2).toBe(0);
  });
});
