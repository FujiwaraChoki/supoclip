import { createProxyResponse, fetchBackend } from "@/server/backend-api";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
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
