import { NextResponse } from "next/server";

import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export async function POST() {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const upstream = await fetchBackend("/admin/test-ollama-connection", {
    method: "POST",
    userId: session.user.id,
    cache: "no-store",
  });
  return createTextProxyResponse(upstream);
}
