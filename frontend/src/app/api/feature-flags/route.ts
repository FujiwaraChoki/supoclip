import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

/** Feature flags evaluated for the signed-in user; the iOS app's `x-supoclip-client` is forwarded. */
export async function GET() {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const upstream = await fetchBackend("/feature-flags", {
    method: "GET",
    userId: session.user.id,
    cache: "no-store",
  });

  return createProxyResponse(upstream);
}
