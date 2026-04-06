/**
 * WebChat Channel Receiver (inbound) — interface HTTP embeddable
 *
 * Analogia Python: rlm/server/webchat.py
 *
 * Expõe chat web leve para integrar em qualquer página HTML (<iframe> ou JS embed).
 *
 * Rotas:
 *   GET  /webchat                        — serve HTML do widget
 *   POST /webchat/message                — envia mensagem ao brain
 *   GET  /webchat/session/:sessionId/activity — polling de respostas (Server-Sent Events)
 *
 * Design melhora o Python:
 *   - SSE real via Hono `stream()` em vez de polling puro
 *   - Session store em memória com cleanup automático (TTL 2h)
 *   - HTML inline (sem arquivo estático obrigatório)
 */

import { stream } from "hono/streaming";
import { Hono } from "hono";
import { childLogger } from "../logger.js";
import { newEnvelope } from "../envelope.js";
import type { ChannelRegistry } from "../registry.js";
import type { WsBridge } from "../ws-bridge.js";

const log = childLogger({ component: "channel:webchat" });

// ---------------------------------------------------------------------------
// Session store
// ---------------------------------------------------------------------------

interface WebChatSession {
  sessionId: string;
  events: Array<{ ts: number; type: string; text: string }>;
  createdTs: number;
  lastActivity: number;
}

class WebChatSessionStore {
  private sessions = new Map<string, WebChatSession>();
  private readonly ttlMs: number;

  constructor(ttlMs = 2 * 60 * 60 * 1000) {
    this.ttlMs = ttlMs;
    // Cleanup a cada 10 minutos
    const timer = setInterval(() => this.cleanup(), 10 * 60 * 1000);
    timer.unref?.();
  }

  getOrCreate(sessionId: string): WebChatSession {
    const existing = this.sessions.get(sessionId);
    if (existing) {
      existing.lastActivity = Date.now();
      return existing;
    }
    const session: WebChatSession = {
      sessionId,
      events: [],
      createdTs: Date.now(),
      lastActivity: Date.now(),
    };
    this.sessions.set(sessionId, session);
    return session;
  }

  pushEvent(sessionId: string, type: string, text: string): void {
    const session = this.sessions.get(sessionId);
    if (!session) return;
    session.events.push({ ts: Date.now(), type, text });
    session.lastActivity = Date.now();
    // Mantém apenas os últimos 500 eventos
    if (session.events.length > 500) session.events.splice(0, session.events.length - 500);
  }

  since(sessionId: string, afterTs: number): Array<{ ts: number; type: string; text: string }> {
    const session = this.sessions.get(sessionId);
    if (!session) return [];
    return session.events.filter((e) => e.ts > afterTs);
  }

