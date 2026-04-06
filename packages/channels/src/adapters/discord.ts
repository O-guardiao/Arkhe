/**
 * Discord Channel Adapter (webhook-based).
 *
 * Implementação leve usando a Discord Webhook API — sem discord.js,
 * sem dependências pesadas. Suporta mensagens de texto e embeds com imagem.
 *
 * @see https://discord.com/developers/docs/resources/webhook
 */

import type { ChannelAdapter, ChannelHealth, OutboundEnvelope } from "../types.js";

// ---------------------------------------------------------------------------
// Tipos internos
// ---------------------------------------------------------------------------

type HttpInit = {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
};

type HttpResult = { ok: boolean; status: number };

/** Função fetch injetável para substituição em testes. */
export type FetchLike = (url: string, init?: HttpInit) => Promise<HttpResult>;

/** Fetch nativo do Node 20+ como implementação padrão. */
const nativeFetch: FetchLike = (url, init) => globalThis.fetch(url, init as RequestInit);

// ---------------------------------------------------------------------------
// Opções de configuração
// ---------------------------------------------------------------------------

export interface DiscordAdapterOptions {
  /** URL do webhook do Discord. Deve usar HTTPS. */
  webhook_url: string;
  /** Token de bot para autenticação (reservado para extensões futuras). */
  bot_token: string;
  /** ID do canal Discord alvo. */
  channel_id: string;
  /**
   * @internal Substitui a função fetch — usado apenas em testes unitários.
   * Não definir em produção.
   */
  _fetchFn?: FetchLike;
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

export class DiscordAdapter implements ChannelAdapter {
  readonly id: string;
  readonly type = "discord" as const;

  private readonly webhookUrl: string;
  private readonly fetchFn: FetchLike;

  private connected = false;
  private lastSendAt: number | null = null;
  private lastError: string | null = null;

  constructor(options: DiscordAdapterOptions) {
    if (!options.webhook_url.startsWith("https://")) {
      throw new Error("Discord webhook_url must use HTTPS");
    }
    this.webhookUrl = options.webhook_url;
    this.id = `discord:${options.channel_id}`;
    this.fetchFn = options._fetchFn ?? nativeFetch;
  }

  async connect(): Promise<void> {
    // Valida estrutura da URL (o construtor já garante HTTPS)
    new URL(this.webhookUrl);
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  async send(envelope: OutboundEnvelope): Promise<void> {
    const body: Record<string, unknown> = {
      content: envelope.text,
    };

    if (envelope.media_url !== undefined) {
      body["embeds"] = [{ image: { url: envelope.media_url } }];
    }

    let res: HttpResult;
    try {
      res = await this.fetchFn(this.webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (err) {
      this.lastError = err instanceof Error ? err.message : String(err);
      throw err;
    }

    this.lastSendAt = Date.now();

    if (!res.ok) {
      this.lastError = `Discord webhook returned ${res.status}`;
      throw new Error(this.lastError);
    }

    this.lastError = null;
  }

  health(): ChannelHealth {
    if (!this.connected) {
      const base: ChannelHealth = { status: "down", latency_ms: 0 };
      return this.lastError !== null ? { ...base, last_error: this.lastError } : base;
    }

    const latency_ms = this.lastSendAt !== null ? Date.now() - this.lastSendAt : 0;
    const base: ChannelHealth = {
      status: this.lastError !== null ? "degraded" : "up",
      latency_ms,
    };
    return this.lastError !== null ? { ...base, last_error: this.lastError } : base;
  }
}
