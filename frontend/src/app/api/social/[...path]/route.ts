import { NextResponse } from "next/server";

import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

// Generic authenticated proxy for /social/* backend routes (connections,
// posts, performance). OAuth start/callback and public media have their own
// handlers under /api/social/connect, /api/social/callback and /api/social/media.
const SAFE_PATH_SEGMENT = /^[A-Za-z0-9_-]+$/;

async function proxySocialRequest(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { path } = await params;
  if (path.length === 0 || !path.every((segment) => SAFE_PATH_SEGMENT.test(segment))) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }

  const incomingUrl = new URL(request.url);
  const targetPath = `/social/${path.join("/")}${incomingUrl.search}`;
  const body =
    request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();

  const upstream = await fetchBackend(targetPath, {
    method: request.method,
    userId: session.user.id,
    extraHeaders: {
      ...(body ? { "Content-Type": request.headers.get("content-type") || "application/json" } : {}),
    },
    body,
    cache: "no-store",
  });

  return createTextProxyResponse(upstream);
}

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxySocialRequest(request, context);
}

export async function POST(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxySocialRequest(request, context);
}

export async function DELETE(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxySocialRequest(request, context);
}