  private cleanup(): void {
    const cutoff = Date.now() - this.ttlMs;
    for (const [id, session] of this.sessions) {
      if (session.lastActivity < cutoff) {
        this.sessions.delete(id);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Widget HTML inline
// ---------------------------------------------------------------------------

const WEBCHAT_HTML = /* html */ `<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>RLM WebChat</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #0f0f0f; color: #e0e0e0; height: 100dvh; display: flex; flex-direction: column; }
    #msgs { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 8px; }
    .msg { max-width: 80%; padding: 10px 14px; border-radius: 14px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
    .msg.user { align-self: flex-end; background: #3b82f6; color: #fff; }
    .msg.bot  { align-self: flex-start; background: #1e1e1e; color: #e0e0e0; border: 1px solid #333; }
    #form { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #222; }
    #input { flex: 1; background: #1e1e1e; border: 1px solid #333; border-radius: 8px; padding: 10px 14px; color: #e0e0e0; font-size: 14px; outline: none; }
    #send  { background: #3b82f6; color: #fff; border: none; border-radius: 8px; padding: 10px 18px; cursor: pointer; font-size: 14px; }
    #send:hover { background: #2563eb; }
  </style>
</head>
<body>
  <div id="msgs"></div>
  <form id="form">
    <input id="input" type="text" placeholder="Digite sua mensagem..." autocomplete="off" />
    <button id="send" type="submit">Enviar</button>
  </form>
  <script>
    const sid = localStorage.getItem("rlm_sid") || crypto.randomUUID();
    localStorage.setItem("rlm_sid", sid);
    const msgs = document.getElementById("msgs");
    const form = document.getElementById("form");
    const input = document.getElementById("input");
    let lastTs = 0;

    function addMsg(role, text) {
      const el = document.createElement("div");
      el.className = "msg " + role;
      el.textContent = text;
      msgs.appendChild(el);
      msgs.scrollTop = msgs.scrollHeight;
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      addMsg("user", text);
      await fetch("/webchat/message", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ session_id: sid, text })
      });
    });

    async function poll() {
      const r = await fetch("/webchat/session/" + sid + "/activity?after=" + lastTs);
      if (r.ok) {
        const events = await r.json();
        for (const ev of events) {
          if (ev.type === "reply" && ev.text) addMsg("bot", ev.text);
          lastTs = Math.max(lastTs, ev.ts);
        }
      }
      setTimeout(poll, 1500);
    }

    poll();
  </script>
</body>
</html>`;

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createWebChatHandler(registry: ChannelRegistry, bridge: WsBridge): Hono {
  const router = new Hono();
  const store = new WebChatSessionStore();

  // Captura respostas do Brain e empurra para o store
  bridge.onReply((envelope) => {
    const sourceClientId = envelope.source_client_id ?? "";
    if (!sourceClientId.startsWith("webchat:")) return;
    const sessionId = sourceClientId.split(":")[1];
    if (!sessionId) return;

    const text =
      envelope.text !== "" ? envelope.text : "(empty response)";
    store.pushEvent(sessionId, "reply", text);
  });

  // Serve o widget HTML
  router.get("/webchat", (c) => {
    return c.html(WEBCHAT_HTML);
  });

  // Recebe mensagem do usuário
  router.post("/webchat/message", async (c) => {
    let body: { session_id?: string; text?: string };
    try {
      body = await c.req.json() as typeof body;
    } catch {
      return c.json({ error: "bad json" }, 400);
    }

    const sessionId = body.session_id?.trim();
    const text = body.text?.trim();

    if (!sessionId || !text) {
      return c.json({ error: "session_id and text are required" }, 400);
    }

    store.getOrCreate(sessionId);
    store.pushEvent(sessionId, "user", text);

    const envelope = newEnvelope({
      source_channel: "webchat",
      source_id: sessionId,
      source_client_id: `webchat:${sessionId}`,
      direction: "inbound",
      message_type: "text",
      text,
      metadata: { webchat_session_id: sessionId },
    });

    const forwarded = registry.forwardToBrain(envelope);
    if (!forwarded) {
      log.error({ sessionId }, "Brain not available for WebChat message");
      return c.json({ error: "brain unavailable" }, 503);
    }

    log.debug({ sessionId, text: text.slice(0, 80) }, "WebChat message forwarded");
    return c.json({ ok: true });
  });

  // Polling de atividade (chamado a cada ~1.5s pelo frontend)
  router.get("/webchat/session/:sessionId/activity", (c) => {
    const { sessionId } = c.req.param();
    const afterTs = parseInt(c.req.query("after") ?? "0", 10);

    const events = store.since(sessionId, afterTs);
    return c.json(events);
  });

  // SSE stream (alternativa ao polling para clientes que suportam)
  router.get("/webchat/session/:sessionId/stream", (c) => {
    const { sessionId } = c.req.param();
    let lastTs = parseInt(c.req.query("after") ?? "0", 10);

    // Headers SSE precisam ser definidos antes de iniciar o stream
    c.header("Content-Type", "text/event-stream");
    c.header("Cache-Control", "no-cache");

    return stream(c, async (writer) => {
      await writer.write("retry: 1500\n\n");

      const interval = setInterval(async () => {
        const events = store.since(sessionId, lastTs);
        for (const ev of events) {
          await writer.write(`data: ${JSON.stringify(ev)}\n\n`);
          lastTs = Math.max(lastTs, ev.ts);
        }
      }, 1000);

      // Limpa o interval quando o cliente desconectar
      c.req.raw.signal?.addEventListener("abort", () => clearInterval(interval));

      // Mantém a conexão viva
      await new Promise<void>((resolve) => {
        // Wrap em arrow function para compatibilidade com EventListener
        c.req.raw.signal?.addEventListener("abort", () => resolve());
        // Força encerramento após 5 minutos
        setTimeout(resolve, 5 * 60_000);
      });

      clearInterval(interval);
    });
  });

  return router;
}
