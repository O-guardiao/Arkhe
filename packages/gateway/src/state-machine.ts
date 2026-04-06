/**
 * GatewayStateMachine — estado do ciclo de vida do gateway.
 *
 * Espelha o WorkerStatus do claw-code (Spawning → TrustRequired → ReadyForPrompt …)
 * adaptado para o contexto do gateway de canais.
 *
 * Estados:
 *   idle         → bootstrap() →      starting
 *   starting     → bridgeConnected() → running
 *   running      → gracefulStop() →   draining
 *   draining     → drained() →        stopped
 *   * (qualquer) → fatalError() →     failed
 */

import { childLogger } from "./logger.js";

const log = childLogger({ component: "state-machine" });

export type GatewayState =
  | "idle"
  | "starting"
  | "running"
  | "draining"
  | "stopped"
  | "failed";

export type TransitionEvent =
  | "bootstrap"
  | "bridgeConnected"
  | "gracefulStop"
  | "drained"
  | "fatalError";

type TransitionMap = Partial<Record<GatewayState, Partial<Record<TransitionEvent, GatewayState>>>>;

const TRANSITIONS: TransitionMap = {
  idle: { bootstrap: "starting" },
  starting: { bridgeConnected: "running", fatalError: "failed" },
  running: { gracefulStop: "draining", fatalError: "failed" },
  draining: { drained: "stopped", fatalError: "failed" },
};

export type StateChangeHandler = (from: GatewayState, to: GatewayState, event: TransitionEvent) => void;

export class GatewayStateMachine {
  private current: GatewayState = "idle";
  private readonly handlers = new Set<StateChangeHandler>();
  private transitionCount = 0;

  get state(): GatewayState {
    return this.current;
  }

  /** Registra callback de transição de estado */
  onTransition(handler: StateChangeHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  /** Dispara um evento de transição */
  dispatch(event: TransitionEvent): GatewayState {
    const allowed = TRANSITIONS[this.current];
    const next = allowed?.[event];

    if (!next) {
      log.warn({ state: this.current, event }, "Invalid transition ignored");
      return this.current;
    }

    const from = this.current;
    this.current = next;
    this.transitionCount++;

    log.info({ from, to: next, event, count: this.transitionCount }, "Gateway state transition");

    for (const handler of this.handlers) {
      try {
        handler(from, next, event);
      } catch (err) {
        log.error({ err }, "State transition handler threw");
      }
    }

    return next;
  }

  is(state: GatewayState): boolean {
    return this.current === state;
  }

  isOneOf(...states: GatewayState[]): boolean {
    return states.includes(this.current);
  }
}
