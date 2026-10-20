import { cookies } from "next/headers";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";
import { CHATGPT_STATE_COOKIE, chatGPTResultRedirect, validChatGPTState } from "@/server/chatgpt";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const cookieStore = await cookies();
  if (!validChatGPTState(cookieStore.get(CHATGPT_STATE_COOKIE)?.value, params.get("state"))) {
    return chatGPTResultRedirect(request, "expired");
  }
  const session = await getServerSession();
  if (!session?.user?.id) return chatGPTResultRedirect(request, "expired");
  try {
    const upstream = await fetchBackend("/chatgpt/complete", {
      method: "POST", userId: session.user.id, cache: "no-store",
      extraHeaders: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(["state", "code", "client_id", "error"].map((key) => [key, params.get(key)]))),
    });
    return chatGPTResultRedirect(request, upstream.ok ? "connected" : "failed");
  } catch {
    return chatGPTResultRedirect(request, "failed");
  }
}
