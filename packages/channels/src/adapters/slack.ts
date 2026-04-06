/**
 * Slack Channel Adapter (Incoming Webhooks + verificação de assinatura).
 *
 * Formata mensagens como Slack Block Kit e verifica a assinatura HMAC-SHA256
 * de payloads recebidos para garantir autenticidade.
 *
 * @see https://api.slack.com/messaging/webhooks
 * @see https://api.slack.com/authentication/verifying-requests-from-slack
 */

import { createHmac, timingSafeEqual } from "node:crypto";
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

export type FetchLike = (url: string, init?: HttpInit) => Promise<HttpResult>;

const nativeFetch: FetchLike = (url, init) => globalThis.fetch(url, init as RequestInit);

// ---------------------------------------------------------------------------
// Opções de configuração
// ---------------------------------------------------------------------------

export interface SlackAdapterOptions {
  /** URL do Incoming Webhook gerado no Slack App. Deve usar HTTPS. */
  webhook_url: string;
  /** Nome ou ID do canal destino (ex.: `#general`). */
  channel: string;
  /** Signing secret para verificação HMAC de payloads Slack Events API. */
  signing_secret: string;
  /**
   * @internal Substitui a função fetch — usado apenas em testes unitários.
   */
  _fetchFn?: FetchLike;
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

export class SlackAdapter implements ChannelAdapter {
  readonly id: string;
  readonly type = "slack" as const;

  private readonly webhookUrl: string;
  private readonly channel: string;
  private readonly signingSecret: string;
  private readonly fetchFn: FetchLike;

  private connected = false;
  private lastSendAt: number | null = null;
  private lastError: string | null = null;

  constructor(options: SlackAdapterOptions) {
    if (!options.webhook_url.startsWith("https://")) {
      throw new Error("Slack webhook_url must use HTTPS");
    }
    this.webhookUrl = options.webhook_url;
    this.channel = options.channel;
    this.signingSecret = options.signing_secret;
    this.id = `slack:${options.channel}`;
    this.fetchFn = options._fetchFn ?? nativeFetch;
  }

  async connect(): Promise<void> {
    // Valida estrutura da URL
    new URL(this.webhookUrl);
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  async send(envelope: OutboundEnvelope): Promise<void> {
    // Formata mensagem usando Block Kit
    const blocks: Record<string, unknown>[] = [
      {
        type: "section",
        text: { type: "mrkdwn", text: envelope.text },
      },
    ];

    if (envelope.media_url !== undefined) {
      blocks.push({
        type: "image",
        image_url: envelope.media_url,
        alt_text: "image",
      });
    }

    const payload: Record<string, unknown> = {
      channel: this.channel,
      blocks,
    };

    let res: HttpResult;
    try {
      res = await this.fetchFn(this.webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      this.lastError = err instanceof Error ? err.message : String(err);
      throw err;
    }

    this.lastSendAt = Date.now();

    if (!res.ok) {
      this.lastError = `Slack webhook returned ${res.status}`;
      throw new Error(this.lastError);
    }

    this.lastError = null;
  }

  /**
   * Verifica a assinatura HMAC-SHA256 de um payload recebido da Slack Events API.
   *
   * @param body      Corpo bruto da requisição (string utf-8)
   * @param timestamp Valor do header `X-Slack-Request-Timestamp`
   * @param signature Valor do header `X-Slack-Signature` (formato `v0=<hex>`)
   * @returns `true` se a assinatura é válida, `false` caso contrário
   */
  verifySignature(body: string, timestamp: string, signature: string): boolean {
    const baseString = `v0:${timestamp}:${body}`;
    const computed = `v0=${createHmac("sha256", this.signingSecret)
      .update(baseString)
      .digest("hex")}`;

    // Compara apenas se os comprimentos forem iguais (timingSafeEqual exige isso)
    if (computed.length !== signature.length) return false;

    const a = Buffer.from(computed, "utf8");
    const b = Buffer.from(signature, "utf8");
    return timingSafeEqual(a, b);
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
