import { describe, it, expect } from "vitest";
import { ChannelRegistry } from "../src/registry.js";
import type { ChannelAdapter, ChannelHealth, OutboundEnvelope } from "../src/types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

interface MockControl {
  adapter: ChannelAdapter;
  connectCalled: () => boolean;
  disconnectCalled: () => boolean;
  sendCalled: () => boolean;
}

function makeMockAdapter(id: string, type = "mock"): MockControl {
  let connectCalled = false;
  let disconnectCalled = false;
  let sendCalled = false;

  const adapter: ChannelAdapter = {
    id,
    type,
    connect: async () => {
      connectCalled = true;
    },
    disconnect: async () => {
      disconnectCalled = true;
    },
    send: async (_env: OutboundEnvelope) => {
      sendCalled = true;
    },
    health: (): ChannelHealth => ({ status: "up", latency_ms: 10 }),
  };

  return {
    adapter,
    connectCalled: () => connectCalled,
    disconnectCalled: () => disconnectCalled,
    sendCalled: () => sendCalled,
  };
}

// ---------------------------------------------------------------------------
// Testes
// ---------------------------------------------------------------------------

describe("ChannelRegistry", () => {
  it("register e get retornam o mesmo adapter", () => {
    const registry = new ChannelRegistry();
    const { adapter } = makeMockAdapter("test:1");

    registry.register(adapter);

    expect(registry.get("test:1")).toBe(adapter);
  });

  it("get retorna undefined para id desconhecido", () => {
    const registry = new ChannelRegistry();

    expect(registry.get("nonexistent")).toBeUndefined();
  });

  it("getAll retorna todos os adapters registrados", () => {
    const registry = new ChannelRegistry();
    const { adapter: a1 } = makeMockAdapter("test:1");
    const { adapter: a2 } = makeMockAdapter("test:2");

    registry.register(a1);
    registry.register(a2);

    const all = registry.getAll();
    expect(all).toHaveLength(2);
    expect(all).toContain(a1);
    expect(all).toContain(a2);
  });

  it("healthAll retorna snapshot de saúde indexado por id", () => {
    const registry = new ChannelRegistry();
    const { adapter } = makeMockAdapter("test:health");

    registry.register(adapter);

    const health = registry.healthAll();
    const entry = health["test:health"];

    expect(entry).toBeDefined();
    expect(entry).toEqual({ status: "up", latency_ms: 10 });
  });

  it("connectAll chama connect em todos os adapters", async () => {
    const registry = new ChannelRegistry();
    const m1 = makeMockAdapter("test:1");
    const m2 = makeMockAdapter("test:2");

    registry.register(m1.adapter);
    registry.register(m2.adapter);

    await registry.connectAll();

    expect(m1.connectCalled()).toBe(true);
    expect(m2.connectCalled()).toBe(true);
  });

  it("disconnectAll chama disconnect em todos os adapters", async () => {
    const registry = new ChannelRegistry();
    const m1 = makeMockAdapter("test:1");
    const m2 = makeMockAdapter("test:2");

    registry.register(m1.adapter);
    registry.register(m2.adapter);

    await registry.disconnectAll();

    expect(m1.disconnectCalled()).toBe(true);
    expect(m2.disconnectCalled()).toBe(true);
  });

  it("registrar o mesmo id duas vezes substitui o adapter anterior", () => {
    const registry = new ChannelRegistry();
    const { adapter: a1 } = makeMockAdapter("test:dup");
    const { adapter: a2 } = makeMockAdapter("test:dup");

    registry.register(a1);
    registry.register(a2);

    expect(registry.get("test:dup")).toBe(a2);
    expect(registry.getAll()).toHaveLength(1);
  });

  it("registry vazio: getAll retorna array vazio", () => {
    const registry = new ChannelRegistry();
    expect(registry.getAll()).toEqual([]);
  });

  it("healthAll em registry vazio retorna objeto vazio", () => {
    const registry = new ChannelRegistry();
    expect(registry.healthAll()).toEqual({});
  });
});
