import { createProxyResponse, fetchBackend } from "@/server/backend-api";

// Public, unauthenticated media endpoint. Instagram fetches Reels from a URL
// rather than accepting uploads, so the backend mints a short-lived random
// token per post and this route streams the clip for it. The token is the
// only credential; it expires a few hours after publishing starts.
const SAFE_TOKEN = /^[A-Za-z0-9_-]{16,64}$/;

export async function GET(
  request: Request,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  if (!SAFE_TOKEN.test(token)) {
    return Response.json({ detail: "Media not found" }, { status: 404 });
  }

  const upstream = await fetchBackend(`/social/media/${token}`, {
    method: "GET",
    extraHeaders: {
      ...(request.headers.get("range") ? { Range: request.headers.get("range") as string } : {}),
      ...(request.headers.get("if-range")
        ? { "If-Range": request.headers.get("if-range") as string }
        : {}),
    },
    cache: "no-store",
  });

  return createProxyResponse(upstream);
}

export async function HEAD(
  request: Request,
  context: { params: Promise<{ token: string }> },
) {
  const response = await GET(request, context);
  await response.body?.cancel().catch(() => undefined);
  return new Response(null, { status: response.status, headers: response.headers });
}
