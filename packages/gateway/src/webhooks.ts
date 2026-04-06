/**
 * Webhook Dispatcher — receptor HTTP universal para acionar o Brain de sistemas externos.
 *
 * Analogia Python: rlm/server/webhook_dispatch.py
 *
 * Endpoints:
 *   POST /api/hooks/:token              — disparo simples (texto livre)
 *   POST /api/hooks/:token/:client_id   — disparo para sessão específica
 *
 * Formato do body (todos opcionais):
 *   {
 *     "text":       "faça um relatório de vendas",
 *     "client_id":  "pipeline_vendas",
 *     "session_id": "sess_abc123",
 *     "channel":    "n8n",
 *     "metadata":   {}
 *   }
 *
 * Autenticação:
 *   Token em path: POST /api/hooks/{meu_token}
 *   Header:        X-Hook-Token: meu_token
 *   Bearer:        Authorization: Bearer meu_token
 *
 * Rate limiting:
 *   Sliding window in-memory por IP. Default: 60 req/min.
 *   Bypass via RLM_HOOK_RATE_LIMIT=0.
 *
 * Uso curl:
 *   curl -X POST http://localhost:3000/api/hooks/meu_token \
 *        -H "Content-Type: application/json" \
 *        -d '{"text": "gere relatório de hoje"}'
 */

import type { Context } from "hono";
import { Hono } from "hono";
import { z } from "zod";
import { childLogger } from "./logger.js";
import { newEnvelope } from "./envelope.js";
import type { ChannelRegistry } from "./registry.js";

const log = childLogger({ component: "webhooks" });

const DEFAULT_HOOK_CLIENT_ID = "hook_default";
const DEFAULT_RATE_LIMIT_RPM = 60;
const DEFAULT_MAX_BODY_BYTES = 256_000; // 256 KB

// ---------------------------------------------------------------------------
// Rate Limiter — sliding window in-memory
// ---------------------------------------------------------------------------

interface RateWindow {
  timestamps: number[];
}

export class WebhookRateLimiter {
  private readonly rpm: number;
  private readonly windows = new Map<string, RateWindow>();
  private cleanupTimer: ReturnType<typeof setInterval> | null = null;

  constructor(rpm = DEFAULT_RATE_LIMIT_RPM) {
    this.rpm = rpm;
    if (rpm > 0) {
      // Limpa janelas antigas a cada 2 minutos
      this.cleanupTimer = setInterval(() => this.cleanup(), 120_000);
      this.cleanupTimer.unref?.();
    }
  }

  /**
   * Verifica se a chave está dentro do limite.
   * @returns { allowed: boolean, retryAfterSecs: number }
   */
  check(key: string): { allowed: boolean; retryAfterSecs: number } {
    if (this.rpm <= 0) return { allowed: true, retryAfterSecs: 0 };

    const now = Date.now();
    const windowStart = now - 60_000;

    let win = this.windows.get(key);
    if (!win) {
      win = { timestamps: [] };
      this.windows.set(key, win);
    }

    // Remove timestamps fora da janela de 1 minuto
    win.timestamps = win.timestamps.filter((t) => t > windowStart);

    if (win.timestamps.length >= this.rpm) {
      const oldest = win.timestamps[0] ?? now;
      const retryAfterSecs = Math.ceil((oldest + 60_000 - now) / 1_000) + 1;
      return { allowed: false, retryAfterSecs };
    }

    win.timestamps.push(now);
    return { allowed: true, retryAfterSecs: 0 };
  }

  /** Checa tanto IP quanto client_id (mais restritivo vence). */
  checkDual(
    clientIp: string,
    clientId?: string,
  ): { allowed: boolean; retryAfterSecs: number } {
    const byIp = this.check(`ip:${clientIp}`);
    if (!byIp.allowed) return byIp;
    if (clientId) {
      return this.check(`cid:${clientId}`);
    }
    return { allowed: true, retryAfterSecs: 0 };
  }

  private cleanup(): void {
    const cutoff = Date.now() - 120_000;
    for (const [key, win] of this.windows) {
      if (!win.timestamps.some((t) => t > cutoff)) {
        this.windows.delete(key);
      }
    }
  }

  destroy(): void {
    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer);
      this.cleanupTimer = null;
    }
  }
}

// ---------------------------------------------------------------------------
// Body schema
// ---------------------------------------------------------------------------

