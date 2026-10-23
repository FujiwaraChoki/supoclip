import { headers } from "next/headers";
import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";

export async function POST(request: Request, context: { params: Promise<{ action: string }> }) {
  const { action } = await context.params;
  if (action !== "complete" && action !== "abort" && action !== "parts") {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const apiUrl = process.env.BACKEND_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const response = await fetch(`${apiUrl.replace(/\/$/, "")}/upload/${action}`, {
    method: "POST",
    headers: { ...buildBackendAuthHeaders(session.user.id), ...(await backendTraceHeaders()), "Content-Type": "application/json" },
    body: await request.text(),
    cache: "no-store",
  });
  return new Response(response.body, { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
}
