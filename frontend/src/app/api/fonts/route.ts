import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";

const UPSTREAM_TIMEOUT_MS = 10_000;

type TransportError = { name: string; message: string; code?: unknown; cause?: { code?: unknown } };

// undici's codes for a connection that was refused, reset, closed or timed out, before or during the body.
const TRANSIENT_TRANSPORT_CODES = new Set([
  "UND_ERR_SOCKET",
  "UND_ERR_CLOSED",
  "UND_ERR_CONNECT_TIMEOUT",
  "UND_ERR_HEADERS_TIMEOUT",
  "UND_ERR_BODY_TIMEOUT",
  "ECONNRESET",
  "ECONNREFUSED",
  "ECONNABORTED",
  "EPIPE",
  "ETIMEDOUT",
]);

function transportErrorCode(error: TransportError): string | undefined {
  const code = error.cause?.code ?? error.code;
  return typeof code === "string" ? code : undefined;
}

function isTransientTransportError(error: unknown): error is TransportError {
  if (typeof error !== "object" || error === null) return false;
  const { name, message } = error as Partial<TransportError>;
  // AbortSignal.timeout rejects with TimeoutError.
  if (name === "TimeoutError" || name === "AbortError") return true;
  // undici rejects a dropped or refused connection with TypeError("fetch failed"), and a body cut off
  // mid-read with TypeError("terminated") (cause UND_ERR_SOCKET).
  if (error instanceof TypeError && (message === "fetch failed" || message === "terminated")) return true;
  const code = transportErrorCode(error as TransportError);
  return code !== undefined && TRANSIENT_TRANSPORT_CODES.has(code);
}

function transportErrorReason(error: TransportError) {
  const code = transportErrorCode(error);
  return code ? `${error.name}: ${error.message} (${code})` : `${error.name}: ${error.message}`;
}

// The font list is an idempotent GET, so a reset or timed-out connection gets one more try.
// The body is read inside the attempt because the connection can also drop mid-body.
async function fetchFonts(url: string, headers: Record<string, string>) {
  const attempt = async () => {
    const response = await fetch(url, { headers, cache: "no-store", signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS) });
    return { status: response.status, contentType: response.headers.get("content-type"), body: await response.text() };
  };
  try {
    return await attempt();
  } catch (error) {
    if (!isTransientTransportError(error)) throw error;
    return await attempt();
  }
}

export async function GET() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const normalizedApiUrl = apiUrl.replace(/\/$/, "");
  const backendAuthHeaders = { ...buildBackendAuthHeaders(session.user.id), ...(await backendTraceHeaders()) };

  let upstream: Awaited<ReturnType<typeof fetchFonts>>;
  try {
    upstream = await fetchFonts(`${normalizedApiUrl}/fonts`, backendAuthHeaders);
    if (upstream.status === 404) {
      upstream = await fetchFonts(`${normalizedApiUrl}/api/fonts`, backendAuthHeaders);
    }
  } catch (error) {
    if (!isTransientTransportError(error)) throw error;
    console.error(`Font list request to the backend failed after a retry: ${transportErrorReason(error)}`);
    return NextResponse.json(
      { error: "Fonts are temporarily unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.contentType || "application/json",
    },
  });
}
