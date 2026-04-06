/**
 * Interface base que todos os adapters de canal devem implementar.
 * Desacopla o registry de canais dos adapters concretos.
 */

import type { Envelope } from "../envelope.js";

// ---------------------------------------------------------------------------
// Tipos públicos
// ---------------------------------------------------------------------------

export type ChannelStatus = "healthy" | "degraded" | "down" | "disabled";

export interface ChannelInfo {
  id: string;
  name: string;
  type: string;
  status: ChannelStatus;
  lastSeenMs?: number;
  messagesSent: number;
  messagesReceived: number;
  errors: number;
}

export interface SendResult {
  ok: boolean;
  messageId?: string;
  error?: string;
}

export interface ChannelIdentity {
  botId?: string | number;
  username?: string;
  displayName?: string;
}

export interface ProbeResult {
  ok: boolean;
  elapsedMs: number;
  error?: string;
  identity?: ChannelIdentity;
}

// ---------------------------------------------------------------------------
// Interface do adapter
// ---------------------------------------------------------------------------

/**
 * Interface que todos os adapters de canal devem implementar.
 * Cada adapter sabe como enviar mensagens para seu canal específico.
 */
export interface ChannelAdapter {
  /** Nome canônico do canal (deve ser único no registry) */
  readonly channelName: string;

  /**
   * Envia uma mensagem de texto para o destinatário.
   *
   * @param targetId ID do destinatário neste canal (ex: chat_id no Telegram)
   * @param text Texto a enviar (pode precisar ser dividido se longo)
   * @param envelope Envelope original para contexto
   */
  sendMessage(targetId: string, text: string, envelope: Envelope): Promise<SendResult>;

  /**
   * Envia um arquivo de mídia (opcional — nem todos os canais suportam).
   *
   * @param targetId ID do destinatário
   * @param url URL pública do arquivo
   * @param mime MIME type do arquivo
   * @param caption Legenda opcional
   */
  sendMedia?(
    targetId: string,
    url: string,
    mime: string,
    caption?: string
  ): Promise<SendResult>;

  /**
   * Retorna informações atuais sobre o status do canal.
   */
  getChannelInfo(): ChannelInfo;

  /**
   * Executa um probe sob demanda no canal, quando suportado.
   */
  probe?(timeoutMs?: number): Promise<ProbeResult>;
}
