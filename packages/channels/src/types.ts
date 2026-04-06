/**
 * Core types partilhados por todos os channel adapters do RLM/Arkhe.
 */

/** Estado operacional de um channel adapter. */
export interface ChannelHealth {
  status: "up" | "down" | "degraded";
  latency_ms: number;
  last_error?: string;
}

/**
 * Envelope de entrada — estrutura mínima que cruza a fronteira do canal
 * em direção ao Brain Python.
 */
export interface InboundEnvelope {
  channel_id: string;
  channel_type: string;
  /** ID externo do remetente (user_id, phone_number, socket_id…) */
  external_id: string;
  text: string;
  media_url?: string;
  /** Unix ms */
  timestamp: number;
}

/**
 * Envelope de saída — estrutura que o Brain emite para ser entregue
 * num canal específico.
 */
export interface OutboundEnvelope {
  channel_id: string;
  channel_type: string;
  /** ID externo do destinatário */
  target_external_id: string;
  text: string;
  media_url?: string;
}

/** Tipos de eventos emitidos pelo ciclo de vida de um canal. */
export type ChannelEventType = "connected" | "disconnected" | "message" | "error";

/** Evento emitido por um adapter ou pela state machine. */
export interface ChannelEvent {
  type: ChannelEventType;
  channel_id: string;
  data: unknown;
}

/**
 * Contrato que todo channel adapter deve implementar.
 * Cada adapter encapsula a lógica de transporte de um canal específico
 * (Discord, Slack, WhatsApp, Webchat…).
 */
export interface ChannelAdapter {
  /** Identificador único dentro do registry (ex.: `discord:123456`). */
  readonly id: string;
  /** Tipo do canal (ex.: `'discord'`). */
  readonly type: string;
  /** Inicializa e valida a conexão com o canal. */
  connect(): Promise<void>;
  /** Encerra a conexão de forma limpa. */
  disconnect(): Promise<void>;
  /** Envia uma mensagem ao destinatário indicado no envelope. */
  send(envelope: OutboundEnvelope): Promise<void>;
  /** Snapshot instantâneo do estado de saúde do adapter. */
  health(): ChannelHealth;
}
