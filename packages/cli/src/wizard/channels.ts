/**
 * Especificações e testes de canais de comunicação (Telegram, Discord, etc.).
 *
 * Migrado de rlm/cli/wizard/channels.py
 */

// ─────────────────────────────────────────────── Tipos

export interface ChannelVarSpec {
  /** Nome da variável de ambiente */
  key: string;
  /** Rótulo para exibição no wizard */
  label: string;
  /** Se verdadeiro, o wizard alerta sobre ausência */
  required: boolean;
}

export interface ChannelSpec {
  name: string;
  id: string;
  vars: ChannelVarSpec[];
  hint: string;
  /** Nome interno da função de teste, se houver */
  testFn?: string;
}

// ─────────────────────────────────────────────── Especificações dos canais

export const CHANNEL_SPECS: ChannelSpec[] = [
  {
    name: "Telegram",
    id: "telegram",
    vars: [
      { key: "TELEGRAM_BOT_TOKEN", label: "Bot Token (do @BotFather)", required: true },
      { key: "TELEGRAM_OWNER_CHAT_ID", label: "Chat ID do dono (para notificações)", required: false },
    ],
    hint: "Converse com @BotFather → /newbot → copie o token",
    testFn: "testTelegramToken",
  },
  {
    name: "Discord",
    id: "discord",
    vars: [
      { key: "DISCORD_BOT_TOKEN", label: "Bot Token", required: false },
      { key: "DISCORD_APP_PUBLIC_KEY", label: "Public Key (Ed25519)", required: true },
      { key: "DISCORD_APP_ID", label: "Application ID", required: true },
    ],
    hint: "Discord Developer Portal → Applications → Bot → Token",
    testFn: "testDiscordToken",
  },
  {
    name: "WhatsApp",
    id: "whatsapp",
    vars: [
      { key: "WHATSAPP_TOKEN", label: "Access Token (Meta Cloud API)", required: true },
      { key: "WHATSAPP_PHONE_ID", label: "Phone Number ID", required: true },
      { key: "WHATSAPP_VERIFY_TOKEN", label: "Webhook Verify Token (defina você)", required: true },
    ],
    hint: "Meta for Developers → Your App → WhatsApp → Configuration",
  },
  {
    name: "Slack",
    id: "slack",
    vars: [
      { key: "SLACK_BOT_TOKEN", label: "Bot User OAuth Token (xoxb-…)", required: true },
      { key: "SLACK_SIGNING_SECRET", label: "Signing Secret", required: true },
    ],
    hint: "Slack API → Your App → OAuth & Permissions + Basic Information",
  },
];

// ─────────────────────────────────────────────── Funções de teste

/**
 * Testa token do Telegram via /getMe.
 * Retorna [ok, mensagem descritiva].
 */
export async function testTelegramToken(token: string): Promise<[boolean, string]> {
  const url = `https://api.telegram.org/bot${encodeURIComponent(token)}/getMe`;
  try {
    const resp = await fetch(url, {
      method: "GET",
      signal: AbortSignal.timeout(10_000),
    });
    if (!resp.ok) {
      return [false, `HTTP ${resp.status}`];
    }
    const data = (await resp.json()) as Record<string, unknown>;
    if (data["ok"]) {
      const result = (data["result"] ?? {}) as Record<string, unknown>;
      const name = String(result["first_name"] ?? "?");
      const uname = String(result["username"] ?? "?");
      return [true, `@${uname} (${name})`];
    }
    return [false, "API retornou ok=false"];
  } catch (err) {
    return [false, String(err)];
  }
}

/**
 * Testa token do Discord via /users/@me.
 * Retorna [ok, mensagem descritiva].
 */
export async function testDiscordToken(token: string): Promise<[boolean, string]> {
  const url = "https://discord.com/api/v10/users/@me";
  try {
    const resp = await fetch(url, {
      method: "GET",
      headers: { Authorization: `Bot ${token}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!resp.ok) {
      return [false, `HTTP ${resp.status}`];
    }
    const data = (await resp.json()) as Record<string, unknown>;
    const uname = String(data["username"] ?? "?");
    return [true, `@${uname}`];
  } catch (err) {
    return [false, String(err)];
  }
}

/** Mapa de funções de teste por nome interno. */
export const CHANNEL_TEST_FNS: Record<
  string,
  (token: string) => Promise<[boolean, string]>
> = {
  testTelegramToken,
  testDiscordToken,
};
