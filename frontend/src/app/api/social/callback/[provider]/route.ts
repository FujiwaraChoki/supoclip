import { NextResponse } from "next/server";

import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

const SUPPORTED_PROVIDERS = new Set(["youtube", "tiktok", "instagram"]);

function settingsRedirect(request: Request, query: Record<string, string>) {
  const url = new URL("/settings/social", request.url);
  for (const [key, value] of Object.entries(query)) {
    url.searchParams.set(key, value);
  }
  return NextResponse.redirect(url);
}

// OAuth redirect target. The platform sends the browser back here with
// ?code=&state=; we hand both to the backend (with the signed session) which
// validates the state, exchanges the code and stores the encrypted tokens.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ provider: string }> },
) {
  const { provider } = await params;
  if (!SUPPORTED_PROVIDERS.has(provider)) {
    return settingsRedirect(request, { error: "Unknown provider" });
  }

  const url = new URL(request.url);
  const providerError =
    url.searchParams.get("error_description") ||
    url.searchParams.get("error_reason") ||
    url.searchParams.get("error");
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");

  if (providerError || !code || !state) {
    return settingsRedirect(request, {
      error: providerError || "The platform did not return an authorization code",
    });
  }

  const session = await getServerSession();
  if (!session?.user?.id) {
    return settingsRedirect(request, {
      error: "Your session expired during the connection. Sign in and try again.",
    });
  }

  const upstream = await fetchBackend(`/social/connections/${provider}/callback`, {
    method: "POST",
    userId: session.user.id,
    extraHeaders: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, state }),
    cache: "no-store",
  });

  const payload = (await upstream.json().catch(() => ({}))) as {
    connection?: { display_name?: string | null; username?: string | null };
    detail?: string;
  };

  if (!upstream.ok) {
    return settingsRedirect(request, {
      error: payload.detail || `Could not connect your ${provider} account`,
    });
  }

  return settingsRedirect(request, {
    connected: provider,
    account: payload.connection?.username || payload.connection?.display_name || "",
  });
}
