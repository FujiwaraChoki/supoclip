import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

/**
 * Same-origin proxy for dynamic-edit assets (backend `api/routes/fx.py`):
 * `GET /api/fx/assets` (the manifest) and `GET /api/fx/assets/{sha256}.{ext}` (one SFX, music bed or the emoji
 * atlas). Bodies are streamed, never buffered, and the backend's caching headers are kept: asset files are
 * content-addressed and immutable. Only those two shapes are forwarded, so the path can never reach another
 * backend route.
 */
const ASSET_FILE = /^[0-9a-f]{64}\.(?:wav|mp3|m4a|ogg|png|json)$/;

function fxBackendPath(path: readonly string[]): string | null {
  if (path.length === 1 && path[0] === "assets") return "/fx/assets";
  if (path.length === 2 && path[0] === "assets" && ASSET_FILE.test(path[1])) return `/fx/assets/${path[1]}`;
  return null;
}

export async function GET(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const target = fxBackendPath((await params).path ?? []);
  if (!target) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const forwarded: Record<string, string> = {};
  for (const [name, header] of [["range", "Range"], ["if-range", "If-Range"], ["if-none-match", "If-None-Match"],
    ["if-modified-since", "If-Modified-Since"], ["accept", "Accept"]] as const) {
    const value = request.headers.get(name);
    if (value) forwarded[header] = value;
  }

  let upstream: Response;
  try {
    upstream = await fetchBackend(target, {
      method: "GET",
      // Stop reading from the backend when the browser goes away (audio elements abort range requests often).
      signal: request.signal,
      userId: session.user.id,
      extraHeaders: forwarded,
      cache: "no-store",
    });
  } catch {
    // Backend unreachable (or the browser aborted): a plain 502 the client can retry, not an unhandled 500.
    return NextResponse.json({ error: "Edit assets are unavailable" }, { status: 502 });
  }
  return createProxyResponse(upstream);
}
