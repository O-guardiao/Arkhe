/**
 * Cliente HTTP do workbench vivo para o servidor Arkhe.
 *
 * Migrado de rlm/cli/tui/live_api.py
 */

import type { CliContext } from "../context.js";

// ─────────────────────────────────────────────── Erro operacional

export class LiveWorkbenchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LiveWorkbenchError";
  }
}

// ─────────────────────────────────────────────── Helpers internos

function getInternalToken(env: Record<string, string>): string {
  for (const name of ["RLM_INTERNAL_TOKEN", "RLM_WS_TOKEN", "RLM_API_TOKEN"]) {
    const token = (env[name] ?? "").trim();
    if (token) return token;
  }
  return "";
}

function buildHeaders(env: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getInternalToken(env);
  if (token) {
    headers["X-RLM-Token"] = token;
  }
  return headers;
}

// ─────────────────────────────────────────────── Tipos de dados

export interface LiveSessionInfo {
  session_id: string;
  client_id: string;
  status: string;
  state_dir: string;
  metadata: Record<string, unknown>;
}

// ─────────────────────────────────────────────── Classe principal

export class LiveWorkbenchAPI {
  private readonly _context: CliContext;
  private readonly _baseUrl: string;
  private readonly _headers: Record<string, string>;

  constructor(context: CliContext, baseUrl?: string) {
    this._context = context;
    this._baseUrl = (
      baseUrl ||
      (context.env as Record<string, string>)["RLM_INTERNAL_HOST"] ||
      context.apiBaseUrl()
    ).replace(/\/$/, "");
    this._headers = buildHeaders(context.env as Record<string, string>);
  }

  get baseUrl(): string {
    return this._baseUrl;
  }

  /** Verifica se o servidor está acessível via /health. */
  async probe(opts: { timeout?: number } = {}): Promise<boolean> {
    const url = `${this._baseUrl}/health`;
    try {
      const resp = await fetch(url, {
        method: "GET",
        headers: this._headers,
        signal: AbortSignal.timeout((opts.timeout ?? 3) * 1000),
      });
      return resp.status === 200;
    } catch {
      return false;
    }
  }

  /** Cria ou retoma uma sessão de operador. */
  async ensureSession(clientId: string): Promise<LiveSessionInfo> {
    const payload = await this._requestJson("POST", "/operator/session", { client_id: clientId });
    return {
      session_id: String(payload["session_id"] ?? ""),
      client_id: String(payload["client_id"] ?? clientId),
      status: String(payload["status"] ?? "idle"),
      state_dir: String(payload["state_dir"] ?? ""),
      metadata: (payload["metadata"] as Record<string, unknown>) ?? {},
    };
  }

  /** Busca atividade da sessão. */
  async fetchActivity(sessionId: string): Promise<Record<string, unknown>> {
    return this._requestJson("GET", `/operator/session/${sessionId}/activity`);
  }

  /** Envia um prompt para a sessão. */
  async dispatchPrompt(
    sessionId: string,
    clientId: string,
    text: string
  ): Promise<Record<string, unknown>> {
    return this._requestJson(
      "POST",
      `/operator/session/${sessionId}/message`,
      { client_id: clientId, text },
      { timeout: 15 }
    );
  }

  /** Aplica um comando à sessão. */
  async applyCommand(
    sessionId: string,
    opts: {
      clientId: string;
      commandType: string;
      payload: Record<string, unknown>;
      branchId: number | null;
    }
  ): Promise<Record<string, unknown>> {
    return this._requestJson("POST", `/operator/session/${sessionId}/commands`, {
      client_id: opts.clientId,
      command_type: opts.commandType,
      payload: opts.payload,
      branch_id: opts.branchId,
    });
  }

  // ── Endpoints de status de canais ──────────────────────────────────────

  /** GET /api/channels/status — retorna snapshot de todos os canais. */
  async fetchChannelsStatus(): Promise<Record<string, unknown>> {
    return this._requestJson("GET", "/api/channels/status");
  }

  /** POST /api/channels/{channelId}/probe — probe sob demanda. */
  async probeChannel(channelId: string): Promise<Record<string, unknown>> {
    return this._requestJson("POST", `/api/channels/${channelId}/probe`);
  }

  /** POST /api/channels/send — envia mensagem cross-channel. */
  async crossChannelSend(
    targetClientId: string,
    message: string
  ): Promise<Record<string, unknown>> {
    return this._requestJson("POST", "/api/channels/send", {
      target_client_id: targetClientId,
      message,
    });
  }

  // ── Método interno de requisição ───────────────────────────────────────

  private async _requestJson(
    method: string,
    apiPath: string,
    body?: Record<string, unknown>,
    opts: { timeout?: number } = {}
  ): Promise<Record<string, unknown>> {
    const url = `${this._baseUrl}${apiPath}`;
    const timeoutMs = (opts.timeout ?? 10) * 1000;

    let resp: Response;
    try {
      resp = await fetch(url, {
        method,
        headers: this._headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        throw new LiveWorkbenchError(
          `Timeout ao chamar ${apiPath} (>${opts.timeout ?? 10}s)`
        );
      }
      throw new LiveWorkbenchError(
        `Servidor vivo indisponível em ${this._baseUrl}. Rode 'arkhe start' e tente novamente.`
      );
    }

    if (!resp.ok) {
      let detail = "";
      try {
        detail = await resp.text();
      } catch {
        // ignore
      }
      throw new LiveWorkbenchError(
        `HTTP ${resp.status} em ${apiPath}: ${detail.slice(0, 300)}`
      );
    }

    let payload: unknown;
    try {
      payload = await resp.json();
    } catch {
      throw new LiveWorkbenchError(`Resposta inválida (não-JSON) do backend vivo em ${apiPath}`);
    }

    if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
      throw new LiveWorkbenchError(`Resposta inválida do backend vivo em ${apiPath}`);
    }

    return payload as Record<string, unknown>;
  }
}
