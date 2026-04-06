/**
 * State machine por canal.
 *
 * Estados: idle → connecting → connected → reconnecting → disconnected
 *
 * A máquina de estados garante que transições inválidas sejam silenciosamente
 * ignoradas (não lançam exceção), mantendo o estado coerente.
 */

export type ChannelState =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

export type ChannelStateEvent = "connect" | "connected" | "error" | "disconnect";

export type StateChangeListener = (from: ChannelState, to: ChannelState) => void;

// ---------------------------------------------------------------------------
// Tabela de transições
// ---------------------------------------------------------------------------

type TransitionRow = Readonly<Partial<Record<ChannelStateEvent, ChannelState>>>;
type TransitionTable = Readonly<Record<ChannelState, TransitionRow>>;

const TRANSITIONS: TransitionTable = {
  idle: {
    connect: "connecting",
  },
  connecting: {
    connected: "connected",
    error: "disconnected",
    disconnect: "disconnected",
  },
  connected: {
    error: "reconnecting",
    disconnect: "disconnected",
  },
  reconnecting: {
    connect: "connecting",
    connected: "connected",
    error: "disconnected",
    disconnect: "disconnected",
  },
  disconnected: {
    connect: "connecting",
  },
};

// ---------------------------------------------------------------------------
// Classe principal
// ---------------------------------------------------------------------------

export class ChannelStateMachine {
  private state: ChannelState;
  private readonly listeners: StateChangeListener[] = [];

  constructor(initial: ChannelState = "idle") {
    this.state = initial;
  }

  /** Estado atual. */
  current(): ChannelState {
    return this.state;
  }

  /**
   * Dispara uma transição.
   * Transições inválidas para o estado atual são silenciosamente ignoradas.
   */
  transition(event: ChannelStateEvent): void {
    const row: TransitionRow = TRANSITIONS[this.state];
    const next: ChannelState | undefined = row[event];
    if (next === undefined) return;

    const from = this.state;
    this.state = next;
    for (const fn of this.listeners) {
      fn(from, next);
    }
  }

  /**
   * Registra um listener para mudanças de estado.
   * Retorna uma função de cancelamento (unsubscribe).
   */
  onStateChange(listener: StateChangeListener): () => void {
    this.listeners.push(listener);
    return () => {
      const idx = this.listeners.indexOf(listener);
      if (idx !== -1) this.listeners.splice(idx, 1);
    };
  }
}
