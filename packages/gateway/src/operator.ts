/**
 * Operator Bridge — API REST para o TUI operador do RLM.
 *
 * Analogia Python: rlm/server/operator_bridge.py
 *
 * O TUI operador (workbench) usa esta bridge para:
 *   - Anexar a uma sessão ativa
 *   - Ler eventos/atividade da sessão (polling)
 *   - Enviar prompts diretamente a uma sessão
 *   - Enviar comandos operacionais (pause, cancel, adjust)
 *
 * Endpoints:
 *   POST /operator/session              — cria/anexa sessão
 *   GET  /operator/session/:id/activity — snapshot de eventos da sessão
 *   POST /operator/session/:id/message  — envia prompt ao brain
 *   POST /operator/session/:id/commands — envia comando operacional
 *   GET  /operator/sessions             — lista sessões ativas
 *
 * Autenticação:
 *   Authorization: Bearer {RLM_INTERNAL_TOKEN}
 *   Ou query param: ?token=...
 *
 * Design de entrega cross-channel:
 *   O operator não recebe respostas via WebSocket direto.
 *   O TUI faz polling em /activity a cada ~1s.
 *   O Brain entrega respostas no log de eventos da sessão.
 *   O próximo poll do TUI vê a resposta.
 */

import type { Context } from "hono";
import { Hono } from "hono";
import { z } from "zod";
import { childLogger } from "./logger.js";
import { newEnvelope } from "./envelope.js";
import type { ChannelRegistry } from "./registry.js";
import type { WsBridge } from "./ws-bridge.js";

const log = childLogger({ component: "operator" });

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

const INTERNAL_TOKEN_ENVS = ["RLM_INTERNAL_TOKEN", "RLM_WS_TOKEN", "RLM_API_TOKEN"] as const;

function getInternalToken(): string {
  for (const name of INTERNAL_TOKEN_ENVS) {
    const val = process.env[name];
    if (val) return val;
  }
  return "";
}

function validateOperatorToken(c: Context): boolean {
  const expected = getInternalToken();
  if (!expected) return true; // Sem token configurado = aberto (dev)

  // 1. Authorization: Bearer
  const auth = c.req.header("authorization");
  if (auth?.startsWith("Bearer ")) {
    const provided = auth.slice(7);
    const a = Buffer.from(provided.padEnd(256, "\0"));
    const b = Buffer.from(expected.padEnd(256, "\0"));
    if (a.equals(b)) return true;
  }

  // 2. Query param
  const queryToken = new URL(c.req.url).searchParams.get("token");
  if (queryToken) {
    const a = Buffer.from(queryToken.padEnd(256, "\0"));
    const b = Buffer.from(expected.padEnd(256, "\0"));
    if (a.equals(b)) return true;
  }

  return false;
}

// ---------------------------------------------------------------------------
// In-memory session store para o operator (snapshot cache)
// ---------------------------------------------------------------------------

interface OperatorSession {
  sessionId: string;
  clientId: string;
  attachedAt: number;
  events: OperatorEvent[];
  lastActivityAt: number;
}

export interface OperatorEvent {
  eventId: string;
  type: string;
  payload: Record<string, unknown>;
  ts: number;
}

const MAX_EVENTS_PER_SESSION = 200;

class OperatorSessionStore {
  private sessions = new Map<string, OperatorSession>();

  getOrCreate(clientId: string): OperatorSession {
    // Procura por clientId existente
    for (const s of this.sessions.values()) {
      if (s.clientId === clientId) return s;
    }
    const sessionId = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
    const session: OperatorSession = {
      sessionId,
      clientId,
      attachedAt: Date.now(),
      events: [],
      lastActivityAt: Date.now(),
    };
    this.sessions.set(sessionId, session);
    return session;
  }

  get(sessionId: string): OperatorSession | undefined {
    return this.sessions.get(sessionId);
  }

  all(): OperatorSession[] {
    return [...this.sessions.values()];
  }

  logEvent(sessionId: string, type: string, payload: Record<string, unknown>): void {
    const session = this.sessions.get(sessionId);
    if (!session) return;

    session.events.push({
      eventId: crypto.randomUUID().replace(/-/g, "").slice(0, 16),
      type,
      payload,
      ts: Date.now(),
    });

    // Mantém apenas os últimos N eventos
    if (session.events.length > MAX_EVENTS_PER_SESSION) {
      session.events = session.events.slice(-MAX_EVENTS_PER_SESSION);
    }
    session.lastActivityAt = Date.now();
  }
}

// ---------------------------------------------------------------------------
// Request schemas
// ---------------------------------------------------------------------------

const AttachSessionSchema = z.object({
  client_id: z.string().min(1).max(256),
});

const MessageSchema = z.object({
  text: z.string().min(1),
  client_id: z.string().optional(),
});

