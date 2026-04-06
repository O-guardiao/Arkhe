/**
 * Backoff exponencial com jitter para reconnects e retries.
 * Baseado no padrão backoff.py do rlm/server/ — reimplementado em TypeScript.
 */

export interface BackoffOptions {
  /** Delay inicial em ms */
  initialMs: number;
  /** Delay máximo em ms */
  maxMs: number;
  /** Multiplicador por tentativa (default: 2.0) */
  multiplier: number;
  /** Adiciona jitter aleatório (±20%) para evitar thundering herd */
  jitter: boolean;
}

const DEFAULT_OPTS: BackoffOptions = {
  initialMs: 1_000,
  maxMs: 30_000,
  multiplier: 2.0,
  jitter: true,
};

export class ExponentialBackoff {
  private readonly opts: BackoffOptions;
  private attempt = 0;

  constructor(opts: Partial<BackoffOptions> = {}) {
    this.opts = { ...DEFAULT_OPTS, ...opts };
  }

  /** Retorna o delay em ms para a próxima tentativa e incrementa o contador */
  next(): number {
    const base = Math.min(
      this.opts.initialMs * Math.pow(this.opts.multiplier, this.attempt),
      this.opts.maxMs
    );
    this.attempt++;

    if (!this.opts.jitter) return base;

    // Jitter ±20%
    const jitterRange = base * 0.2;
    return Math.floor(base + (Math.random() * 2 - 1) * jitterRange);
  }

  /** Reseta o contador de tentativas */
  reset(): void {
    this.attempt = 0;
  }

  get currentAttempt(): number {
    return this.attempt;
  }
}

/**
 * Executa uma função com backoff exponencial em caso de erro.
 *
 * @param fn Função assíncrona a executar
 * @param maxAttempts Número máximo de tentativas (default: 5)
 * @param opts Opções de backoff
 */
export async function withBackoff<T>(
  fn: () => Promise<T>,
  maxAttempts = 5,
  opts: Partial<BackoffOptions> = {}
): Promise<T> {
  const backoff = new ExponentialBackoff(opts);
  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (attempt === maxAttempts) break;

      const delay = backoff.next();
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }

  throw lastError;
}