const HookBodySchema = z.object({
  text: z.string().max(DEFAULT_MAX_BODY_BYTES / 4).optional().default(""),
  client_id: z.string().min(1).max(256).optional(),
  session_id: z.string().max(256).optional(),
  channel: z.string().min(1).max(64).optional().default("webhook"),
  metadata: z.record(z.string(), z.unknown()).optional().default({}),
});

type HookBody = z.infer<typeof HookBodySchema>;

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------

function extractToken(c: Context, pathToken: string): string | null {
  // 1. Token no path
  if (pathToken && pathToken.length > 0) return pathToken;
  // 2. Header X-Hook-Token
  const ht = c.req.header("x-hook-token");
  if (ht) return ht;
  // 3. Authorization: Bearer <token>
  const auth = c.req.header("authorization");
  if (auth?.startsWith("Bearer ")) return auth.slice(7);
  return null;
}

function validateToken(provided: string | null, expected: string): boolean {
  if (!provided || !expected) return false;
  // Timing-safe comparison
  const a = Buffer.from(provided.padEnd(256, "\0"));
  const b = Buffer.from(expected.padEnd(256, "\0"));
  return a.length === b.length && a.equals(b);
}

// ---------------------------------------------------------------------------
// Webhook dispatch handler factory
// ---------------------------------------------------------------------------

export interface WebhookDispatchConfig {
  /** Lê de RLM_HOOK_TOKEN se não fornecido. Desabilitado se vazio. */
  hookToken?: string;
  /** Default: 60. Set 0 to disable. */
  rateLimitRpm?: number;
}

export function createWebhookRouter(
  registry: ChannelRegistry,
  config: WebhookDispatchConfig = {},
): Hono {
  const hookToken = config.hookToken ?? process.env["RLM_HOOK_TOKEN"] ?? "";
  const rateLimiter = new WebhookRateLimiter(
    config.rateLimitRpm ?? Number(process.env["RLM_HOOK_RATE_LIMIT"] ?? DEFAULT_RATE_LIMIT_RPM),
  );

  const router = new Hono();

  const handle = async (c: Context, pathToken: string, pathClientId?: string) => {
    // Endpoint desabilitado se nenhum token configurado
    if (!hookToken) {
      return c.json({ error: "Webhook endpoint not configured (RLM_HOOK_TOKEN missing)" }, 404);
    }

    // Auth
    const provided = extractToken(c, pathToken);
    if (!validateToken(provided, hookToken)) {
      log.warn({ ip: c.req.header("x-forwarded-for") ?? "unknown" }, "Webhook auth failed");
      return c.json({ error: "Unauthorized" }, 401);
    }

    // Rate limit
    const clientIp = c.req.header("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
    const rateResult = rateLimiter.checkDual(clientIp, pathClientId);
    if (!rateResult.allowed) {
      return c.json(
        { error: "Rate limit exceeded", retry_after: rateResult.retryAfterSecs },
        429,
      );
    }

    // Parse body
    let body: HookBody;
    try {
      const raw = await c.req.json() as unknown;
      body = HookBodySchema.parse(raw);
    } catch {
      return c.json({ error: "Invalid request body" }, 400);
    }

    // Resolve client_id: path > body > default
    const clientId = pathClientId ?? body.client_id ?? DEFAULT_HOOK_CLIENT_ID;
    const channelName = body.channel ?? "webhook";

    const envelope = newEnvelope({
      source_channel: "api",
      source_id: clientId,
      source_client_id: `api:${clientId}`,
      direction: "inbound",
      message_type: "text",
      text: body.text,
      metadata: {
        hook_channel: channelName,
        session_id: body.session_id,
        ...body.metadata,
      },
    });

    const forwarded = registry.forwardToBrain(envelope);
    if (!forwarded) {
      log.error({ clientId }, "Webhook: brain bridge unavailable");
      return c.json({ error: "Brain unavailable" }, 503);
    }

    log.info({ clientId, channel: channelName, textLen: body.text.length }, "Webhook dispatched");
    return c.json({ ok: true, envelope_id: envelope.id });
  };

  // POST /api/hooks/:token
  router.post("/api/hooks/:token", (c) => handle(c, c.req.param("token")));
  // POST /api/hooks/:token/:client_id
  router.post("/api/hooks/:token/:client_id", (c) =>
    handle(c, c.req.param("token"), c.req.param("client_id")),
  );

  return router;
}
