import { describe, it, expect, vi, beforeEach } from "vitest";
import { RlmClient, RlmApiError, loadConfig } from "../src/client.js";

// ---------------------------------------------------------------------------
// loadConfig
// ---------------------------------------------------------------------------
describe("loadConfig", () => {
  beforeEach(() => {
    delete process.env["RLM_HOST"];
    delete process.env["RLM_PORT"];
    delete process.env["RLM_TOKEN"];
    delete process.env["RLM_TIMEOUT_MS"];
  });

  it("usa valores padrão quando as env vars não estão definidas", () => {
    const cfg = loadConfig();
    expect(cfg.host).toBe("http://localhost:8000");
    expect(cfg.token).toBe("");
    expect(cfg.timeout).toBe(60_000);
  });

  it("lê RLM_HOST", () => {
    process.env["RLM_HOST"] = "http://myserver:9000";
    const cfg = loadConfig();
    expect(cfg.host).toBe("http://myserver:9000");
  });

  it("lê RLM_PORT quando RLM_HOST não está definido", () => {
    process.env["RLM_PORT"] = "1234";
    const cfg = loadConfig();
    expect(cfg.host).toBe("http://localhost:1234");
  });

  it("lê RLM_TOKEN", () => {
    process.env["RLM_TOKEN"] = "my-secret";
    const cfg = loadConfig();
    expect(cfg.token).toBe("my-secret");
  });
});

// ---------------------------------------------------------------------------
// RlmApiError
// ---------------------------------------------------------------------------
describe("RlmApiError", () => {
  it("tem mensagem formatada", () => {
    const err = new RlmApiError(404, "not found", "http://localhost/x");
    expect(err.message).toContain("404");
    expect(err.message).toContain("http://localhost/x");
    expect(err.name).toBe("RlmApiError");
    expect(err.statusCode).toBe(404);
  });
});

// ---------------------------------------------------------------------------
// RlmClient — fetch mockado
// ---------------------------------------------------------------------------
describe("RlmClient", () => {
  function makeMockFetch(
    status: number,
    body: unknown
  ): typeof globalThis.fetch {
    return vi.fn().mockResolvedValue({
      ok: status < 400,
      status,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response);
  }

  it("GET retorna JSON parseado", async () => {
    globalThis.fetch = makeMockFetch(200, { status: "ok" });
    const client = new RlmClient({ host: "http://test" });
    const result = await client.get<{ status: string }>("/health");
    expect(result.status).toBe("ok");
  });

  it("POST envia body e retorna JSON parseado", async () => {
    const mockFetch = makeMockFetch(200, { response: "olá" });
    globalThis.fetch = mockFetch;
    const client = new RlmClient({ host: "http://test" });
    const result = await client.post<{ response: string }>("/brain/prompt", { content: "hi" });
    expect(result.response).toBe("olá");

    // Verifica que body foi enviado
    const call = (mockFetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(call[1]?.body).toContain('"content":"hi"');
  });

  it("lança RlmApiError em resposta 4xx", async () => {
    globalThis.fetch = makeMockFetch(422, { detail: "invalid" });
    const client = new RlmClient({ host: "http://test" });
    await expect(client.get("/x")).rejects.toBeInstanceOf(RlmApiError);
  });

  it("inclui Authorization header quando token está configurado", async () => {
    const mockFetch = makeMockFetch(200, {});
    globalThis.fetch = mockFetch;
    const client = new RlmClient({ host: "http://test", token: "tok123" });
    await client.get("/x");
    const call = (mockFetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const headers = call[1]?.headers as Record<string, string>;
    expect(headers["authorization"]).toBe("Bearer tok123");
  });
});
