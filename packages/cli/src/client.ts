/**
 * Configuração e cliente HTTP para o servidor RLM.
 *
 * Lê RLM_HOST, RLM_PORT e RLM_TOKEN das variáveis de ambiente.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Schema de configuração
// ---------------------------------------------------------------------------

const ConfigSchema = z.object({
  host: z.string().default("http://localhost:8000"),
  token: z.string().default(""),
  timeout: z.number().positive().default(60_000),
});

export type RlmConfig = z.infer<typeof ConfigSchema>;

export function loadConfig(): RlmConfig {
  const raw: Record<string, unknown> = {
    host:
      process.env["RLM_HOST"] ??
      `http://localhost:${process.env["RLM_PORT"] ?? "8000"}`,
    token: process.env["RLM_TOKEN"] ?? "",
    timeout: Number(process.env["RLM_TIMEOUT_MS"] ?? "60000"),
  };
  return ConfigSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// Cliente HTTP
// ---------------------------------------------------------------------------

export class RlmClient {
  private readonly base: string;
  private readonly token: string;
  private readonly timeout: number;

  constructor(config?: Partial<RlmConfig>) {
    const full = { ...loadConfig(), ...config };
    this.base = full.host.replace(/\/$/, "");
    this.token = full.token;
    this.timeout = full.timeout;
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      "content-type": "application/json",
    };
    if (this.token) {
      h["authorization"] = `Bearer ${this.token}`;
    }
    return h;
  }

  async get<T>(path: string): Promise<T> {
    const res = await this._fetch("GET", path);
    return (await res.json()) as T;
  }

  async post<T>(path: string, body: unknown): Promise<T> {
    const res = await this._fetch("POST", path, body);
    return (await res.json()) as T;
  }

  async delete<T>(path: string): Promise<T> {
    const res = await this._fetch("DELETE", path);
    return (await res.json()) as T;
  }

  private async _fetch(
    method: string,
    path: string,
    body?: unknown
  ): Promise<Response> {
    const url = `${this.base}${path}`;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.timeout);

    try {
      const res = await fetch(url, {
        method,
        headers: this.headers(),
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: ctrl.signal,
      });

      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new RlmApiError(res.status, text, url);
      }
      return res;
    } finally {
      clearTimeout(timer);
    }
  }
}

export class RlmApiError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly body: string,
    public readonly url: string
  ) {
    super(`HTTP ${statusCode} at ${url}: ${body.slice(0, 200)}`);
    this.name = "RlmApiError";
  }
}
