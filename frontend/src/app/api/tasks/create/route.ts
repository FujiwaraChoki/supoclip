import { NextResponse } from "next/server";

import { getServerSession } from "@/server/session";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

export async function POST(request: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const payload = await request.text();
  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const upstream = await fetch(`${apiUrl}/tasks/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...buildBackendAuthHeaders(session.user.id),
    },
    body: payload,
  });

  const responseText = await upstream.text();
  const traceId = upstream.headers.get("x-trace-id");
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
      ...(traceId ? { "x-trace-id": traceId } : {}),
    },
  });
}
