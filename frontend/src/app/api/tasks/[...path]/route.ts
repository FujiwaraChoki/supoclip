import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

async function proxyTaskRequest(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { path } = await params;
  const incomingUrl = new URL(request.url);
  const targetPath = `/tasks/${path.join("/")}${incomingUrl.search}`;
  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.text();

  const upstream = await fetchBackend(targetPath, {
    method: request.method,
    // Close the backend stream even when the client leaves before headers arrive.
    signal: request.signal,
    userId: session.user.id,
    extraHeaders: {
      ...(body && request.headers.get("content-type")
        ? { "Content-Type": request.headers.get("content-type") as string }
        : {}),
      ...(request.headers.get("accept")
        ? { Accept: request.headers.get("accept") as string }
        : {}),
      ...(request.headers.get("range")
        ? { Range: request.headers.get("range") as string }
        : {}),
      ...(request.headers.get("if-range")
        ? { "If-Range": request.headers.get("if-range") as string }
        : {}),
    },
    body,
    cache: "no-store",
  });

  // Canvas video needs a CORS-enabled response. Following a same-origin
  // redirect to object storage can produce a request without Origin, so the
  // bucket returns usable bytes but no CORS header. Keep editor assets on this
  // authenticated origin; ordinary clip downloads still use signed redirects.
  const editorMedia = path.length === 6 && path[1] === "clips" &&
    path[3] === "editor" && path[4] === "media" &&
    ["clean.mp4", "layout.mp4", "thumbnails.jpg"].includes(path[5]);
  if (request.method === "GET" && editorMedia && upstream.status === 307) {
    const location = upstream.headers.get("location");
    let target: URL;
    try {
      target = new URL(location ?? "");
      if (!["https:", "http:"].includes(target.protocol) || target.username || target.password) throw new Error("Invalid media URL");
    } catch {
      await upstream.body?.cancel();
      return NextResponse.json({ error: "Preview storage returned an invalid URL." }, { status: 502 });
    }
    await upstream.body?.cancel();
    const mediaHeaders: Record<string, string> = {};
    for (const name of ["range", "if-range", "accept"]) {
      const value = request.headers.get(name);
      if (value) mediaHeaders[name] = value;
    }
    // Location comes from the authenticated backend, never the client. Do not
    // send its signed backend-auth headers, cookies, or client credentials to S3.
    const media = await fetch(target.toString(), {
      method: "GET", headers: mediaHeaders, signal: request.signal,
      cache: "no-store", credentials: "omit",
    });
    const response = createProxyResponse(media);
    response.headers.set("cache-control", "private, no-store");
    return response;
  }
  return createProxyResponse(upstream);
}

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyTaskRequest(request, context);
}

export async function POST(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyTaskRequest(request, context);
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyTaskRequest(request, context);
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyTaskRequest(request, context);
}
