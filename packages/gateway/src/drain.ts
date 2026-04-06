/**
 * Graceful shutdown — aguarda requests in-flight finalizarem antes de encerrar o processo.
 * Usa um contador atômico: cada request chama enter() ao iniciar e leave() ao finalizar.
 */

export class DrainGate {
  private activeCount = 0;
  private draining = false;
  private readonly waiters: Array<() => void> = [];

  /** Registra início de um request in-flight */
  enter(): void {
    if (this.draining) {
      throw new Error("DrainGate is draining — no new requests accepted");
    }
    this.activeCount++;
  }

  /** Registra término de um request in-flight */
  leave(): void {
    this.activeCount--;
    if (this.activeCount < 0) {
      // Invariante violada — nunca deve acontecer em código correto
      this.activeCount = 0;
    }
    if (this.draining && this.activeCount === 0) {
      this.notifyWaiters();
    }
  }

  /**
   * Inicia o drain: recusa novos requests e aguarda os in-flight finalizarem.
   *
   * @param timeoutMs Timeout máximo em ms (0 = sem timeout). Default: 30 segundos.
   */
  drain(timeoutMs = 30_000): Promise<void> {
    this.draining = true;

    if (this.activeCount === 0) {
      return Promise.resolve();
    }

    return new Promise<void>((resolve, reject) => {
      const timer =
        timeoutMs > 0
          ? setTimeout(() => {
              reject(new Error(`DrainGate timed out after ${timeoutMs}ms with ${this.activeCount} active requests`));
            }, timeoutMs)
          : null;

      this.waiters.push(() => {
        if (timer !== null) clearTimeout(timer);
        resolve();
      });
    });
  }

  /** Retorna true enquanto aceita novos requests */
  isOpen(): boolean {
    return !this.draining;
  }

  get active(): number {
    return this.activeCount;
  }

  private notifyWaiters(): void {
    const waiters = this.waiters.splice(0);
    for (const fn of waiters) fn();
  }
}
