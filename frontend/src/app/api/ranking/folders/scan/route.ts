import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { NextResponse } from "next/server";

import { getServerSession } from "@/server/session";

// A dedicated route (not the generic [...path] proxy) — that proxy reads the
// request body with `.text()`, which corrupts multipart/form-data uploads
// (binary bytes + boundary). This mirrors api/upload/route.ts's pattern:
// re-parse as FormData and let fetch generate a fresh multipart body.
export async function POST(request: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const formData = await request.formData();
  const upstream = await fetchBackend("/ranking/folders/scan", {
    method: "POST",
    userId: session.user.id,
    body: formData,
    cache: "no-store",
  });

  return createProxyResponse(upstream);
}
