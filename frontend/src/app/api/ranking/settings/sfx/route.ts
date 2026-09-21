import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { NextResponse } from "next/server";

import { getServerSession } from "@/server/session";

// Dedicated route for the same reason as folders/scan/route.ts — the
// generic [...path] proxy reads the body as text, which corrupts a
// multipart file upload.
export async function POST(request: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const formData = await request.formData();
  const upstream = await fetchBackend("/ranking/settings/sfx", {
    method: "POST",
    userId: session.user.id,
    body: formData,
    cache: "no-store",
  });

  return createProxyResponse(upstream);
}
