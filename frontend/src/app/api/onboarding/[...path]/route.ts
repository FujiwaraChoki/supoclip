import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

// Authenticated proxy for the onboarding routes (/onboarding/* on the backend).
const SAFE_PATH_SEGMENT = /^[A-Za-z0-9_-]+$/;
const FORWARDED = ["content-type", "x-trace-id"] as const;

async function proxyOnboardingRequest(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { path } = await params;
  if (path.length === 0 || !path.every((segment) => SAFE_PATH_SEGMENT.test(segment))) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  const incomingUrl = new URL(request.url);
  const body = request.method === "GET" ? undefined : await request.text();
  const upstream = await fetchBackend(`/onboarding/${path.join("/")}${incomingUrl.search}`, {
    method: request.method,
    signal: request.signal,
    userId: session.user.id,
    extraHeaders: body ? { "Content-Type": request.headers.get("content-type") || "application/json" } : {},
    body,
    cache: "no-store",
  });
  return createProxyResponse(upstream, FORWARDED);
}

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyOnboardingRequest(request, context);
}

export async function POST(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyOnboardingRequest(request, context);
}
