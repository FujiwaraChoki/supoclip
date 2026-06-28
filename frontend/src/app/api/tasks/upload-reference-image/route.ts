import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const formData = await request.formData();
  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const normalizedApiUrl = apiUrl.replace(/\/$/, "");
  const upstream = await fetch(
    `${normalizedApiUrl}/tasks/upload-reference-image`,
    {
      method: "POST",
      headers: buildBackendAuthHeaders(session.user.id),
      body: formData,
    }
  );

  const responseText = await upstream.text();
  const traceId = upstream.headers.get("x-trace-id");
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") || "application/json",
      ...(traceId ? { "x-trace-id": traceId } : {}),
    },
  });
}
