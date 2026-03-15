import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

export async function requireAdminSession() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return { error: NextResponse.json({ error: "Unauthorized" }, { status: 401 }) };
  }

  const isAdmin = Boolean((session.user as { is_admin?: boolean }).is_admin);
  if (!isAdmin) {
    return { error: NextResponse.json({ error: "Forbidden" }, { status: 403 }) };
  }

  return { session };
}

export async function proxyAdminApiRequest(
  request: Request,
  pathSegments: string[]
) {
  const adminCheck = await requireAdminSession();
  if (adminCheck.error) {
    return adminCheck.error;
  }

  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const normalizedApiUrl = apiUrl.replace(/\/$/, "");
  const incomingUrl = new URL(request.url);
  const targetUrl = `${normalizedApiUrl}/admin/${pathSegments.join("/")}${incomingUrl.search}`;
  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.text();

  const upstream = await fetch(targetUrl, {
    method: request.method,
    headers: {
      ...buildBackendAuthHeaders(adminCheck.session.user.id),
      ...(body && request.headers.get("content-type")
        ? { "Content-Type": request.headers.get("content-type") as string }
        : {}),
      ...(request.headers.get("accept")
        ? { Accept: request.headers.get("accept") as string }
        : {}),
    },
    body,
    cache: "no-store",
  });

  const responseHeaders = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) {
    responseHeaders.set("Content-Type", contentType);
  }
  const traceId = upstream.headers.get("x-trace-id");
  if (traceId) {
    responseHeaders.set("x-trace-id", traceId);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
