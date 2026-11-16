import { NextResponse } from "next/server";

import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

/** Allow or deny an MCP connector. Returns the URL to send the browser to. */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // A JSON body can't be sent cross-site without a CORS preflight, so a form on
  // another site can't approve a connection with the user's cookie.
  if (!(request.headers.get("content-type") || "").toLowerCase().startsWith("application/json")) {
    return NextResponse.json({ error: "Expected JSON" }, { status: 415 });
  }
  const body = (await request.json().catch(() => null)) as { approve?: unknown } | null;
  if (typeof body?.approve !== "boolean") {
    return NextResponse.json({ error: "approve must be true or false" }, { status: 400 });
  }

  const { id } = await params;
  const upstream = await fetchBackend(`/oauth/requests/${encodeURIComponent(id)}/decision`, {
    method: "POST",
    userId: session.user.id,
    extraHeaders: { "Content-Type": "application/json" },
    body: JSON.stringify({ approve: body.approve }),
    cache: "no-store",
  });

  return createTextProxyResponse(upstream);
}
