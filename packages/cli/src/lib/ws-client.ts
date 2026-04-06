/**
 * WsEventClient — cliente WebSocket para o CLI/TUI
 *
 * Conecta ao endpoint `/events` do Gateway e distribui eventos
 * tipados para os handlers registrados.
 *
 * Design:
 *  - Reconexão exponencial automática (não usa a bridge do gateway)
 *  - Emitter simples baseado em Map — sem dependência de EventEmitter
 *  - Suporta heartbeat (pong automático a pings do servidor)
 */

import WebSocket from "ws";

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

export interface RlmEvent {
  type: string;
  /** Timestamp ISO 8601 */
  ts: string;
  /** Payload do evento — varia por tipo */
  payload: Record<string, unknown>;
}

export type EventHandler = (event: RlmEvent) => void;

export type ConnectionStatus = "idle" | "connecting" | "connected" | "disconnected" | "error";

export interface ConnectionState {
  status: ConnectionStatus;
  reconnects: number;
  lastErrorMs?: number;
  lastConnectedMs?: number;
}

// ---------------------------------------------------------------------------
// Constantes
// ---------------------------------------------------------------------------

const BASE_RECONNECT_MS = 500;
const MAX_RECONNECT_MS = 30_000;
const RECONNECT_JITTER_MS = 300;
const PING_TIMEOUT_MS = 60_000; // fecha conexão se nenhum ping em 60s

// ---------------------------------------------------------------------------
// WsEventClient
// ---------------------------------------------------------------------------

export class WsEventClient {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<EventHandler>>();
  private wildcardHandlers = new Set<EventHandler>();
  private statusHandlers = new Set<(state: ConnectionState) => void>();

  private state: ConnectionState = { status: "idle", reconnects: 0 };
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  private pingTimeoutTimer: ReturnType<typeof setTimeout> | undefined;
  private reconnectDelay = BASE_RECONNECT_MS;
  private stopped = false;

  constructor(
    private readonly url: string,
    private readonly token: string,
  ) {}

  // -------------------------------------------------------------------------
  // API pública
  // -------------------------------------------------------------------------

  /** Inicia conexão. Idempotente — ignora se já conectado. */
  connect(): void {
    this.stopped = false;
    if (this.state.status === "connecting" || this.state.status === "connected") return;
    this._doConnect();
  }

  /** Para reconexão e fecha o socket. */
  disconnect(): void {
    this.stopped = true;
    this._clearTimers();
    if (this.ws) {
      this.ws.close(1000, "client disconnect");
      this.ws = null;
    }
    this._setState({ status: "idle", reconnects: 0 });
  }

  /** Retorna `true` se connected. */
  isConnected(): boolean {
    return this.state.status === "connected";
  }

  /** Retorna snapshot do estado atual. */
  getState(): Readonly<ConnectionState> {
    return { ...this.state };
  }

  /**
   * Registra handler para um tipo específico de evento (ex: "brain.reply").
   * Use `"*"` para receber todos os eventos.
   * Retorna função de unsubscribe.
   */
  on(eventType: string, handler: EventHandler): () => void {
    if (eventType === "*") {
      this.wildcardHandlers.add(handler);
      return () => this.wildcardHandlers.delete(handler);
    }
    if (!this.handlers.has(eventType)) {
      this.handlers.set(eventType, new Set());
    }
    this.handlers.get(eventType)!.add(handler);
    return () => this.handlers.get(eventType)?.delete(handler);
  }

  /**
   * Registra listener para mudanças de estado da conexão.
   * Retorna função de unsubscribe.
   */
  onStateChange(handler: (state: ConnectionState) => void): () => void {
    this.statusHandlers.add(handler);
    return () => this.statusHandlers.delete(handler);
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private _doConnect(): void {
    this._setState({ ...this.state, status: "connecting" });

    const wsUrl = this._buildUrl();
    let ws: WebSocket;

    try {
      ws = new WebSocket(wsUrl, {
        headers: { Authorization: `Bearer ${this.token}` },
        handshakeTimeout: 10_000,
      });
    } catch (err) {
      this._setState({ ...this.state, status: "error", lastErrorMs: Date.now() });
      this._scheduleReconnect();
      return;
    }

    this.ws = ws;

    ws.on("open", () => {
      this.reconnectDelay = BASE_RECONNECT_MS;
      this._setState({ ...this.state, status: "connected", lastConnectedMs: Date.now() });
      this._resetPingTimeout();
    });

    ws.on("message", (data: WebSocket.RawData) => {
      this._resetPingTimeout();
      const text = data.toString("utf8");

      // Responde a ping do servidor (protocolo simples)
      if (text === "ping") {
        ws.send("pong");
        return;
      }

      let event: RlmEvent;
      try {
        event = JSON.parse(text) as RlmEvent;
      } catch {
        return; // mensagem não-JSON ignorada
      }

      this._dispatch(event);
    });

    ws.on("close", (code, reason) => {
      this._clearPingTimeout();
      const wasConnected = this.state.status === "connected";
      this._setState({
        ...this.state,
        status: "disconnected",
        reconnects: wasConnected ? this.state.reconnects + 1 : this.state.reconnects,
      });
      this.ws = null;

      if (!this.stopped) {
        this._scheduleReconnect();
      }
    });

    ws.on("error", (err) => {
      this._clearPingTimeout();
      this._setState({ ...this.state, status: "error", lastErrorMs: Date.now() });
      // "error" sempre é seguido de "close" no ws — não reconecta aqui
    });
  }

  private _buildUrl(): string {
    // Converte http→ws, https→wss se necessário
    const base = this.url
      .replace(/^http:\/\//, "ws://")
      .replace(/^https:\/\//, "wss://")
      .replace(/\/+$/, "");
    return `${base}/events`;
  }

  private _dispatch(event: RlmEvent): void {
    const specific = this.handlers.get(event.type);
    if (specific) {
      for (const h of specific) {
        try { h(event); } catch { /* ignora erros em handlers */ }
      }
    }
    for (const h of this.wildcardHandlers) {
      try { h(event); } catch { /* ignora */ }
    }
  }

  private _scheduleReconnect(): void {
    if (this.stopped) return;
    const jitter = Math.random() * RECONNECT_JITTER_MS;
    const delay = Math.min(this.reconnectDelay + jitter, MAX_RECONNECT_MS);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, MAX_RECONNECT_MS);

    this.reconnectTimer = setTimeout(() => {
      if (!this.stopped) this._doConnect();
    }, delay);
  }

  private _setState(next: ConnectionState): void {
    this.state = next;
    for (const h of this.statusHandlers) {
      try { h({ ...next }); } catch { /* ignora */ }
    }
  }

  private _resetPingTimeout(): void {
    this._clearPingTimeout();
    this.pingTimeoutTimer = setTimeout(() => {
      // Sem ping do servidor por 60s → fecha para forçar reconexão
      this.ws?.terminate();
    }, PING_TIMEOUT_MS);
  }

  private _clearPingTimeout(): void {
    if (this.pingTimeoutTimer !== undefined) {
      clearTimeout(this.pingTimeoutTimer);
      this.pingTimeoutTimer = undefined;
    }
  }

  private _clearTimers(): void {
    if (this.reconnectTimer !== undefined) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = undefined;
    }
    this._clearPingTimeout();
  }
}
