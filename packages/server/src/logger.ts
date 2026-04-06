import pino from "pino";

export const logger = pino({
  level: process.env["LOG_LEVEL"] ?? "info",
  formatters: {
    level(label) {
      return { level: label };
    },
  },
  timestamp: pino.stdTimeFunctions.isoTime,
  base: { service: "arkhe-server" },
});

export function childLogger(context: Record<string, unknown>): pino.Logger {
  return logger.child(context);
}