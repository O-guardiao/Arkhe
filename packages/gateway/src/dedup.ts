/**
 * Deduplicação de mensagens via LRU cache com TTL.
 * Evita processamento duplicado de updates quando o webhook é chamado múltiplas vezes.
 */

interface CacheEntry {
  expiresAt: number;
}

export class MessageDedup {
  private readonly cache = new Map<string, CacheEntry>();
  private readonly maxSize: number;
  private readonly ttlMs: number;
  private cleanupHandle: ReturnType<typeof setInterval> | null = null;

  constructor(maxSize = 1_000, ttlMs = 60_000) {
    this.maxSize = maxSize;
    this.ttlMs = ttlMs;

    // Limpeza periódica de entradas expiradas (a cada TTL)
    this.cleanupHandle = setInterval(() => this.cleanup(), ttlMs);
    // Não bloqueia o process se for o único ref
    this.cleanupHandle.unref?.();
  }

  /** Verifica se o ID já foi visto e não expirou */
  isDuplicate(id: string): boolean {
    const entry = this.cache.get(id);
    if (!entry) return false;
    if (Date.now() > entry.expiresAt) {
      this.cache.delete(id);
      return false;
    }
    return true;
  }

  /**
   * Verifica se o ID já foi visto E o registra caso seja novo.
   * @returns true se for duplicata (já visto), false se for novo (e agora será marcado)
   */
  seen(id: string): boolean {
    if (this.isDuplicate(id)) return true;
    this.markSeen(id);
    return false;
  }

  /** Registra um ID como visto */
  markSeen(id: string): void {
    // Se atingiu o limite, remove a entrada mais antiga (primeiro item do Map)
    if (this.cache.size >= this.maxSize) {
      const firstKey = this.cache.keys().next().value;
      if (firstKey !== undefined) {
        this.cache.delete(firstKey);
      }
    }
    this.cache.set(id, { expiresAt: Date.now() + this.ttlMs });
  }

  /** Remove entradas expiradas */
  cleanup(): void {
    const now = Date.now();
    for (const [key, entry] of this.cache) {
      if (now > entry.expiresAt) {
        this.cache.delete(key);
      }
    }
  }

  /** Para o cleanup periódico */
  destroy(): void {
    if (this.cleanupHandle !== null) {
      clearInterval(this.cleanupHandle);
      this.cleanupHandle = null;
    }
  }

  get size(): number {
    return this.cache.size;
  }
}
