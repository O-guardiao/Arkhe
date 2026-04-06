/**
 * Events WebSocket — endpoint /events para clientes TUI.
 *
 * O TUI operador (packages/cli) conecta via WsEventClient a ws://<gateway>/events
 * para receber eventos em tempo real. Este módulo:
 *
 *  1. Escuta upgrade requests em /events no http.Server
 *  2. Valida token de autenticação (Authorization header ou query param)
 *  3. Registra-se no bridge.onReply() para receber envelopes do Brain
 *  4. Encaminha eventos formatados como RlmEvent JSON ao TUI
 *  5. Mantém keep-alive com pings periódicos
 */

import { WebSocketServer, WebSocket } from "ws";
import type { IncomingMessage } from "node:http";
import type { Duplex } from "node:stream";
import { childLogger } from "./logger.js";
import type { WsBridge } from "./ws-bridge.js";
import type { Envelope } from "./envelope.js";
import type { OperatorSessionStore } from "./operator.js";

const log = childLogger({ component: "events-ws" });

const INTERNAL_TOKEN_ENVS = ["RLM_INTERNAL_TOKEN", "RLM_WS_TOKEN", "RLM_API_TOKEN"] as const;

function getInternalToken(): string {
  for (const name of INTERNAL_TOKEN_ENVS) {
    const val = process.env[name];
    if (val) return val;
  }
  return "";
}

function validateToken(req: IncomingMessage): boolean {
  const expected = getInternalToken();
  if (!expected) return true; // No token = open (dev mode)

  // Authorization: Bearer <token>
  const auth = req.headers["authorization"];
  if (auth?.startsWith("Bearer ")) {
    const provided = auth.slice(7);
    if (provided === expected) return true;
  }

  // Query param ?token=<token>
  try {
    const url = new URL(req.url ?? "", `http://${req.headers.host ?? "localhost"}`);
    const queryToken = url.searchParams.get("token");
    if (queryToken === expected) return true;
  } catch {
    // ignore
  }

  return false;
}

interface RlmEvent {
  type: string;
  ts: string;
  payload: Record<string, unknown>;
}

function envelopeToEvents(envelope: Envelope): RlmEvent[] {
  const ts = envelope.timestamp ?? new Date().toISOString();
  const events: RlmEvent[] = [];

  if (envelope.direction === "outbound") {
    events.push({
      type: "brain.reply",
      ts,
      payload: {
        channel: envelope.target_channel ?? envelope.source_channel,
        text: envelope.text ?? "",
        envelope_id: envelope.id,
        source_client_id: envelope.source_client_id,
        target_client_id: envelope.target_client_id,
        message_type: envelope.message_type,
      },
    });
  } else if (envelope.direction === "inbound") {
    events.push({
      type: "inbound_message",
      ts,
      payload: {
        channel: envelope.source_channel,
        text: envelope.text ?? "",
        envelope_id: envelope.id,
        source_client_id: envelope.source_client_id,
      },
    });
  }

  // Metadata-based events (tool_call, tool_result, llm_latency, etc.)
  const meta = envelope.metadata ?? {};
  if (meta["event_type"]) {
    events.push({
      type: String(meta["event_type"]),
      ts,
      payload: meta as Record<string, unknown>,
    });
  }

  return events;
}

export function attachEventsWebSocket(
  server: ReturnType<typeof import("@hono/node-server").serve>,
  bridge: WsBridge,
  _operatorStore: OperatorSessionStore,
): void {
  const wss = new WebSocketServer({ noServer: true });
  const clients = new Set<WebSocket>();

  // Handle upgrade requests for /events path
  (server as unknown as import("node:http").Server).on(
    "upgrade",
    (req: IncomingMessage, socket: Duplex, head: Buffer) => {
      const pathname = new URL(req.url ?? "", `http://${req.headers.host ?? "localhost"}`).pathname;

      if (pathname !== "/events") {
        // Not our endpoint — let default handling (or 404)
        socket.destroy();
        return;
      }

      if (!validateToken(req)) {
        socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
        socket.destroy();
        return;
      }

      wss.handleUpgrade(req, socket, head, (ws) => {
        wss.emit("connection", ws, req);
      });
    },
  );

  wss.on("connection", (ws: WebSocket) => {
    clients.add(ws);
    log.info({ clients: clients.size }, "TUI events client connected");

    // Send initial connected event
    const connectEvent: RlmEvent = {
      type: "connected",
      ts: new Date().toISOString(),
      payload: { message: "Events stream connected" },
    };
    ws.send(JSON.stringify(connectEvent));

    // Handle client messages (ping/pong)
    ws.on("message", (data) => {
      const text = data.toString("utf8");
      if (text === "pong") return; // Client heartbeat response
    });

    ws.on("close", () => {
      clients.delete(ws);
      log.info({ clients: clients.size }, "TUI events client disconnected");
    });

    ws.on("error", (err) => {
      log.warn({ err }, "TUI events client error");
      clients.delete(ws);
    });
  });

  // Subscribe to Brain replies and broadcast to TUI clients
  bridge.onReply((envelope: Envelope) => {
    if (clients.size === 0) return;

    const events = envelopeToEvents(envelope);
    for (const evt of events) {
      const msg = JSON.stringify(evt);
      for (const client of clients) {
        if (client.readyState === WebSocket.OPEN) {
          client.send(msg);
        }
      }
    }
  });

  // Periodic ping to keep connections alive
  const pingInterval = setInterval(() => {
    for (const client of clients) {
      if (client.readyState === WebSocket.OPEN) {
        client.send("ping");
      }
    }
  }, 30_000);

  // Cleanup on server close
  (server as unknown as import("node:http").Server).on("close", () => {
    clearInterval(pingInterval);
    for (const client of clients) {
      client.close(1000, "server shutdown");
    }
    clients.clear();
  });

  log.info("Events WebSocket endpoint registered at /events");
}
