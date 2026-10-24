import { timingSafeEqual } from "node:crypto";
import { NextResponse } from "next/server";

export const CHATGPT_STATE_COOKIE = "supoclip_chatgpt_state";

export function validChatGPTState(expected: string | undefined, actual: string | null) {
  if (!expected || !actual || actual.length > 256) return false;
  const left = Buffer.from(expected);
  const right = Buffer.from(actual);
  return left.length === right.length && timingSafeEqual(left, right);
}

export function requireSameOrigin(request: Request) {
  const origin = request.headers.get("origin");
  if (origin !== new URL(request.url).origin) {
    return NextResponse.json({ detail: "Invalid request origin" }, { status: 403 });
  }
  return null;
}

export function chatGPTResultRedirect(request: Request, result: "connected" | "failed" | "expired") {
  const url = new URL("/settings/chatgpt", request.url);
  url.searchParams.set("chatgpt", result);
  const response = NextResponse.redirect(url, 303);
  response.cookies.set(CHATGPT_STATE_COOKIE, "", { httpOnly: true, sameSite: "lax", path: "/api/chatgpt", maxAge: 0 });
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
