import { NextResponse } from "next/server";

import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

/** A pending MCP connector request, described for the consent page. */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const upstream = await fetchBackend(`/oauth/requests/${encodeURIComponent(id)}`, {
    method: "GET",
    userId: session.user.id,
    cache: "no-store",
  });

  return createTextProxyResponse(upstream);
}
