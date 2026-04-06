import { describe, it, expect } from "vitest";
import {
  newEnvelope,
  replyEnvelope,
  parseEnvelope,
  safeParseEnvelope,
  type Envelope,
} from "../src/envelope.js";

/** Helper: campos mínimos obrigatórios para newEnvelope */
const minimal = (overrides: Record<string, unknown> = {}) => ({
  source_channel: "telegram" as const,
  source_id: "123456789",
  direction: "inbound" as const,
  text: "hello",
  ...overrides,
});

describe("newEnvelope", () => {
  it("gera id único em formato hex de 32 chars", () => {
    const e1 = newEnvelope(minimal());
    const e2 = newEnvelope(minimal());
    expect(e1.id).toMatch(/^[0-9a-f]{32}$/);
    expect(e2.id).toMatch(/^[0-9a-f]{32}$/);
    expect(e1.id).not.toBe(e2.id);
  });

  it("define direction inbound por padrão", () => {
    const e = newEnvelope(minimal());
    expect(e.direction).toBe("inbound");
  });

  it("herda campos passados no partial", () => {
    const e = newEnvelope(minimal({ metadata: { origin: "test" } }));
    expect(e.metadata).toEqual({ origin: "test" });
  });

  it("timestamp é uma string ISO 8601", () => {
    const before = Date.now();
    const e = newEnvelope(minimal());
    const after = Date.now();
    const ts = new Date(e.timestamp).getTime();
    expect(ts).toBeGreaterThanOrEqual(before);
    expect(ts).toBeLessThanOrEqual(after);
  });
});

describe("replyEnvelope", () => {
  it("cria envelope outbound com correlation_id do original", () => {
    const inbound = newEnvelope(minimal());
    const reply = replyEnvelope(inbound, "resposta aqui");
    expect(reply.direction).toBe("outbound");
    expect(reply.correlation_id).toBe(inbound.id);
    expect(reply.source_channel).toBe("internal");
    expect(reply.text).toBe("resposta aqui");
  });
});

describe("parseEnvelope", () => {
  it("analisa JSON válido", () => {
    const input: Envelope = newEnvelope(minimal());
    const parsed = parseEnvelope(input);
    expect(parsed.id).toBe(input.id);
  });

  it("lança ZodError para objeto inválido", () => {
    expect(() => parseEnvelope({ channel: "invalid-channel-xyz" })).toThrow();
  });
});

describe("safeParseEnvelope", () => {
  it("retorna success=true para envelope válido", () => {
    const input = newEnvelope(minimal());
    const result = safeParseEnvelope(input);
    expect(result.success).toBe(true);
  });

  it("retorna success=false para dado inválido", () => {
    const result = safeParseEnvelope({ foo: "bar" });
    expect(result.success).toBe(false);
  });
});
