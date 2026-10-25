import { createProxyResponse, fetchBackend } from "@/server/backend-api";

export const dynamic = "force-dynamic";

// The backend page sets all of these; relay them unchanged.
const FORWARDED_HEADERS = [
  "content-type",
  "cache-control",
  "referrer-policy",
  "x-robots-tag",
  "x-content-type-options",
  "content-security-policy",
] as const;

function unavailable() {
  return new Response("We couldn’t update your email preference. Please try again, or contact support@supoclip.com.", {
    status: 503,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" },
  });
}

// Intentionally public: an email link or mailbox one-click POST has no session.
// Token verification and preference writes belong to the backend.
async function unsubscribe(request: Request) {
  const token = new URL(request.url).searchParams.get("token") ?? "";
  if (token.length > 256) {
    return new Response("Invalid unsubscribe link", { status: 400, headers: { "Cache-Control": "no-store" } });
  }
  try {
    const upstream = await fetchBackend(`/email/product-updates/unsubscribe?${new URLSearchParams({ token })}`, {
      // Next serves HEAD (link scanners) through GET; the backend only routes GET and POST.
      method: request.method === "POST" ? "POST" : "GET",
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
    // Anything but the backend's own page (e.g. a JSON 500) is an outage, not content to show.
    if (!upstream.headers.get("content-type")?.startsWith("text/html")) {
      return unavailable();
    }
    return createProxyResponse(upstream, FORWARDED_HEADERS);
  } catch {
    return unavailable();
  }
}

export const GET = unsubscribe;
export const POST = unsubscribe;
