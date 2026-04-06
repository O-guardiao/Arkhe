import type { Context } from "hono";

const INTERNAL_TOKEN_ENVS = ["RLM_INTERNAL_TOKEN", "RLM_WS_TOKEN", "RLM_API_TOKEN"] as const;

function resolvedInternalToken(): string {
  for (const name of INTERNAL_TOKEN_ENVS) {
    const token = process.env[name]?.trim();
    if (token) {
      return token;
    }
  }
  return "";
}

function extractProvidedToken(c: Context): string | null {
  const auth = c.req.header("authorization");
  if (auth?.startsWith("Bearer ")) {
    return auth.slice(7);
  }

  const direct = c.req.header("x-rlm-token") ?? c.req.header("x-internal-token");
  if (direct?.trim()) {
    return direct.trim();
  }

  const queryToken = c.req.query("token");
  if (queryToken?.trim()) {
    return queryToken.trim();
  }

  return null;
}

function timingSafeEquals(a: string | null, b: string): boolean {
  if (!a || !b) {
    return false;
  }

  const left = Buffer.from(a.padEnd(256, "\0"));
  const right = Buffer.from(b.padEnd(256, "\0"));
  return left.equals(right);
}

export function requireInternalToken(c: Context): Response | null {
  const expected = resolvedInternalToken();
  if (!expected) {
    return null;
  }

  const provided = extractProvidedToken(c);
  if (timingSafeEquals(provided, expected)) {
    return null;
  }

  return c.json({ error: "Unauthorized" }, 401);
}