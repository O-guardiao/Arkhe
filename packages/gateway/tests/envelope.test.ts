import { describe, it, expect } from "vitest";
import {
  newEnvelope,
  replyEnvelope,
  parseEnvelope,
  safeParseEnvelope,
  type Envelope,
} from "../src/envelope.js";

describe("newEnvelope", () => {
  it("gera id único em formato hex de 32 chars", () => {
    const e1 = newEnvelope({ channel: "telegram", direction: "inbound" });
    const e2 = newEnvelope({ channel: "telegram", direction: "inbound" });
    expect(e1.id).toMatch(/^[0-9a-f]{32}$/);
    expect(e2.id).toMatch(/^[0-9a-f]{32}$/);
    expect(e1.id).not.toBe(e2.id);
  });

  it("define direction inbound por padrão", () => {
    const e = newEnvelope({ channel: "telegram" });
    expect(e.direction).toBe("inbound");
  });

  it("herda campos passados no partial", () => {
    const e = newEnvelope({
      channel: "telegram",
      routing_key: "123456",
      payload: { text: "oi" },
    });
    expect(e.routing_key).toBe("123456");
    expect(e.payload).toEqual({ text: "oi" });
  });

  it("timestamp é um número positivo", () => {
    const before = Date.now();
    const e = newEnvelope({ channel: "telegram" });
    const after = Date.now();
    expect(e.timestamp).toBeGreaterThanOrEqual(before);
    expect(e.timestamp).toBeLessThanOrEqual(after);
  });
});

describe("replyEnvelope", () => {
  it("cria envelope outbound com correlation_id do original", () => {
    const inbound = newEnvelope({
      channel: "telegram",
      direction: "inbound",
      source_client_id: "telegram:123",
      routing_key: "456",
    });
    const reply = replyEnvelope(inbound, "resposta aqui");
    expect(reply.direction).toBe("outbound");
    expect(reply.correlation_id).toBe(inbound.id);
    expect(reply.channel).toBe(inbound.channel);
    expect(reply.routing_key).toBe(inbound.routing_key);
    expect(reply.payload?.text).toBe("resposta aqui");
  });
});

describe("parseEnvelope", () => {
  it("analisa JSON válido", () => {
    const input: Envelope = newEnvelope({ channel: "telegram", direction: "inbound" });
    const parsed = parseEnvelope(input);
    expect(parsed.id).toBe(input.id);
  });

  it("lança ZodError para objeto inválido", () => {
    expect(() => parseEnvelope({ channel: "invalid-channel-xyz" })).toThrow();
  });
});

describe("safeParseEnvelope", () => {
  it("retorna success=true para envelope válido", () => {
    const input = newEnvelope({ channel: "telegram" });
    const result = safeParseEnvelope(input);
    expect(result.success).toBe(true);
  });

  it("retorna success=false para dado inválido", () => {
    const result = safeParseEnvelope({ foo: "bar" });
    expect(result.success).toBe(false);
  });
});
