import { newEnvelope, type Envelope, replyEnvelope, type MessageType, type SupportedChannel } from "../../gateway/src/envelope.js";

export interface BridgeLike {
  onReply(handler: (envelope: Envelope) => void): () => void;
}

const SUPPORTED_CHANNELS = new Set<SupportedChannel>([
  "telegram",
  "discord",
  "slack",
  "whatsapp",
  "webchat",
  "api",
  "internal",
]);

const SUPPORTED_MESSAGE_TYPES = new Set<MessageType>([
  "text",
  "image",
  "audio",
  "video",
  "document",
  "location",
  "command",
  "event",
  "action",
  "system",
]);

function normalizeSupportedChannel(value: string): SupportedChannel {
  if (SUPPORTED_CHANNELS.has(value as SupportedChannel)) {
    return value as SupportedChannel;
  }
  return "api";
}

function normalizeMessageType(value: string): MessageType {
  if (SUPPORTED_MESSAGE_TYPES.has(value as MessageType)) {
    return value as MessageType;
  }
  return "text";
}

export function splitClientId(clientId: string): {
  rawPrefix: string;
  sourceChannel: SupportedChannel;
  sourceId: string;
  sourceClientId: string;
} {
  const [rawPrefix, ...rest] = clientId.split(":");
  const sourceId = rest.length > 0 ? rest.join(":") : clientId;
  const normalizedPrefix = normalizeSupportedChannel((rawPrefix || "api").toLowerCase());

  return {
    rawPrefix: (rawPrefix || "api").toLowerCase(),
    sourceChannel: normalizedPrefix,
    sourceId,
    sourceClientId: `${normalizedPrefix}:${sourceId}`,
  };
}

function normalizeText(payload: Record<string, unknown>): string {
  const text = payload["text"] ?? payload["message"] ?? payload["prompt"] ?? "";
  return String(text);
}

export function buildCompatibilityWebhookEnvelope(
  clientId: string,
  payload: Record<string, unknown>,
): Envelope {
  const routing = splitClientId(clientId);
  const metadata: Record<string, unknown> = {
    ...payload,
    from_user: String(payload["from_user"] ?? routing.sourceId),
    original_client_id: clientId,
    original_prefix: routing.rawPrefix,
    routing_key: clientId,
  };

  if (routing.sourceChannel === "api" && routing.rawPrefix !== "api") {
    metadata["original_channel"] = routing.rawPrefix;
  }

  return newEnvelope({
    source_channel: routing.sourceChannel,
    source_id: routing.sourceId,
    source_client_id: routing.sourceClientId,
    direction: "inbound",
    message_type: normalizeMessageType(String(payload["type"] ?? payload["content_type"] ?? "text")),
    text: normalizeText(payload),
    metadata,
  });
}

export function buildCompatibilityReply(targetClientId: string, message: string): Envelope {
  const routing = splitClientId(targetClientId);
  return replyEnvelope(
    newEnvelope({
      source_channel: routing.sourceChannel,
      source_id: routing.sourceId,
      source_client_id: routing.sourceClientId,
      direction: "inbound",
      message_type: "text",
      text: "",
      metadata: { routing_key: targetClientId },
    }),
    message,
    {
      target_channel: routing.sourceChannel,
      target_id: routing.sourceId,
      target_client_id: targetClientId,
    },
  );
}

export async function waitForBrainReply(
  bridge: BridgeLike,
  correlationId: string,
  timeoutMs = 60_000,
): Promise<Envelope> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      unsubscribe();
      reject(new Error("Brain reply timeout"));
    }, timeoutMs);

    const unsubscribe = bridge.onReply((envelope) => {
      if (envelope.correlation_id === correlationId || envelope.id === correlationId) {
        clearTimeout(timeout);
        unsubscribe();
        resolve(envelope);
      }
    });
  });
}