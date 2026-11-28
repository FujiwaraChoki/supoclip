import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

/** Continuous live clipping sessions (backend `/live-sessions`): list, read, stop. */
async function proxyLiveSessionRequest(
  request: Request,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { path = [] } = await params;
  if (path.some((part) => !/^[A-Za-z0-9-]+$/.test(part))) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  const upstream = await fetchBackend(`/live-sessions/${path.join("/")}`, {
    method: request.method,
    signal: request.signal,
    userId: session.user.id,
    cache: "no-store",
  });
  return createProxyResponse(upstream);
}

export const GET = proxyLiveSessionRequest;
export const POST = proxyLiveSessionRequest;
