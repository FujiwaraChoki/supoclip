import { randomUUID } from "crypto";

import { buildBackendAuthHeaders } from "@/lib/backend-auth";

export const DEFAULT_FORWARDED_HEADERS = [
  "accept-ranges",
  "cache-control",
  "content-length",
  "content-range",
  "content-disposition",
  "content-type",
  "etag",
  "last-modified",
  "location",
  "x-trace-id",
] as const;

export function getBackendApiBaseUrl() {
  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";

  return apiUrl.replace(/\/$/, "");
}

export function buildBackendUrl(path: string) {
  return `${getBackendApiBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
}

const TRACE_ID_PATTERN = /^[A-Za-z0-9._-]{8,128}$/;

/**
 * Forward the browser's `x-trace-id` (set per request in `lib/trace-fetch.ts`)
 * so backend logs and PostHog errors share an id. Outside a request, or when
 * the header is missing or malformed, a fresh id is used.
 */
export async function backendTraceHeaders(): Promise<Record<string, string>> {
  let incoming: string | null = null;
  try {
    const { headers } = await import("next/headers");
    incoming = (await headers()).get("x-trace-id");
  } catch {
    // Not inside a request scope (scripts, tests).
  }
  return { "x-trace-id": incoming && TRACE_ID_PATTERN.test(incoming) ? incoming : randomUUID() };
}

export async function fetchBackend(
  path: string,
  init: RequestInit & {
    userId?: string;
    extraHeaders?: HeadersInit;
  } = {},
) {
  const { userId, extraHeaders, headers, ...rest } = init;

  return fetch(buildBackendUrl(path), {
    redirect: "manual",
    ...rest,
    headers: {
      ...(await backendTraceHeaders()),
      ...(userId ? buildBackendAuthHeaders(userId) : {}),
      ...(headers ?? {}),
      ...(extraHeaders ?? {}),
    },
  });
}

export function createProxyResponse(
  upstream: Response,
  forwardedHeaders: readonly string[] = DEFAULT_FORWARDED_HEADERS,
) {
  const responseHeaders = new Headers();
  for (const headerName of forwardedHeaders) {
    const value = upstream.headers.get(headerName);
    if (value) {
      responseHeaders.set(headerName, value);
    }
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export async function createTextProxyResponse(
  upstream: Response,
  contentTypeFallback = "application/json",
) {
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || contentTypeFallback,
      ...(upstream.headers.get("x-trace-id")
        ? { "x-trace-id": upstream.headers.get("x-trace-id") as string }
        : {}),
    },
  });
}
