import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

// Authenticated proxy for the agent home screen (/agent/* on the backend). Responses
// are streamed through unchanged, so the conversation's SSE event stream works too.
const SAFE_PATH_SEGMENT = /^[A-Za-z0-9_-]+$/;
const FORWARDED = ["content-type", "cache-control", "x-trace-id"] as const;

async function proxyAgentRequest(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { path } = await params;
  if (path.length === 0 || !path.every((segment) => SAFE_PATH_SEGMENT.test(segment))) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  const incomingUrl = new URL(request.url);
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();
  const lastEventId = request.headers.get("last-event-id");
  const upstream = await fetchBackend(`/agent/${path.join("/")}${incomingUrl.search}`, {
    method: request.method,
    signal: request.signal,
    userId: session.user.id,
    extraHeaders: {
      ...(body ? { "Content-Type": request.headers.get("content-type") || "application/json" } : {}),
      ...(request.headers.get("accept") ? { Accept: request.headers.get("accept") as string } : {}),
      ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
    },
    body,
    cache: "no-store",
  });
  const response = createProxyResponse(upstream, FORWARDED);
  if (upstream.headers.get("content-type")?.includes("text/event-stream")) {
    response.headers.set("cache-control", "no-cache, no-transform");
    response.headers.set("x-accel-buffering", "no");
  }
  return response;
}

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyAgentRequest(request, context);
}

export async function POST(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyAgentRequest(request, context);
}

export async function PATCH(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyAgentRequest(request, context);
}

export async function DELETE(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyAgentRequest(request, context);
}
