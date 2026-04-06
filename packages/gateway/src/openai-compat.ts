/**
 * OpenAI-Compatible API Layer — POST /v1/chat/completions
 *
 * Analogia Python: rlm/server/openai_compat.py
 *
 * Qualquer cliente que usa a API OpenAI (LangChain, Cursor, n8n, litellm,
 * openai SDK) fala com o RLM Brain sem mudança de código — só troca a base_url.
 *
 * Diferença chave:
 *   - OpenAI real: repassa para um LLM a cada turno
 *   - RLM: o "chat" é continuação de sessão multi-turno com memória vetorial,
 *     tools, e decay temporal. O histórico não é passado pela request — está
 *     na sessão RLM. Apenas messages[-1].content é usado como prompt.
 *
 * Mapeamento de campos:
 *   OpenAI messages[-1].content → prompt para o brain
 *   OpenAI model                → sobrescreve RLM_MODEL (opcional, ignorado)
 *   OpenAI user                 → client_id (sessão RLM)
 *   OpenAI stream               → true = SSE chunked, false = JSON completo
 *   OpenAI max_tokens           → ignorado (RLM tem max_iterations)
 *   OpenAI temperature          → ignorado (configurado no Brain)
 *
 * Autenticação:
 *   Authorization: Bearer {RLM_API_TOKEN}
 *   Endpoint desabilitado se RLM_API_TOKEN não configurado.
 *
 * Exemplo JS:
 *   import OpenAI from "openai";
 *   const client = new OpenAI({ baseURL: "http://localhost:3000/v1", apiKey: "token" });
 *   const res = await client.chat.completions.create({
 *     model: "rlm",
 *     messages: [{ role: "user", content: "liste as vendas de hoje" }],
 *   });
 */

import type { Context } from "hono";
import { Hono } from "hono";
import { stream } from "hono/streaming";
import { z } from "zod";
import { childLogger } from "./logger.js";
import { newEnvelope } from "./envelope.js";
import type { ChannelRegistry } from "./registry.js";
import type { WsBridge } from "./ws-bridge.js";
import type { Envelope } from "./envelope.js";

const log = childLogger({ component: "openai-compat" });

const RLM_MODEL_ID = "rlm";

// ---------------------------------------------------------------------------
// Schemas OpenAI (subconjunto mínimo)
// ---------------------------------------------------------------------------

const ChatMessageSchema = z.object({
  role: z.enum(["system", "user", "assistant", "tool"]),
  content: z.union([z.string(), z.array(z.unknown())]).nullable().default(""),
  name: z.string().optional(),
});

const ChatCompletionRequestSchema = z.object({
  model: z.string().default(RLM_MODEL_ID),
  messages: z.array(ChatMessageSchema).min(1),
  stream: z.boolean().default(false),
  user: z.string().optional(),       // → client_id no RLM
  max_tokens: z.number().optional(), // ignorado
  temperature: z.number().optional(), // ignorado
  n: z.number().default(1),
});

type ChatCompletionRequest = z.infer<typeof ChatCompletionRequestSchema>;

// ---------------------------------------------------------------------------
// Builders de resposta no formato OpenAI
// ---------------------------------------------------------------------------

function makeCompletionResponse(
  runId: string,
  model: string,
  content: string,
  finishReason = "stop",
): object {
  return {
    id: `chatcmpl-${runId}`,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [
      {
        index: 0,
        message: { role: "assistant", content },
        finish_reason: finishReason,
      },
    ],
    // RLM não contabiliza tokens — retorna -1 como placeholder
    usage: { prompt_tokens: -1, completion_tokens: -1, total_tokens: -1 },
  };
}

