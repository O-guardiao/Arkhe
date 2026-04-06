/**
 * Envelope — Unidade de transferência de mensagem entre canais externos e o Brain Python.
 * Definição Zod validada contra schemas/envelope.v1.json (Source of Truth).
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Schema Zod — espelha exatamente envelope.v1.json
// ---------------------------------------------------------------------------

export const EnvelopeSchema = z.object({
  /** UUID hex 32 chars (sem hífens) */
  id: z.string().regex(/^[0-9a-f]{32}$/, "must be 32-char hex UUID without dashes"),

  /** ID da mensagem que originou esta (rastreio de conversa) */
  correlation_id: z.string().nullable().default(null),

  /** ID da mensagem à qual esta é uma resposta direta */
  reply_to_id: z.string().nullable().default(null),

  /** Canal de origem: telegram | discord | slack | whatsapp | webchat | api | internal */
  source_channel: z.enum(["telegram", "discord", "slack", "whatsapp", "webchat", "api", "internal"]),

  /** Identificador do usuário/entidade no canal de origem */
  source_id: z.string().min(1),

  /** ID composto padronizado: {channel}:{source_id}  ex: "telegram:123456789" */
  source_client_id: z.string().regex(/^[a-z]+:.+$/),

  /** Canal de destino para mensagens outbound */
  target_channel: z.string().nullable().default(null),

  /** ID do destinatário no canal de destino */
  target_id: z.string().nullable().default(null),

  /** ID composto do destinatário: {channel}:{id} */
  target_client_id: z.string().nullable().default(null),

  /** Direção do fluxo */
  direction: z.enum(["inbound", "outbound", "internal"]),

  /** Tipo semântico da mensagem */
  message_type: z
    .enum(["text", "image", "audio", "video", "document", "location", "command", "event", "action", "system"])
    .default("text"),

  /** Conteúdo textual principal */
  text: z.string().max(65536),

  /** URL do arquivo de mídia */
  media_url: z.string().url().nullable().default(null),

  /** MIME type do arquivo de mídia */
  media_mime: z.string().nullable().default(null),

  /** Dados adicionais específicos do canal */
  metadata: z.record(z.string(), z.unknown()).default({}),

  /** Timestamp ISO 8601 */
  timestamp: z.string().datetime(),

  /** Número de tentativas de entrega realizadas */
  delivery_attempts: z.number().int().min(0).default(0),

  /** Máximo de tentativas de entrega */
  max_retries: z.number().int().min(0).max(10).default(3),

  /** Prioridade: -1 (baixa) | 0 (normal) | 1 (alta) */
  priority: z.union([z.literal(-1), z.literal(0), z.literal(1)]).default(0),
});

// ---------------------------------------------------------------------------
// Tipos TypeScript derivados do schema Zod
// ---------------------------------------------------------------------------

/** Envelope completo e válido */
export type Envelope = z.infer<typeof EnvelopeSchema>;

/** Entrada parcial para criação — campos opcionais têm defaults */
export type EnvelopeInput = z.input<typeof EnvelopeSchema>;

/** Canais suportados */
export type SupportedChannel = Envelope["source_channel"];

/** Direção da mensagem */
export type MessageDirection = Envelope["direction"];

/** Tipo semântico da mensagem */
export type MessageType = Envelope["message_type"];

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

/**
 * Cria um novo Envelope inbound com ID e timestamp gerados automaticamente.
 *
 * @example
 * ```ts
 * const env = newEnvelope({
 *   source_channel: "telegram",
 *   source_id: "123456789",
 *   text: "Olá!",
 *   direction: "inbound",
 * });
 * ```
 */
export function newEnvelope(
  partial: Omit<EnvelopeInput, "id" | "timestamp" | "source_client_id"> & {
    source_client_id?: string;
  }
): Envelope {
  const id = crypto.randomUUID().replace(/-/g, "");
  const source_client_id = partial.source_client_id ?? `${partial.source_channel}:${partial.source_id}`;

  const raw: EnvelopeInput = {
    ...partial,
    id,
    source_client_id,
    timestamp: new Date().toISOString(),
  };

  return EnvelopeSchema.parse(raw);
}

/**
 * Cria um Envelope de resposta (outbound) baseado em um Envelope inbound.
 * Inverte source ↔ target e muda direction para "outbound".
 */
export function replyEnvelope(
  inbound: Envelope,
  text: string,
  overrides?: Partial<EnvelopeInput>
): Envelope {
  return newEnvelope({
    correlation_id: inbound.id,
    reply_to_id: inbound.id,
    source_channel: "internal",
    source_id: "brain",
    target_channel: inbound.source_channel,
    target_id: inbound.source_id,
    target_client_id: inbound.source_client_id,
    direction: "outbound",
    message_type: "text",
    text,
    metadata: { original_envelope_id: inbound.id },
    ...overrides,
  });
}

/**
 * Valida e parseia um objeto desconhecido como Envelope.
 * Retorna o Envelope validado ou lança ZodError.
 */
export function parseEnvelope(raw: unknown): Envelope {
  return EnvelopeSchema.parse(raw);
}

/**
 * Valida sem lançar exceção — retorna success/error.
 */
export function safeParseEnvelope(raw: unknown): z.SafeParseReturnType<EnvelopeInput, Envelope> {
  return EnvelopeSchema.safeParse(raw);
}
