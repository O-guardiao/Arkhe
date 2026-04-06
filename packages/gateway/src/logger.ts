/**
 * Logger pino — singleton com contexto de gateway.
 */

import pino from "pino";

export const logger = pino({
  level: process.env["LOG_LEVEL"] ?? "info",
  formatters: {
    level(label) {
      return { level: label };
    },
  },
  timestamp: pino.stdTimeFunctions.isoTime,
  base: { service: "arkhe-gateway" },
});

/**
 * Cria um child logger com contexto adicional.
 *
 * @example
 * ```ts
 * const log = childLogger({ channel: "telegram", envelope_id: "abc123" });
 * log.info("Mensagem recebida");
 * ```
 */
export function childLogger(context: Record<string, unknown>): pino.Logger {
  return logger.child(context);
}
