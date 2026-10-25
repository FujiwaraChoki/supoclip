import { fetchBackend } from "@/server/backend-api";

export const dynamic = "force-dynamic";

// Intentionally public: an email link or mailbox one-click POST has no session.
// Token verification and preference writes belong to the backend.
async function unsubscribe(request: Request) {
  const token = new URL(request.url).searchParams.get("token") ?? "";
  if (token.length > 256) {
    return new Response("Invalid unsubscribe link", { status: 400, headers: { "Cache-Control": "no-store" } });
  }
  try {
    const upstream = await fetchBackend(`/email/product-updates/unsubscribe?${new URLSearchParams({ token })}`, {
      method: request.method,
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
    const headers = new Headers({
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Referrer-Policy": "no-referrer",
      "X-Robots-Tag": "noindex, nofollow",
      "X-Content-Type-Options": "nosniff",
    });
    const csp = upstream.headers.get("content-security-policy");
    if (csp) headers.set("Content-Security-Policy", csp);
    return new Response(await upstream.text(), { status: upstream.status, headers });
  } catch {
    return new Response("We couldn’t update your email preference. Please try again, or contact support@supoclip.com.", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" },
    });
  }
}

export const GET = unsubscribe;
export const POST = unsubscribe;
