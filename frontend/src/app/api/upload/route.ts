import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";
import { maxVideoUploadBytes, uploadTooLargeMessage } from "@/lib/video-upload";

// Same slack the API allows for multipart boundaries around a file on the cap.
const UPLOAD_FRAMING_BYTES = 1024 * 1024;

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // Refuse before `formData()` buffers the body. A missing length is how a
  // chunked upload skips the cap; browsers send Content-Length for a file.
  const declaredLength = request.headers.get("content-length");
  const limit = maxVideoUploadBytes();
  if (!declaredLength || !/^\d+$/.test(declaredLength)) {
    return NextResponse.json(
      { detail: "A Content-Length is required for this upload." },
      { status: 411 },
    );
  }
  if (Number(declaredLength) > limit + UPLOAD_FRAMING_BYTES) {
    return NextResponse.json({ detail: uploadTooLargeMessage(limit) }, { status: 413 });
  }

  const formData = await request.formData();
  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const normalizedApiUrl = apiUrl.replace(/\/$/, "");
  const upstream = await fetch(`${normalizedApiUrl}/upload`, {
    method: "POST",
    headers: { ...buildBackendAuthHeaders(session.user.id), ...(await backendTraceHeaders()) },
    body: formData,
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
      ...(upstream.headers.get("x-trace-id")
        ? { "x-trace-id": upstream.headers.get("x-trace-id") as string }
        : {}),
    },
  });
}
