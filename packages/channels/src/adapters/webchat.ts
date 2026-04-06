/**
 * Webchat Channel Adapter (Server-Sent Events).
 *
 * Mantém um mapa em memória de conexões SSE abertas (external_id → writer).
 * O servidor HTTP externo (ex.: Hono) é responsável por criar e encerrar
 * as conexões SSE; este adapter apenas gerencia quem está conectado e
 * empurra mensagens para o stream correto.
 *
 * Fluxo típico de integração:
 *   1. Cliente abre `GET /sse/:userId`  → servidor chama `adapter.onConnection(userId, sseWriteFn)`
 *   2. Brain chama `adapter.send(envelope)` → mensagem chega ao cliente via SSE
 *   3. Cliente fecha a aba / perde conexão → servidor chama `adapter.onDisconnection(userId)`
 */

import type { ChannelAdapter, ChannelHealth, OutboundEnvelope } from "../types.js";

// ---------------------------------------------------------------------------
// Tipos internos
// ---------------------------------------------------------------------------

/**
 * Função de escrita fornecida pelo servidor HTTP para um cliente SSE específico.
 * Recebe dados já formatados como `data: ...\n\n`.
 */
export type SseWriter = (data: string) => void;

// ---------------------------------------------------------------------------
// Opções de configuração
// ---------------------------------------------------------------------------

export interface WebchatAdapterOptions {
  /**
   * URL base do servidor de webchat (usada para compor o `id` do adapter).
   * Ex.: `"https://chat.arkhe.dev"`.
   */
  base_url: string;
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

export class WebchatAdapter implements ChannelAdapter {
  readonly id: string;
  readonly type = "webchat" as const;

  /** Mapa de conexões SSE ativas: external_id → função de escrita. */
  private readonly connections = new Map<string, SseWriter>();

  private connected = false;
  private lastError: string | null = null;

  constructor(options: WebchatAdapterOptions) {
    this.id = `webchat:${options.base_url}`;
  }

  async connect(): Promise<void> {
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connections.clear();
    this.connected = false;
  }

  /**
   * Registra uma nova conexão SSE.
   * Deve ser chamado pelo servidor HTTP quando um cliente abre o stream.
   *
   * @param external_id Identificador do cliente (user ID, session ID…)
   * @param writer      Função de escrita no stream SSE
   */
  onConnection(external_id: string, writer: SseWriter): void {
    this.connections.set(external_id, writer);
  }

  /**
   * Remove uma conexão SSE encerrada.
   * Deve ser chamado pelo servidor HTTP quando o cliente fecha o stream.
   *
   * @param external_id Identificador do cliente a remover
   */
  onDisconnection(external_id: string): void {
    this.connections.delete(external_id);
  }

  async send(envelope: OutboundEnvelope): Promise<void> {
    const writer = this.connections.get(envelope.target_external_id);

    if (writer === undefined) {
      const msg = `No active SSE connection for external_id: ${envelope.target_external_id}`;
      this.lastError = msg;
      throw new Error(msg);
    }

    // Serializa o payload no formato SSE: `data: <json>\n\n`
    const payload: Record<string, string> = { text: envelope.text };
    if (envelope.media_url !== undefined) {
      payload["media_url"] = envelope.media_url;
    }

    try {
      writer(`data: ${JSON.stringify(payload)}\n\n`);
      this.lastError = null;
    } catch (err) {
      this.lastError = err instanceof Error ? err.message : String(err);
      throw err;
    }
  }

  health(): ChannelHealth {
    const activeCxns = this.connections.size;
    const base: ChannelHealth = {
      status: this.connected ? "up" : "down",
      latency_ms: 0,
    };

    if (this.lastError !== null) {
      return { ...base, status: "degraded", last_error: this.lastError };
    }

    // Degradado se não há conexões ativas (mas adaptador está online)
    if (this.connected && activeCxns === 0) {
      return { ...base, status: "degraded" };
    }

    return base;
  }

  /** Retorna o número de conexões SSE ativas no momento. */
  activeConnections(): number {
    return this.connections.size;
  }
}
