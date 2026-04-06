import { z } from "zod";

// ---------------------------------------------------------------------------
// Primitive schemas
// ---------------------------------------------------------------------------

const LogLevelSchema = z
  .enum(["debug", "info", "warn", "error"])
  .describe("Nível mínimo de severidade emitido pelo logger estruturado");

const ChannelTypeSchema = z
  .enum(["telegram", "discord", "slack", "whatsapp", "webchat"])
  .describe("Protocolo / plataforma a que este canal se conecta");

// ---------------------------------------------------------------------------
// Agent config schema
// ---------------------------------------------------------------------------

export const AgentConfigSchema = z
  .object({
    name: z
      .string()
      .min(1)
      .describe("Identificador legível para o agente, ex: 'arkhe-main'"),
    model: z
      .string()
      .min(1)
      .describe("Identificador do modelo LLM, ex: 'gpt-4o'"),
    max_tokens: z
      .number()
      .int()
      .positive()
      .describe("Número máximo de tokens gerados por turno"),
    temperature: z
      .number()
      .min(0)
      .max(2)
      .describe("Temperatura de amostragem em [0, 2]"),
    tools_allowed: z
      .array(z.string().min(1))
      .describe("Lista de nomes de ferramentas que o agente pode invocar"),
    memory_enabled: z
      .boolean()
      .describe("Habilita recuperação de memória episódica para este agente"),
  })
  .strict()
  .describe("Configurações que governam uma instância de agente");

// ---------------------------------------------------------------------------
// Channel config schema
// ---------------------------------------------------------------------------

export const ChannelConfigSchema = z
  .object({
    channel_id: z
      .string()
      .min(1)
      .describe("Identificador único estável para esta instância de canal"),
    channel_type: ChannelTypeSchema.describe(
      "Protocolo / plataforma a que este canal se conecta"
    ),
    enabled: z
      .boolean()
      .describe("Se o adaptador de canal deve ser iniciado no boot"),
    rate_limit_rpm: z
      .number()
      .int()
      .nonnegative()
      .describe(
        "Número máximo de mensagens por minuto aceitas pelo adaptador antes do rate-limit"
      ),
  })
  .strict()
  .describe("Configurações de conectividade e rate-limit por canal");

// ---------------------------------------------------------------------------
// Daemon config schema
// ---------------------------------------------------------------------------

export const DaemonConfigSchema = z
  .object({
    host: z
      .string()
      .min(1)
      .describe("Endereço de bind, ex: '0.0.0.0' ou '127.0.0.1'"),
    port: z
      .number()
      .int()
      .min(1)
      .max(65535)
      .describe("Porta TCP em que o servidor HTTP/WebSocket escuta"),
    ws_path: z
      .string()
      .startsWith("/")
      .describe("Caminho URL para o endpoint WebSocket de entrada dos canais"),
    brain_ws_url: z
      .string()
      .url()
      .describe("URL WebSocket do serviço Python Brain"),
    log_level: LogLevelSchema.describe(
      "Nível mínimo de severidade emitido pelo logger do daemon"
    ),
  })
  .strict()
  .describe("Configurações de rede e transporte do processo daemon gateway");

// ---------------------------------------------------------------------------
// Security config schema
// ---------------------------------------------------------------------------

export const SecurityConfigSchema = z
  .object({
    allowed_origins: z
      .array(z.string().min(1))
      .describe(
        "Origens CORS/WebSocket permitidas; use ['*'] apenas em dev local"
      ),
    require_auth: z
      .boolean()
      .describe(
        "Quando true, toda requisição de entrada precisa de um bearer token válido"
      ),
    secret_rotation_days: z
      .number()
      .int()
      .nonnegative()
      .describe(
        "Dias antes de um segredo compartilhado ser considerado obsoleto; 0 desativa"
      ),
  })
  .strict()
  .describe("Políticas de origin filtering, autenticação e rotação de chave");

// ---------------------------------------------------------------------------
// Root config schema
// ---------------------------------------------------------------------------

export const RlmConfigSchema = z
  .object({
    agent: AgentConfigSchema.describe(
      "Configurações de inferência do agente principal"
    ),
    channels: z
      .array(ChannelConfigSchema)
      .describe("Um item por canal de mensagens ativo"),
    daemon: DaemonConfigSchema.describe(
      "Configuração de rede do daemon gateway"
    ),
    security: SecurityConfigSchema.describe(
      "Políticas de segurança para a superfície da API"
    ),
  })
  .strict()
  .describe("Objeto de configuração raiz do RLM");

// ---------------------------------------------------------------------------
// Inferred types (keep in sync with types.ts)
// ---------------------------------------------------------------------------

export type AgentConfigInput = z.input<typeof AgentConfigSchema>;
export type ChannelConfigInput = z.input<typeof ChannelConfigSchema>;
export type DaemonConfigInput = z.input<typeof DaemonConfigSchema>;
export type SecurityConfigInput = z.input<typeof SecurityConfigSchema>;
export type RlmConfigInput = z.input<typeof RlmConfigSchema>;
