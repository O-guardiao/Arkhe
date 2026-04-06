import type { ChannelAdapter, ChannelHealth } from "./types.js";

/**
 * Registro central de channel adapters.
 *
 * Mantém um mapa `id → adapter` e fornece operações em bloco
 * (connect all, disconnect all, health snapshot).
 */
export class ChannelRegistry {
  private readonly adapters = new Map<string, ChannelAdapter>();

  /** Registra (ou substitui) um adapter pelo seu `id`. */
  register(adapter: ChannelAdapter): void {
    this.adapters.set(adapter.id, adapter);
  }

  /** Retorna o adapter com o `id` dado, ou `undefined` se não registrado. */
  get(id: string): ChannelAdapter | undefined {
    return this.adapters.get(id);
  }

  /** Retorna todos os adapters registrados. */
  getAll(): ChannelAdapter[] {
    return Array.from(this.adapters.values());
  }

  /** Snapshot de saúde de todos os adapters, indexado por `id`. */
  healthAll(): Record<string, ChannelHealth> {
    const result: Record<string, ChannelHealth> = {};
    for (const [id, adapter] of this.adapters) {
      result[id] = adapter.health();
    }
    return result;
  }

  /** Conecta todos os adapters em paralelo. */
  async connectAll(): Promise<void> {
    await Promise.all(this.getAll().map((a) => a.connect()));
  }

  /** Desconecta todos os adapters em paralelo. */
  async disconnectAll(): Promise<void> {
    await Promise.all(this.getAll().map((a) => a.disconnect()));
  }
}
