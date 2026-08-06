import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

async function proxySocialRequest(
  request: Request,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { path = [] } = await params;
  const url = new URL(request.url);
  const suffix = path.length ? `/${path.join("/")}` : "";
  const body = ["GET", "HEAD"].includes(request.method) ? undefined : await request.text();
  const upstream = await fetchBackend(`/social${suffix}${url.search}`, {
    method: request.method, userId: session.user.id,
    extraHeaders: body ? { "Content-Type": request.headers.get("content-type") || "application/json" } : undefined,
    body, cache: "no-store",
  });
  return createProxyResponse(upstream);
}

export const GET = proxySocialRequest;
export const POST = proxySocialRequest;
export const DELETE = proxySocialRequest;
