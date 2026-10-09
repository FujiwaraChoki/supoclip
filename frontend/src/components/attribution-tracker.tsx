"use client";

import { useEffect, useRef } from "react";

import {
  captureAnonymousAttribution,
  claimAnonymousAttribution,
  confirmAccountAttribution,
  isAccountAttributionConfirmed,
  sanitizeAttribution,
} from "@/lib/attribution";
import { useSession } from "@/lib/auth-client";
import { captureCreatorCodeFromUrl } from "@/lib/creator-code";

/**
 * Captures campaign data only for signed-out visits. When an account is
 * signed in, claims this browser's anonymous visit for it synchronously, syncs
 * the claim, and keeps the server's answer once it is confirmed. Unconfirmed
 * or failed syncs are retried on the next page load.
 */
export function AttributionTracker() {
  const { data: session, isPending } = useSession();
  const userId = session?.user?.id;
  // The landing URL is read before navigation can change it, but only recorded
  // once the session is known to be signed out.
  const landing = useRef<{ href: string; referrer: string } | null>(null);

  useEffect(() => {
    landing.current = { href: window.location.href, referrer: document.referrer };
    // Remember a creator's ?ref= code on any landing page for checkout.
    void captureCreatorCodeFromUrl();
  }, []);

  useEffect(() => {
    if (isPending) return;
    const visit = landing.current;
    landing.current = null;

    if (!userId) {
      if (visit) captureAnonymousAttribution(visit.href, visit.referrer);
      return;
    }

    // Claim before any network call, so the visit belongs to this account even if the request is slow.
    const claimed = claimAnonymousAttribution(userId);
    if (isAccountAttributionConfirmed(userId)) return;

    const request = claimed
      ? fetch("/api/attribution", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(claimed),
          keepalive: true,
        })
      : fetch("/api/attribution");

    void request
      .then(async (response) => {
        if (!response.ok) return;
        const result = await response.json();
        // An empty answer for a new account may still change (another device can sync); retry later.
        if (result?.confirmed !== true) return;
        confirmAccountAttribution(userId, sanitizeAttribution(result.attribution), claimed);
      })
      .catch(() => {});
  }, [isPending, userId]);

  return null;
}
