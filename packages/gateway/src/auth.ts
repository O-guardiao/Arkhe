/**
 * Validação de assinaturas de webhooks para cada plataforma.
 * Usa crypto.subtle nativo do Node.js — sem dependências externas para HMAC.
 */

const encoder = new TextEncoder();

// ---------------------------------------------------------------------------
// Telegram
// ---------------------------------------------------------------------------

/**
 * Verifica o header X-Telegram-Bot-Api-Secret-Token do webhook do Telegram.
 * O token enviado deve ser == secret configurado via setWebhook.
 */
export function verifyTelegramWebhook(secretToken: string, headerValue: string | null): boolean {
  if (!headerValue) return false;
  // Comparação em tempo constante via Buffer.compare para evitar timing attacks
  return timingSafeEqual(headerValue, secretToken);
}

// ---------------------------------------------------------------------------
// Slack
// ---------------------------------------------------------------------------

/**
 * Verifica a assinatura HMAC-SHA256 do webhook do Slack.
 * Requer headers: X-Slack-Signature e X-Slack-Request-Timestamp
 *
 * @param signingSecret Slack App Signing Secret
 * @param rawBody Corpo da requisição como string
 * @param signature Header X-Slack-Signature (ex: "v0=abc123...")
 * @param timestamp Header X-Slack-Request-Timestamp (unix seconds)
 */
export async function verifySlackWebhook(
  signingSecret: string,
  rawBody: string,
  signature: string | null,
  timestamp: string | null
): Promise<boolean> {
  if (!signature || !timestamp) return false;

  // Rejeita requests com timestamp > 5 minutos (replay protection)
  const requestTime = parseInt(timestamp, 10);
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - requestTime) > 300) return false;

  const baseString = `v0:${timestamp}:${rawBody}`;
  const expected = `v0=${await hmacSha256Hex(signingSecret, baseString)}`;

  return timingSafeEqual(signature, expected);
}

// ---------------------------------------------------------------------------
// Meta (WhatsApp/Instagram/Facebook)
// ---------------------------------------------------------------------------

/**
 * Verifica a assinatura HMAC-SHA256 do webhook do Meta.
 * Requer header: X-Hub-Signature-256
 *
 * @param appSecret Meta App Secret
 * @param rawBody Corpo da requisição (Buffer ou string)
 * @param signatureHeader Header X-Hub-Signature-256 (ex: "sha256=abc123...")
 */
export async function verifyMetaWebhook(
  appSecret: string,
  rawBody: string | Uint8Array,
  signatureHeader: string | null
): Promise<boolean> {
  if (!signatureHeader?.startsWith("sha256=")) return false;

  const receivedHex = signatureHeader.slice("sha256=".length);
  const body = typeof rawBody === "string" ? rawBody : new TextDecoder().decode(rawBody);
  const expectedHex = await hmacSha256Hex(appSecret, body);

  return timingSafeEqual(receivedHex, expectedHex);
}

// ---------------------------------------------------------------------------
// Discord
// ---------------------------------------------------------------------------

/**
 * Verifica a assinatura Ed25519 do webhook do Discord.
 * Usa Web Crypto API (crypto.subtle) nativo — nenhuma lib externa necessária.
 *
 * @param publicKeyHex Chave pública do Discord App em formato hex
 * @param rawBody Corpo raw da requisição
 * @param signatureHex Header X-Signature-Ed25519
 * @param timestamp Header X-Signature-Timestamp
 */
export async function verifyDiscordWebhook(
  publicKeyHex: string,
  rawBody: string,
  signatureHex: string | null,
  timestamp: string | null
): Promise<boolean> {
  if (!signatureHex || !timestamp) return false;

  try {
    const publicKeyBytes = hexToBytes(publicKeyHex);
    const signatureBytes = hexToBytes(signatureHex);
    const message = encoder.encode(timestamp + rawBody);

    const cryptoKey = await crypto.subtle.importKey(
      "raw",
      publicKeyBytes,
      { name: "Ed25519" },
      false,
      ["verify"]
    );

    return await crypto.subtle.verify("Ed25519", cryptoKey, signatureBytes, message);
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Helpers privados
// ---------------------------------------------------------------------------

export async function hmacSha256Hex(secret: string, message: string): Promise<string> {
  const keyBytes = encoder.encode(secret);
  const msgBytes = encoder.encode(message);

  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );

  const signature = await crypto.subtle.sign("HMAC", key, msgBytes);
  return bytesToHex(new Uint8Array(signature));
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function hexToBytes(hex: string): Uint8Array {
  if (hex.length % 2 !== 0) throw new Error("Invalid hex string length");
  const result = new Uint8Array(hex.length / 2);
  for (let i = 0; i < result.length; i++) {
    result[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return result;
}

/**
 * Comparação de strings em tempo constante — previne timing attacks.
 */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}
