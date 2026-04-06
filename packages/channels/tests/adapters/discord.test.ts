import { describe, it, expect } from "vitest";
import { DiscordAdapter } from "../../src/adapters/discord.js";
import type { FetchLike } from "../../src/adapters/discord.js";
import type { OutboundEnvelope } from "../../src/types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Cria um mock fetch que sempre retorna sucesso. */
function makeMockFetch(
  statusCode = 200,
  ok = true,
): { fetchFn: FetchLike; calls: { url: string; method?: string; body?: unknown }[] } {
  const calls: { url: string; method?: string; body?: unknown }[] = [];

  const fetchFn: FetchLike = async (url, init) => {
    calls.push({
      url,
      method: init?.method,
      body: init?.body !== undefined ? (JSON.parse(init.body) as unknown) : undefined,
    });
    return { ok, status: statusCode };
  };

  return { fetchFn, calls };
}

const BASE_OPTIONS = {
  webhook_url: "https://discord.com/api/webhooks/123456/token",
  bot_token: "Bot my-bot-token",
  channel_id: "chan-001",
} as const;

const BASIC_ENVELOPE: OutboundEnvelope = {
  channel_id: "chan-001",
  channel_type: "discord",
  target_external_id: "user-42",
  text: "Olá, Discord!",
};

// ---------------------------------------------------------------------------
// Testes
// ---------------------------------------------------------------------------

describe("DiscordAdapter", () => {
  describe("construtor", () => {
    it("lança erro se webhook_url não usar HTTPS", () => {
      expect(
        () =>
          new DiscordAdapter({
            ...BASE_OPTIONS,
            webhook_url: "http://discord.com/api/webhooks/bad",
          }),
      ).toThrow(/HTTPS/i);
    });

    it("define id como `discord:<channel_id>`", () => {
      const adapter = new DiscordAdapter(BASE_OPTIONS);
      expect(adapter.id).toBe("discord:chan-001");
    });

    it("define type como `discord`", () => {
      const adapter = new DiscordAdapter(BASE_OPTIONS);
      expect(adapter.type).toBe("discord");
    });
  });

  describe("connect()", () => {
    it("resolve sem erros com URL HTTPS válida", async () => {
      const adapter = new DiscordAdapter(BASE_OPTIONS);
      await expect(adapter.connect()).resolves.toBeUndefined();
    });

    it("após connect, health retorna status `up`", async () => {
      const { fetchFn } = makeMockFetch();
      const adapter = new DiscordAdapter({ ...BASE_OPTIONS, _fetchFn: fetchFn });
      await adapter.connect();

      const h = adapter.health();
      expect(h.status).toBe("up");
    });
  });

  describe("health() antes de connect()", () => {
    it("retorna status `down` quando não conectado", () => {
      const adapter = new DiscordAdapter(BASE_OPTIONS);
      expect(adapter.health().status).toBe("down");
    });
  });

  describe("send()", () => {
    it("chama fetchFn com método POST e Content-Type correto", async () => {
      const { fetchFn, calls } = makeMockFetch();
      const adapter = new DiscordAdapter({ ...BASE_OPTIONS, _fetchFn: fetchFn });
      await adapter.connect();

      await adapter.send(BASIC_ENVELOPE);

      expect(calls).toHaveLength(1);
      expect(calls[0]?.method).toBe("POST");
    });

    it("envia `content` com o texto do envelope", async () => {
      const { fetchFn, calls } = makeMockFetch();
      const adapter = new DiscordAdapter({ ...BASE_OPTIONS, _fetchFn: fetchFn });
      await adapter.connect();

      await adapter.send(BASIC_ENVELOPE);

      expect(calls[0]?.body).toMatchObject({ content: "Olá, Discord!" });
    });

    it("inclui embed com imagem quando media_url está presente", async () => {
      const { fetchFn, calls } = makeMockFetch();
      const adapter = new DiscordAdapter({ ...BASE_OPTIONS, _fetchFn: fetchFn });
      await adapter.connect();

      const envelope: OutboundEnvelope = {
        ...BASIC_ENVELOPE,
        media_url: "https://example.com/img.png",
      };
      await adapter.send(envelope);

      expect(calls[0]?.body).toMatchObject({
        content: "Olá, Discord!",
        embeds: [{ image: { url: "https://example.com/img.png" } }],
      });
    });

    it("não inclui `embeds` quando media_url está ausente", async () => {
      const { fetchFn, calls } = makeMockFetch();
      const adapter = new DiscordAdapter({ ...BASE_OPTIONS, _fetchFn: fetchFn });
      await adapter.connect();

      await adapter.send(BASIC_ENVELOPE);

      const body = calls[0]?.body as Record<string, unknown>;
      expect(body).toBeDefined();
      expect(Object.prototype.hasOwnProperty.call(body, "embeds")).toBe(false);
    });

    it("lança erro quando fetchFn retorna resposta não-OK", async () => {
      const { fetchFn } = makeMockFetch(429, false);
      const adapter = new DiscordAdapter({ ...BASE_OPTIONS, _fetchFn: fetchFn });
      await adapter.connect();

      await expect(adapter.send(BASIC_ENVELOPE)).rejects.toThrow(/429/);
    });

    it("após falha, health retorna status `degraded`", async () => {
      const { fetchFn } = makeMockFetch(500, false);
      const adapter = new DiscordAdapter({ ...BASE_OPTIONS, _fetchFn: fetchFn });
      await adapter.connect();

      await expect(adapter.send(BASIC_ENVELOPE)).rejects.toThrow();

      const h = adapter.health();
      expect(h.status).toBe("degraded");
      expect(h.last_error).toContain("500");
    });

    it("após falha seguida de sucesso, health volta para `up`", async () => {
      let shouldFail = true;
      const toggleFetch: FetchLike = async () => {
        if (shouldFail) return { ok: false, status: 503 };
        return { ok: true, status: 200 };
      };

      const adapter = new DiscordAdapter({ ...BASE_OPTIONS, _fetchFn: toggleFetch });
      await adapter.connect();

      // Primeira chamada falha
      await expect(adapter.send(BASIC_ENVELOPE)).rejects.toThrow();
      expect(adapter.health().status).toBe("degraded");

      // Segunda chamada tem sucesso
      shouldFail = false;
      await adapter.send(BASIC_ENVELOPE);
      expect(adapter.health().status).toBe("up");
    });
  });

  describe("disconnect()", () => {
    it("após disconnect, health retorna status `down`", async () => {
      const { fetchFn } = makeMockFetch();
      const adapter = new DiscordAdapter({ ...BASE_OPTIONS, _fetchFn: fetchFn });
      await adapter.connect();
      await adapter.disconnect();

      expect(adapter.health().status).toBe("down");
    });
  });
});