const CommandSchema = z.object({
  command_type: z.string().min(1),
  payload: z.record(z.string(), z.unknown()).default({}),
  branch_id: z.number().optional(),
  client_id: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Router factory
// ---------------------------------------------------------------------------

export function createOperatorRouter(
  registry: ChannelRegistry,
  _bridge: WsBridge,
): { router: Hono; store: OperatorSessionStore } {
  const store = new OperatorSessionStore();
  const router = new Hono();

  // Auth middleware
  router.use("*", async (c, next) => {
    if (!validateOperatorToken(c)) {
      return c.json({ error: "Unauthorized" }, 401);
    }
    await next();
  });

  // --------------------------------------------------------------------------
  // POST /operator/session — cria/anexa sessão
  // --------------------------------------------------------------------------
  router.post("/operator/session", async (c) => {
    let body: z.infer<typeof AttachSessionSchema>;
    try {
      body = AttachSessionSchema.parse(await c.req.json());
    } catch {
      return c.json({ error: "Invalid body" }, 400);
    }

    const session = store.getOrCreate(body.client_id);
    log.info({ clientId: body.client_id, sessionId: session.sessionId }, "Operator session attached");

    return c.json({
      session_id: session.sessionId,
      client_id: session.clientId,
      attached_at: session.attachedAt,
      event_count: session.events.length,
      activity_url: `/operator/session/${session.sessionId}/activity`,
      message_url: `/operator/session/${session.sessionId}/message`,
      commands_url: `/operator/session/${session.sessionId}/commands`,
    });
  });

  // --------------------------------------------------------------------------
  // GET /operator/session/:id/activity — snapshot de eventos
  // --------------------------------------------------------------------------
  router.get("/operator/session/:id/activity", (c) => {
    const sessionId = c.req.param("id");
    const session = store.get(sessionId);
    if (!session) return c.json({ error: "Session not found" }, 404);

    const sinceParam = c.req.query("since");
    const since = sinceParam ? parseInt(sinceParam, 10) : 0;
    const events = since > 0
      ? session.events.filter((e) => e.ts > since)
      : session.events.slice(-50); // últimos 50 se sem filtro

    return c.json({
      session_id: session.sessionId,
      client_id: session.clientId,
      last_activity_at: session.lastActivityAt,
      events,
    });
  });

  // --------------------------------------------------------------------------
  // POST /operator/session/:id/message — envia prompt ao brain
  // --------------------------------------------------------------------------
  router.post("/operator/session/:id/message", async (c) => {
    const sessionId = c.req.param("id");
    const session = store.get(sessionId);
    if (!session) return c.json({ error: "Session not found" }, 404);

    let body: z.infer<typeof MessageSchema>;
    try {
      body = MessageSchema.parse(await c.req.json());
    } catch {
      return c.json({ error: "Invalid body" }, 400);
    }

    const clientId = body.client_id ?? session.clientId;
    const envelope = newEnvelope({
      source_channel: "internal",
      source_id: clientId,
      source_client_id: `tui:${clientId}`,
      direction: "inbound",
      message_type: "text",
      text: body.text,
      metadata: { routing_key: clientId },
    });

    const forwarded = registry.forwardToBrain(envelope);
    if (!forwarded) return c.json({ error: "Brain unavailable" }, 503);

    store.logEvent(sessionId, "operator_message_sent", {
      text_preview: body.text.slice(0, 200),
      envelope_id: envelope.id,
    });

    log.info({ sessionId, clientId, textLen: body.text.length }, "Operator message sent to brain");
    return c.json({ ok: true, envelope_id: envelope.id });
  });

  // --------------------------------------------------------------------------
  // POST /operator/session/:id/commands — envia comando operacional
  // --------------------------------------------------------------------------
  router.post("/operator/session/:id/commands", async (c) => {
    const sessionId = c.req.param("id");
    const session = store.get(sessionId);
    if (!session) return c.json({ error: "Session not found" }, 404);

    let body: z.infer<typeof CommandSchema>;
    try {
      body = CommandSchema.parse(await c.req.json());
    } catch {
      return c.json({ error: "Invalid body" }, 400);
    }

    const clientId = body.client_id ?? session.clientId;

    // Encaminha como envelope de tipo "command"
    const envelope = newEnvelope({
      source_channel: "internal",
      source_id: clientId,
      source_client_id: `tui:${clientId}`,
      direction: "internal",
      message_type: "command",
      text: "",
      metadata: {
        routing_key: clientId,
        command_type: body.command_type,
        command_payload: body.payload,
        branch_id: body.branch_id,
      },
    });

    const forwarded = registry.forwardToBrain(envelope);
    if (!forwarded) return c.json({ error: "Brain unavailable" }, 503);

    store.logEvent(sessionId, "operator_command_sent", {
      command_type: body.command_type,
      envelope_id: envelope.id,
    });

    log.info({ sessionId, clientId, commandType: body.command_type }, "Operator command sent");
    return c.json({ ok: true, envelope_id: envelope.id });
  });

  // --------------------------------------------------------------------------
  // GET /operator/sessions — lista sessões ativas
  // --------------------------------------------------------------------------
  router.get("/operator/sessions", (c) => {
    const sessions = store.all().map((s) => ({
      session_id: s.sessionId,
      client_id: s.clientId,
      attached_at: s.attachedAt,
      last_activity_at: s.lastActivityAt,
      event_count: s.events.length,
    }));
    return c.json({ sessions });
  });

  return { router, store };
}

// ---------------------------------------------------------------------------
// Export store event helper (para outros módulos registrarem eventos)
// ---------------------------------------------------------------------------

export type { OperatorSessionStore };
