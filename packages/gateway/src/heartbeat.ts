/**
 * Heartbeat generalizado para todos os canais.
 *
 * Problema real:
 *   O padrão "keep typing" existia hardcoded no Telegram (sendChatAction a cada 4s).
 *   WhatsApp, Discord e Slack não tinham feedback visual durante processamento.
 *
 * Solução:
 *   Heartbeat aceita qualquer callable como "ação de heartbeat":
 *   - Funciona com promises e sync alike
 *   - IDisposable — para automaticamente no dispose()
 *   - AbortSignal-aware — cancela se o processamento for abortado
 *
 * Uso:
 *   // Indicador de digitação durante processamento
 *   const hb = new ChannelHeartbeat({
 *     action: () => sendTypingIndicator(chatId),
 *     intervalMs: 4_000,
 *   });
 *   hb.start();
 *   try {
 *     const reply = await brain.prompt(text);
 *   } finally {
 *     hb.dispose();
 *   }
 *
 *   // Com AbortSignal (cancela se o cliente desconectar)
 *   const hb = new ChannelHeartbeat({
 *     action: () => sendTypingIndicator(chatId),
 *     intervalMs: 5_000,
 *     signal: req.signal,
 *   });
 */

import { childLogger } from "./logger.js";

const log = childLogger({ component: "heartbeat" });

// ---------------------------------------------------------------------------
// Tipos públicos
// ---------------------------------------------------------------------------

export interface HeartbeatOptions {
  /** Ação executada a cada intervalo. Pode ser async. */
  action: () => void | Promise<void>;
  /** Intervalo em ms entre execuções (default: 4000) */
  intervalMs?: number;
  /** AbortSignal para cancelamento externo */
  signal?: AbortSignal;
}

// ---------------------------------------------------------------------------
// ChannelHeartbeat
// ---------------------------------------------------------------------------

/**
 * Heartbeat generalizado — executa uma ação periodicamente enquanto vivo.
 * IDisposable: para no dispose() ou quando o AbortSignal for abortado.
 */
export class ChannelHeartbeat {
  private readonly action: () => void | Promise<void>;
  private readonly intervalMs: number;
  private readonly signal?: AbortSignal;

  private timer: ReturnType<typeof setInterval> | null = null;
  private disposed = false;
  private abortHandler: (() => void) | null = null;

  constructor(options: HeartbeatOptions) {
    this.action = options.action;
    this.intervalMs = options.intervalMs ?? 4_000;
    // exactOptionalPropertyTypes: só atribui se não for undefined
    if (options.signal !== undefined) {
      this.signal = options.signal;
    }
  }

  /** Inicia o heartbeat. Idempotente. */
  start(): this {
    if (this.disposed || this.timer !== null) return this;
    if (this.signal?.aborted) return this;

    // Registra handler de abort antes de iniciar
    if (this.signal) {
      this.abortHandler = () => this.dispose();
      this.signal.addEventListener("abort", this.abortHandler, { once: true });
    }

    this.timer = setInterval(() => {
      void this.tick();
    }, this.intervalMs);

    // Não bloqueia o processo
    if (this.timer.unref) this.timer.unref();

    return this;
  }

  /** Para o heartbeat. Idempotente. */
  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    if (this.abortHandler && this.signal) {
      this.signal.removeEventListener("abort", this.abortHandler);
      this.abortHandler = null;
    }
  }

  /** IDisposable — para e limpa. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.stop();
  }

  private async tick(): Promise<void> {
    if (this.disposed) return;
    try {
      await this.action();
    } catch (err) {
      log.debug({ err }, "Heartbeat action failed — ignorando");
    }
  }
}

// ---------------------------------------------------------------------------
// Helper: executa uma função com heartbeat automático
// ---------------------------------------------------------------------------

/**
 * Executa `fn` enquanto dispara `action` periodicamente como feedback visual.
 * O heartbeat para automaticamente quando `fn` termina (com sucesso ou erro).
 *
 * @example
 * const result = await withHeartbeat(
 *   () => sendTypingIndicator(chatId),
 *   5_000,
 *   () => brain.prompt(text),
 * );
 */
export async function withHeartbeat<T>(
  action: () => void | Promise<void>,
  intervalMs: number,
  fn: () => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  const hb = new ChannelHeartbeat({ action, intervalMs, ...(signal !== undefined ? { signal } : {}) });
  hb.start();
  try {
    return await fn();
  } finally {
    hb.dispose();
  }
}
