import { buildBackendAuthHeaders } from "@/lib/backend-auth";

export const DEFAULT_FORWARDED_HEADERS = [
  "cache-control",
  "content-disposition",
  "content-type",
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

export async function fetchBackend(
  path: string,
  init: RequestInit & {
    userId?: string;
    extraHeaders?: HeadersInit;
  } = {},
) {
  const { userId, extraHeaders, headers, ...rest } = init;

  return fetch(buildBackendUrl(path), {
    ...rest,
    headers: {
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
