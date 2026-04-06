import type { Context } from "hono";

import { childLogger } from "./logger.js";

const log = childLogger({ component: "python-proxy" });

function buildProxyHeaders(input: Headers): Headers {
  const headers = new Headers(input);
  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length");
  return headers;
}

function bodyAllowed(method: string): boolean {
  return method !== "GET" && method !== "HEAD";
}

export async function proxyToUpstream(c: Context, upstreamBaseUrl: string): Promise<Response> {
  const incomingUrl = new URL(c.req.url);
  const upstreamRoot = new URL(upstreamBaseUrl.endsWith("/") ? upstreamBaseUrl : `${upstreamBaseUrl}/`);
  const targetUrl = new URL(incomingUrl.pathname.replace(/^\//, "") + incomingUrl.search, upstreamRoot);

  try {
    const requestInit: RequestInit = {
      method: c.req.method,
      headers: buildProxyHeaders(c.req.raw.headers),
      redirect: "manual",
    };

    if (bodyAllowed(c.req.method)) {
      requestInit.body = await c.req.raw.arrayBuffer();
    }

    const response = await fetch(targetUrl, requestInit);

    return new Response(response.body, {
      status: response.status,
      headers: response.headers,
    });
  } catch (error) {
    log.error({ error, targetUrl: targetUrl.toString() }, "Upstream proxy request failed");
    return c.json(
      {
        error: "Upstream unavailable",
        upstream: targetUrl.toString(),
      },
      502,
    );
  }
}