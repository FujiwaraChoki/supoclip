import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export async function GET() {
  const session = await getServerSession();
  // For self-hosted mode, use mock user if no real session exists
  const userId = session?.user?.id || "local-user";

  const upstream = await fetchBackend("/tasks/", {
    method: "GET",
    userId,
    cache: "no-store",
  });

  return createProxyResponse(upstream);
}
