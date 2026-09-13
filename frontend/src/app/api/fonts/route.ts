import { NextResponse } from "next/server";

import { getServerSession } from "@/server/session";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

export async function GET() {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const normalizedApiUrl = apiUrl.replace(/\/$/, "");
  const backendAuthHeaders = buildBackendAuthHeaders(session.user.id);

  let upstream = await fetch(`${normalizedApiUrl}/fonts`, {
    headers: {
      ...backendAuthHeaders,
    },
    cache: "no-store",
  });

  if (upstream.status === 404) {
    upstream = await fetch(`${normalizedApiUrl}/api/fonts`, {
      headers: {
        ...backendAuthHeaders,
      },
      cache: "no-store",
    });
  }

  const responseText = await upstream.text();
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
    },
  });
}
