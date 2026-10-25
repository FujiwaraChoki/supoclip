import { captureApiFailure } from "@/lib/posthog";

export const TRACE_HEADER = "x-trace-id";

export function createTraceId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // randomUUID needs a secure context; plain-http self-hosts fall back to a v4 UUID by hand.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Same-origin calls to our API routes; auth and the DataFast proxy are left alone. */
function isTracedApiRequest(url: URL) {
  return (
    url.origin === window.location.origin &&
    url.pathname.startsWith("/api/") &&
    !url.pathname.startsWith("/api/auth/") &&
    url.pathname !== "/api/events"
  );
}

/**
 * Background polls. A gateway timeout or a dropped connection is retried by
 * the caller, which keeps the last payload, so it is not an application exception.
 */
const QUIET_TRANSIENT_PATHS = new Set([
  "/api/announcements",
  "/api/service-status/video-downloads",
]);

const QUIET_TRANSIENT_STATUSES = new Set([502, 503, 504]);

function isQuietTransientFailure(pathname: string, status: number) {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return QUIET_TRANSIENT_PATHS.has(path) && QUIET_TRANSIENT_STATUSES.has(status);
}

/**
 * The browser talks to the backend only through `/api/*` route handlers, from
 * dozens of call sites. Wrapping `fetch` once tags every such request with an
 * `x-trace-id` (forwarded to the backend by the proxies) and reports 5xx
 * responses with that id, so a browser error can be matched to backend logs.
 * Announcement and video-download polls skip 502/503/504; those callers retry.
 */
export function installTraceFetch() {
  if (typeof window === "undefined") return;
  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input, init) => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request ? request.url : String(input), window.location.href);
    if (!isTracedApiRequest(url)) return originalFetch(input, init);

    const headers = new Headers(init?.headers ?? request?.headers);
    const traceId = headers.get(TRACE_HEADER) || createTraceId();
    headers.set(TRACE_HEADER, traceId);

    // A rejected fetch (offline, DNS, dropped connection) propagates to the
    // caller and is not reported. Quiet polls catch it, keep the last payload,
    // and retry. Gateway statuses below are the same kind of blip.
    const response = await originalFetch(input, { ...init, headers });
    if (response.status >= 500 && !isQuietTransientFailure(url.pathname, response.status)) {
      captureApiFailure({
        method: (init?.method ?? request?.method ?? "GET").toUpperCase(),
        path: url.pathname,
        status: response.status,
        traceId: response.headers.get(TRACE_HEADER) || traceId,
      });
    }
    return response;
  };
}
