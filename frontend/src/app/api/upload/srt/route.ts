import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";
import { maxSrtUploadBytes } from "@/lib/video-upload";

// Same slack the API allows for multipart boundaries around a file on the cap.
const UPLOAD_FRAMING_BYTES = 16 * 1024;

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const declaredLength = request.headers.get("content-length");
  const limit = maxSrtUploadBytes();
  if (!declaredLength || !/^\d+$/.test(declaredLength)) {
    return NextResponse.json(
      { detail: "A Content-Length is required for this upload." },
      { status: 411 },
    );
  }
  if (Number(declaredLength) > limit + UPLOAD_FRAMING_BYTES) {
    return NextResponse.json({ detail: "This subtitle file is too large." }, { status: 413 });
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

  const init: RequestInit & { duplex: "half" } = {
    method: "POST",
    headers: forwardHeaders,
    body: request.body,
    duplex: "half",
  };
  let upstream: Response;
  try {
    upstream = await fetch(`${normalizedApiUrl}/upload/srt`, init);
  } catch (error) {
    if (request.signal?.aborted) {
      return new Response(null, { status: 499 });
    }
    console.error("Subtitle upload could not be forwarded to the API", error);
    return NextResponse.json(
      { detail: "The subtitle file could not be stored. Please try again." },
      { status: 502 },
    );
  }

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