function makeChunkResponse(
  runId: string,
  model: string,
  delta: string,
  finishReason: string | null = null,
): object {
  return {
    id: `chatcmpl-${runId}`,
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [
      {
        index: 0,
        delta: finishReason ? {} : { role: "assistant", content: delta },
        finish_reason: finishReason,
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

function validateApiToken(c: Context, expected: string): boolean {
  const auth = c.req.header("authorization");
  if (!auth?.startsWith("Bearer ")) return false;
  const provided = auth.slice(7);
  // Timing-safe
  const a = Buffer.from(provided.padEnd(256, "\0"));
  const b = Buffer.from(expected.padEnd(256, "\0"));
  return a.length === b.length && a.equals(b);
}

// ---------------------------------------------------------------------------
// Reply collector — espera resposta do Brain via WsBridge
// ---------------------------------------------------------------------------

function waitForBrainReply(
  bridge: WsBridge,
  correlationId: string,
  timeoutMs = 60_000,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      unsub();
      reject(new Error("Brain reply timeout"));
    }, timeoutMs);

    const unsub = bridge.onReply((envelope: Envelope) => {
      if (
        envelope.correlation_id === correlationId ||
        envelope.id === correlationId
      ) {
        clearTimeout(timer);
        unsub();
        resolve(envelope.text ?? "");
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Router factory
// ---------------------------------------------------------------------------

export interface OpenAICompatConfig {
  /** Lê RLM_API_TOKEN se não fornecido. Desabilitado se vazio. */
  apiToken?: string;
  /** Timeout de resposta do Brain em ms (default: 60s) */
  brainTimeoutMs?: number;
}

export function createOpenAICompatRouter(
  registry: ChannelRegistry,
  bridge: WsBridge,
  config: OpenAICompatConfig = {},
): Hono {
  const apiToken = config.apiToken ?? process.env["RLM_API_TOKEN"] ?? "";
  const brainTimeout = config.brainTimeoutMs ?? 60_000;

  const router = new Hono();

  // Models list (compatibilidade)
  router.get("/v1/models", (c) => {
    if (apiToken && !validateApiToken(c, apiToken)) {
      return c.json({ error: { message: "Unauthorized", type: "authentication_error" } }, 401);
    }
    return c.json({
      object: "list",
      data: [{ id: RLM_MODEL_ID, object: "model", created: 0, owned_by: "rlm" }],
    });
  });

  // Chat completions
  router.post("/v1/chat/completions", async (c) => {
    // Endpoint desabilitado se token não configurado
    if (!apiToken) {
      return c.json(
        { error: { message: "OpenAI compat endpoint not configured", type: "invalid_request_error" } },
        404,
      );
    }

    if (!validateApiToken(c, apiToken)) {
      return c.json(
        { error: { message: "Invalid API key", type: "authentication_error" } },
        401,
      );
    }

    let req: ChatCompletionRequest;
    try {
      const raw = await c.req.json() as unknown;
      req = ChatCompletionRequestSchema.parse(raw);
    } catch {
      return c.json(
        { error: { message: "Invalid request body", type: "invalid_request_error" } },
        400,
      );
    }

    // Extrai último conteúdo de usuário como prompt
    const lastUserMsg = [...req.messages].reverse().find((m) => m.role === "user");
    const prompt = typeof lastUserMsg?.content === "string"
      ? lastUserMsg.content
      : JSON.stringify(lastUserMsg?.content ?? "");

    if (!prompt.trim()) {
      return c.json(
        { error: { message: "No user message found", type: "invalid_request_error" } },
        400,
      );
    }

    const clientId = req.user ?? "openai_compat";
    const runId = crypto.randomUUID().replace(/-/g, "");

    const envelope = newEnvelope({
      source_channel: "api",
      source_id: clientId,
      source_client_id: `api:${clientId}`,
      direction: "inbound",
      message_type: "text",
      text: prompt,
    });

    // Reusa o id do envelope como correlation_id para rastrear a resposta
    const correlationId = envelope.id;
    const forwarded = registry.forwardToBrain(envelope);
    if (!forwarded) {
      return c.json(
        { error: { message: "Brain unavailable", type: "service_unavailable" } },
        503,
      );
    }

    log.info({ clientId, promptLen: prompt.length, stream: req.stream }, "OpenAI compat request");

    if (req.stream) {
      // SSE streaming
      return stream(c, async (writer) => {
        try {
          const text = await waitForBrainReply(bridge, correlationId, brainTimeout);

          // Emite o conteúdo completo como um único chunk (Brain não faz streaming ainda)
          const chunk = makeChunkResponse(runId, req.model, text);
          await writer.write(`data: ${JSON.stringify(chunk)}\n\n`);

          // Chunk final
          const done = makeChunkResponse(runId, req.model, "", "stop");
          await writer.write(`data: ${JSON.stringify(done)}\n\n`);
          await writer.write("data: [DONE]\n\n");
        } catch (err) {
          log.error({ err, clientId }, "OpenAI compat stream error");
          const errChunk = { error: { message: String(err), type: "internal_server_error" } };
          await writer.write(`data: ${JSON.stringify(errChunk)}\n\n`);
        }
      });
    }

    // Resposta completa (non-stream)
    try {
      const text = await waitForBrainReply(bridge, correlationId, brainTimeout);
      return c.json(makeCompletionResponse(runId, req.model, text));
    } catch (err) {
      log.error({ err, clientId }, "OpenAI compat timeout/error");
      return c.json(
        { error: { message: "Brain response timeout", type: "internal_server_error" } },
        504,
      );
    }
  });

  return router;
}
