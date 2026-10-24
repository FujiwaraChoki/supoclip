import { NextResponse } from "next/server";
import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";
import { requireSameOrigin } from "@/server/chatgpt";

type Context = { params: Promise<{ path: string[] }> };
const ACCOUNT_ID = "[a-fA-F0-9-]{36}";
const ALLOWED: Record<string, RegExp> = {
  GET: new RegExp(`^(status|accounts/${ACCOUNT_ID}/models)$`),
  POST: new RegExp(`^accounts/${ACCOUNT_ID}/(activate|acknowledge)$`),
  DELETE: new RegExp(`^accounts/${ACCOUNT_ID}$`),
};

async function proxy(request: Request, context: Context) {
  if (request.method !== "GET") {
    const originError = requireSameOrigin(request);
    if (originError) return originError;
  }
  const session = await getServerSession();
  if (!session?.user?.id) return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  const path = (await context.params).path.join("/");
  if (!ALLOWED[request.method]?.test(path)) return NextResponse.json({ detail: "Not found" }, { status: 404 });
  try {
    const upstream = await fetchBackend(`/chatgpt/${path}`, {
      method: request.method, userId: session.user.id, cache: "no-store",
      ...(request.method === "POST" ? { body: await request.text(), extraHeaders: { "Content-Type": "application/json" } } : {}),
    });
    const response = await createTextProxyResponse(upstream);
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch {
    return NextResponse.json({ detail: "Unable to reach ChatGPT settings. Please try again." }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
