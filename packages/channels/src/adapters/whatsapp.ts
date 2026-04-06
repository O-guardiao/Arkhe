/**
 * WhatsApp Business Cloud API Adapter.
 *
 * Envia mensagens via API oficial da Meta (Cloud API v18.0).
 * Suporta mensagens de texto simples e mensagens com imagem.
 * Inclui verificação de webhook para o handshake inicial do Meta.
 *
 * @see https://developers.facebook.com/docs/whatsapp/cloud-api/messages
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

export type FetchLike = (url: string, init?: HttpInit) => Promise<HttpResult>;

const nativeFetch: FetchLike = (url, init) => globalThis.fetch(url, init as RequestInit);

const WHATSAPP_API_BASE = "https://graph.facebook.com/v18.0";

// ---------------------------------------------------------------------------
// Opções de configuração
// ---------------------------------------------------------------------------

export interface WhatsAppAdapterOptions {
  /** ID do número de telefone registrado no WhatsApp Business. */
  phone_number_id: string;
  /** Token de acesso permanente ou temporário da Meta. */
  access_token: string;
  /** Token de verificação definido no painel de desenvolvedor da Meta. */
  verify_token: string;
  /**
   * @internal Substitui a função fetch — usado apenas em testes unitários.
   */
  _fetchFn?: FetchLike;
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

export class WhatsAppAdapter implements ChannelAdapter {
  readonly id: string;
  readonly type = "whatsapp" as const;

  private readonly phoneNumberId: string;
  private readonly accessToken: string;
  private readonly verifyToken: string;
  private readonly fetchFn: FetchLike;

  private connected = false;
  private lastSendAt: number | null = null;
  private lastError: string | null = null;

  constructor(options: WhatsAppAdapterOptions) {
    this.phoneNumberId = options.phone_number_id;
    this.accessToken = options.access_token;
    this.verifyToken = options.verify_token;
    this.id = `whatsapp:${options.phone_number_id}`;
    this.fetchFn = options._fetchFn ?? nativeFetch;
  }

  async connect(): Promise<void> {
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  async send(envelope: OutboundEnvelope): Promise<void> {
    const url = `${WHATSAPP_API_BASE}/${this.phoneNumberId}/messages`;

    let message: Record<string, unknown>;

    if (envelope.media_url !== undefined) {
      // Mensagem com imagem
      message = {
        messaging_product: "whatsapp",
        to: envelope.target_external_id,
        type: "image",
        image: {
          link: envelope.media_url,
          caption: envelope.text,
        },
      };
    } else {
      // Mensagem de texto puro
      message = {
        messaging_product: "whatsapp",
        to: envelope.target_external_id,
        type: "text",
        text: { body: envelope.text },
      };
    }

    let res: HttpResult;
    try {
      res = await this.fetchFn(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${this.accessToken}`,
        },
        body: JSON.stringify(message),
      });
    } catch (err) {
      this.lastError = err instanceof Error ? err.message : String(err);
      throw err;
    }

    this.lastSendAt = Date.now();

    if (!res.ok) {
      this.lastError = `WhatsApp API returned ${res.status}`;
      throw new Error(this.lastError);
    }

    this.lastError = null;
  }

  /**
   * Verifica o handshake de webhook da Meta (GET inicial de verificação).
   *
   * @param mode      Valor de `hub.mode` da query string
   * @param token     Valor de `hub.verify_token` da query string
   * @param challenge Valor de `hub.challenge` da query string
   * @returns O `challenge` string se verificação OK, `null` caso contrário
   */
  verifyWebhook(mode: string, token: string, challenge: string): string | null {
    if (mode === "subscribe" && token === this.verifyToken) {
      return challenge;
    }
    return null;
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
