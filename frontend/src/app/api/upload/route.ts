import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";
import { maxVideoUploadBytes, uploadTooLargeMessage } from "@/lib/video-upload";

// Same slack the API allows for multipart boundaries around a file on the cap.
const UPLOAD_FRAMING_BYTES = 1024 * 1024;

export const dynamic = "force-dynamic";
// A multi-gigabyte body can take longer than the platform default to stream
// through. Host nginx in front of this route needs the same room; see
// docs/media-storage.md.
export const maxDuration = 3600;

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // Refuse before the body is read. A missing length is how a chunked upload
  // skips the cap; browsers and the iOS app send Content-Length for a file.
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

  const contentType = request.headers.get("content-type");
  if (!contentType || !request.body) {
    return NextResponse.json({ detail: "A multipart upload is required" }, { status: 400 });
  }

  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const normalizedApiUrl = apiUrl.replace(/\/$/, "");
  const forwardHeaders: Record<string, string> = {
    ...buildBackendAuthHeaders(session.user.id),
    ...(await backendTraceHeaders()),
    "content-type": contentType,
    "content-length": declaredLength,
  };
  const idempotencyKey = request.headers.get("idempotency-key");
  if (idempotencyKey) {
    forwardHeaders["idempotency-key"] = idempotencyKey;
  }

  // Stream the raw body. Buffering it here (`formData()`) holds the whole
  // file in the Next.js process before the API can store it, which is what
  // runs into the proxy's response-header timeout on a ~2 GB upload.
  // Node's fetch requires `duplex` when the body is a stream.
  const init: RequestInit & { duplex: "half" } = {
    method: "POST",
    headers: forwardHeaders,
    body: request.body,
    duplex: "half",
  };
  const upstream = await fetch(`${normalizedApiUrl}/upload`, init);

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
