/**
 * Indicadores de digitação por canal.
 *
 * Rastreia quais canais estão com indicador de "digitando…" ativo.
 * O indicador é automaticamente encerrado após um timeout configurável
 * (padrão: 5000ms).
 *
 * Uso típico:
 *   const typing = new TypingIndicator();
 *   typing.start("telegram:123");      // inicia (com auto-stop em 5s)
 *   typing.isActive("telegram:123");   // → true
 *   typing.stop("telegram:123");       // para manualmente
 */

export class TypingIndicator {
  /** Mapa de timers ativos: channel_id → handle do timer. */
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();

  /**
   * Inicia o indicador de digitação para um canal.
   *
   * Se já houver um indicador ativo para o canal, o timer anterior é
   * cancelado e um novo é iniciado (re-entrada limpa).
   *
   * @param channel_id  Identificador do canal (ex.: `"telegram:42"`)
   * @param timeout_ms  Duração máxima em milissegundos (padrão: 5000)
   */
  start(channel_id: string, timeout_ms = 5000): void {
    // Cancela timer anterior para evitar acúmulo
    this.stop(channel_id);

    const timer = setTimeout(() => {
      this.timers.delete(channel_id);
    }, timeout_ms);

    this.timers.set(channel_id, timer);
  }

  /**
   * Para o indicador de digitação para um canal imediatamente.
   * Operação idempotente — chamar para canal sem indicador ativo é seguro.
   *
   * @param channel_id Identificador do canal a parar
   */
  stop(channel_id: string): void {
    const timer = this.timers.get(channel_id);
    if (timer !== undefined) {
      clearTimeout(timer);
      this.timers.delete(channel_id);
    }
  }

  /**
   * Verifica se o indicador está ativo para o canal dado.
   *
   * @param channel_id Identificador do canal
   * @returns `true` se o indicador está ativo
   */
  isActive(channel_id: string): boolean {
    return this.timers.has(channel_id);
  }

  /**
   * Retorna a lista de channel_ids com indicador atualmente ativo.
   */
  activeChannels(): string[] {
    return Array.from(this.timers.keys());
  }

  /**
   * Para todos os indicadores ativos de uma vez.
   * Útil para cleanup em shutdown do adapter.
   */
  stopAll(): void {
    for (const [id] of this.timers) {
      this.stop(id);
    }
  }
}
