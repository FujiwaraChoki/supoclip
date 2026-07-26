import { createProxyResponse, fetchBackend } from "@/server/backend-api";

// Share tokens, clip IDs, and the fixed "clips"/"file" segments are all URL-safe;
// anything else (".." traversal, encoded slashes) must not reach the backend URL.
const SAFE_PATH_SEGMENT = /^[A-Za-z0-9_-]+$/;

export async function GET(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  if (path.length === 0 || !path.every((segment) => SAFE_PATH_SEGMENT.test(segment))) {
    return Response.json({ detail: "Shared result not found" }, { status: 404 });
  }
  const upstream = await fetchBackend(`/tasks/shared/${path.join("/")}`, {
    method: "GET",
    extraHeaders: {
      ...(request.headers.get("accept")
        ? { Accept: request.headers.get("accept") as string }
        : {}),
      ...(request.headers.get("range")
        ? { Range: request.headers.get("range") as string }
        : {}),
      ...(request.headers.get("if-range")
        ? { "If-Range": request.headers.get("if-range") as string }
        : {}),
    },
    cache: "no-store",
  });

  return createProxyResponse(upstream);
}
