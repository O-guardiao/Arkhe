/**
 * WsBridge — cliente WebSocket persistente para o Brain Python.
 *
 * Responsabilidades:
 *  - Manter conexão viva com reconexão exponencial
 *  - Serializar/deserializar mensagens do protocolo ws-protocol.v1.json
 *  - Expor sendEnvelope() e onReply() para o registry
 *  - Emitir eventos de saúde (connect/disconnect/error)
 */

import { WebSocket } from "ws";
import { childLogger } from "./logger.js";
import { ExponentialBackoff } from "./backoff.js";
import { parseEnvelope } from "./envelope.js";
import type { Envelope } from "./envelope.js";

const log = childLogger({ component: "ws-bridge" });

// ---------------------------------------------------------------------------
// Tipos de protocolo WS (ws-protocol.v1.json)
// ---------------------------------------------------------------------------

export type BrainReplyHandler = (envelope: Envelope) => void;

export type BridgeStatus = "disconnected" | "connecting" | "connected" | "draining";

export interface BridgeHealthSnapshot {
  status: BridgeStatus;
  reconnectCount: number;
  pendingMessages: number;
  lastConnectedMs: number | undefined;
  lastDisconnectedMs: number | undefined;
}

// ---------------------------------------------------------------------------
// WsBridge
// ---------------------------------------------------------------------------

export class WsBridge {
  private ws: WebSocket | null = null;
  private status: BridgeStatus = "disconnected";
  private backoff: ExponentialBackoff;
  private reconnectCount = 0;
  private replyHandlers = new Set<BrainReplyHandler>();
  private pendingQueue: string[] = [];
  private lastConnectedMs: number | undefined;
  private lastDisconnectedMs: number | undefined;
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  private pingTimer: ReturnType<typeof setInterval> | undefined;
  private destroyed = false;

  /**
   * @param brainWsUrl  URL do endpoint WebSocket do Brain (ex: ws://localhost:8000/ws/gateway)
   * @param brainWsToken  Token de autenticação (env: RLM_GATEWAY_TOKEN). Se presente é
   *                      adicionado como query-param ?token=<valor> na URL de conexão.
   */
  constructor(
    private readonly brainWsUrl: string,
    private readonly brainWsToken?: string,
  ) {
    this.backoff = new ExponentialBackoff({ initialMs: 500, maxMs: 30_000 });
  }

  /** Inicia a bridge — tenta conectar em loop */
  start(): void {
    if (this.destroyed) return;
    this.connect();
  }

  /** Para a bridge graciosamente */
  async stop(): Promise<void> {
    this.destroyed = true;
    this.status = "draining";
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    if (this.ws) {
      this.ws.close(1000, "shutdown");
      await new Promise<void>((resolve) => {
        const t = setTimeout(() => resolve(), 3_000);
        this.ws?.once("close", () => {
          clearTimeout(t);
          resolve();
        });
      });
    }
  }

  /** Envia um envelope ao Brain */
  sendEnvelope(envelope: Envelope): boolean {
    const msg = JSON.stringify({ type: "envelope", data: envelope });
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(msg);
      return true;
    }
    // Enfileira para reenvio na próxima conexão (max 512 msgs)
    if (this.pendingQueue.length < 512) {
      this.pendingQueue.push(msg);
    }
    return false;
  }

  /** Registra handler para respostas do Brain */
  onReply(handler: BrainReplyHandler): () => void {
    this.replyHandlers.add(handler);
    return () => this.replyHandlers.delete(handler);
  }

  getHealth(): BridgeHealthSnapshot {
    return {
      status: this.status,
      reconnectCount: this.reconnectCount,
      pendingMessages: this.pendingQueue.length,
      lastConnectedMs: this.lastConnectedMs,
      lastDisconnectedMs: this.lastDisconnectedMs,
    };
  }

  // ---------------------------------------------------------------------------
  // Internos
  // ---------------------------------------------------------------------------

  private connect(): void {
    if (this.destroyed || this.status === "connected" || this.status === "connecting") return;

    this.status = "connecting";

    // Adiciona token como query-param se configurado
    let connectUrl = this.brainWsUrl;
    if (this.brainWsToken) {
      const separator = connectUrl.includes("?") ? "&" : "?";
      // Encoda o token para evitar injeção de chars especiais na URL
      connectUrl = `${connectUrl}${separator}token=${encodeURIComponent(this.brainWsToken)}`;
    }

    log.info({ url: this.brainWsUrl, attempt: this.reconnectCount }, "Connecting to Brain WS");

    const ws = new WebSocket(connectUrl, {
      handshakeTimeout: 5_000,
    });
    this.ws = ws;

    ws.on("open", () => this.onOpen());
    ws.on("message", (raw) => this.onMessage(raw));
    ws.on("close", (code, reason) => this.onClose(code, reason));
    ws.on("error", (err) => this.onError(err));
    ws.on("pong", () => log.debug("Brain pong received"));
  }

  private onOpen(): void {
    this.status = "connected";
    this.lastConnectedMs = Date.now();
    this.backoff.reset();
    this.reconnectCount++;
    log.info({ reconnects: this.reconnectCount }, "Brain WS connected");

    // Drena fila de pendentes
    const pending = this.pendingQueue.splice(0);
    for (const msg of pending) {
      this.ws?.send(msg);
    }

    // Inicia keep-alive
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.ping();
      }
    }, 20_000);
  }

  private onMessage(raw: Parameters<WebSocket["on"]>[1] extends (data: infer D) => void ? D : never): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw.toString());
    } catch {
      log.warn({ raw: raw.toString().slice(0, 200) }, "Malformed Brain message");
      return;
    }

    const msg = parsed as { type?: string; data?: unknown };

    if (msg.type === "envelope" && msg.data) {
      try {
        const envelope = parseEnvelope(msg.data);
        for (const handler of this.replyHandlers) {
          handler(envelope);
        }
      } catch (err) {
        log.warn({ err }, "Invalid envelope from Brain");
      }
      return;
    }

    if (msg.type === "ping") {
      this.ws?.send(JSON.stringify({ type: "pong", ts: Date.now() }));
      return;
    }

    if (msg.type === "error") {
      log.error({ msg }, "Brain reported error");
      return;
    }
  }

  private onClose(code: number, reason: Buffer): void {
    this.status = "disconnected";
    this.lastDisconnectedMs = Date.now();
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.ws = null;
    log.warn({ code, reason: reason.toString() }, "Brain WS closed");
    this.scheduleReconnect();
  }

  private onError(err: Error): void {
    log.error({ err }, "Brain WS error");
    // onClose será disparado em seguida
  }

  private scheduleReconnect(): void {
    if (this.destroyed) return;
    const delay = this.backoff.next();
    log.info({ delayMs: delay }, "Scheduling Brain WS reconnect");
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }
}
