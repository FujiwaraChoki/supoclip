import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

async function proxyWorkflowRequest(
  request: Request,
  { params }: { params: Promise<{ path?: string[] }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { path = [] } = await params;
  const incomingUrl = new URL(request.url);
  const suffix = path.length ? `/${path.join("/")}` : "";
  const contentType = request.headers.get("content-type") || "application/json";
  const body = ["GET", "HEAD"].includes(request.method)
    ? undefined
    : contentType.includes("multipart/form-data")
      ? await request.arrayBuffer()
      : await request.text();
  const upstream = await fetchBackend(`/workflows${suffix}${incomingUrl.search}`, {
    method: request.method,
    userId: session.user.id,
    extraHeaders: body ? { "Content-Type": contentType } : undefined,
    body,
    cache: "no-store",
  });
  return createProxyResponse(upstream);
}

export async function GET(request: Request, context: { params: Promise<{ path?: string[] }> }) {
  return proxyWorkflowRequest(request, context);
}

export async function POST(request: Request, context: { params: Promise<{ path?: string[] }> }) {
  return proxyWorkflowRequest(request, context);
}

export async function PATCH(request: Request, context: { params: Promise<{ path?: string[] }> }) {
  return proxyWorkflowRequest(request, context);
}

export async function DELETE(request: Request, context: { params: Promise<{ path?: string[] }> }) {
  return proxyWorkflowRequest(request, context);
}
