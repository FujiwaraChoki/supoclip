import { NextResponse } from "next/server";

import { getSiteUrl } from "@/lib/site";

import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

const SUPPORTED_PROVIDERS = new Set(["youtube", "tiktok", "instagram"]);
const MOBILE_STATE_PREFIX = "m.";
const APP_CALLBACK_URL = "supoclip://social-callback";

function settingsRedirect(query: Record<string, string>) {
  const url = new URL("/settings/social", getSiteUrl());
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
    return settingsRedirect({ error: "Unknown provider" });
  }

  const url = new URL(request.url);
  const providerError =
    url.searchParams.get("error_description") ||
    url.searchParams.get("error_reason") ||
    url.searchParams.get("error");
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");

  // States started from the native app hand the result back to it: the app's
  // web-auth sheet has no browser session, and the backend only accepts the
  // code from the user who started the handshake.
  if (state?.startsWith(MOBILE_STATE_PREFIX)) {
    const appUrl = new URL(APP_CALLBACK_URL);
    appUrl.searchParams.set("provider", provider);
    if (providerError || !code) {
      appUrl.searchParams.set("error", providerError || "The platform did not return an authorization code");
    } else {
      appUrl.searchParams.set("code", code);
      appUrl.searchParams.set("state", state);
    }
    return NextResponse.redirect(appUrl);
  }

  if (providerError || !code || !state) {
    return settingsRedirect({
      error: providerError || "The platform did not return an authorization code",
    });
  }

  const session = await getServerSession();
  if (!session?.user?.id) {
    return settingsRedirect({
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
    return settingsRedirect({
      error: payload.detail || `Could not connect your ${provider} account`,
    });
  }

  return settingsRedirect({
    connected: provider,
    account: payload.connection?.username || payload.connection?.display_name || "",
  });
}
