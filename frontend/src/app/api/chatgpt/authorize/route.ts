import { NextResponse } from "next/server";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";
import { CHATGPT_STATE_COOKIE, requireSameOrigin } from "@/server/chatgpt";

export async function POST(request: Request) {
  const originError = requireSameOrigin(request);
  if (originError) return originError;
  if (new URL(request.url).origin !== new URL(process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3107").origin) {
    return NextResponse.json({ detail: "Open SupoClip using its configured app address before connecting ChatGPT." }, { status: 400 });
  }
  const session = await getServerSession();
  if (!session?.user?.id) return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  try {
    const upstream = await fetchBackend("/chatgpt/authorize", {
      method: "POST", userId: session.user.id, body: await request.text(),
      extraHeaders: { "Content-Type": "application/json" }, cache: "no-store",
    });
    const data = await upstream.json();
    if (!upstream.ok) return NextResponse.json(data, { status: upstream.status });
    const response = NextResponse.json({ authorization_url: data.authorization_url });
    response.cookies.set(CHATGPT_STATE_COOKIE, data.state, {
      httpOnly: true, sameSite: "lax", secure: new URL(request.url).protocol === "https:",
      path: "/api/chatgpt", maxAge: 600,
    });
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch {
    return NextResponse.json({ detail: "Unable to start ChatGPT sign-in. Please try again." }, { status: 502 });
  }
}
