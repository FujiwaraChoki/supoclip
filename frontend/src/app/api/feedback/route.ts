import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const payload = await request.text();
  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";

  // Stored with the submission in the admin feedback inbox.
  const client: Record<string, string> = {};
  const userAgent = request.headers.get("user-agent");
  const appVersion = request.headers.get("x-supoclip-app-version");
  if (userAgent) client["User-Agent"] = userAgent.slice(0, 500);
  if (appVersion) client["x-supoclip-app-version"] = appVersion.slice(0, 50);

  const upstream = await fetch(`${apiUrl}/feedback`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...client,
      ...buildBackendAuthHeaders(session.user.id),
      ...(await backendTraceHeaders()),
    },
    body: payload,
  });

  const responseText = await upstream.text();
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
    },
  });
}
