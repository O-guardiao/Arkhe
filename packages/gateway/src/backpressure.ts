/**
 * Semáforo para limitar concorrência de chamadas ao Brain.
 * Evita sobrecarga quando muitos canais enviam mensagens simultaneamente.
 */

export class Semaphore {
  private _available: number;
  private readonly queue: Array<() => void> = [];

  constructor(private readonly limit: number) {
    this._available = limit;
  }

  /** Aguarda até que um slot esteja disponível */
  acquire(): Promise<void> {
    if (this._available > 0) {
      this._available--;
      return Promise.resolve();
    }

    return new Promise<void>((resolve) => {
      this.queue.push(resolve);
    });
  }

  /** Libera um slot */
  release(): void {
    const next = this.queue.shift();
    if (next) {
      // Outro waiter estava na fila — passa o slot diretamente
      next();
    } else {
      this._available++;
    }
  }

  /** Slots disponíveis atualmente */
  get available(): number {
    return this._available;
  }

  /** Requests aguardando um slot */
  get pending(): number {
    return this.queue.length;
  }

  /**
   * Executa uma função com o semáforo adquirido,
   * garantindo release mesmo em caso de erro.
   */
  async run<T>(fn: () => Promise<T>): Promise<T> {
    await this.acquire();
    try {
      return await fn();
    } finally {
      this.release();
    }
  }
}
