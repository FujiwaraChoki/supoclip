import { NextResponse } from "next/server";

import { getSiteUrl } from "@/lib/site";

import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

const SUPPORTED_PROVIDERS = new Set(["youtube", "tiktok", "instagram"]);

function settingsRedirect(query: Record<string, string>) {
  const url = new URL("/settings/social", getSiteUrl());
  for (const [key, value] of Object.entries(query)) {
    url.searchParams.set(key, value);
  }
  return NextResponse.redirect(url);
}

// Starts the OAuth handshake: asks the backend for the platform's authorize
// URL (which records a CSRF state bound to this user) and sends the browser there.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ provider: string }> },
) {
  const { provider } = await params;
  if (!SUPPORTED_PROVIDERS.has(provider)) {
    return settingsRedirect({ error: "Unknown provider" });
  }

  const session = await getServerSession();
  if (!session?.user?.id) {
    const signIn = new URL("/sign-in", getSiteUrl());
    signIn.searchParams.set("next", "/settings/social");
    return NextResponse.redirect(signIn);
  }

  const upstream = await fetchBackend(`/social/connections/${provider}/authorize`, {
    method: "POST",
    userId: session.user.id,
    extraHeaders: { "Content-Type": "application/json" },
    body: "{}",
    cache: "no-store",
  });

  const payload = (await upstream.json().catch(() => ({}))) as {
    authorize_url?: string;
    detail?: string;
  };

  if (!upstream.ok || !payload.authorize_url) {
    return settingsRedirect({
      error: payload.detail || `Could not start the ${provider} connection`,
    });
  }

  return NextResponse.redirect(payload.authorize_url);
}
