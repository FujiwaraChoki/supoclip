"use client";

import { useEffect } from "react";

import { captureAttribution, getStoredAttribution } from "@/lib/attribution";
import { useSession } from "@/lib/auth-client";

const syncedKey = (userId: string) => `supoclip:attribution-synced:${userId}`;

/** Captures first-touch campaign data on every page and syncs it once after sign-in. */
export function AttributionTracker() {
  const { data: session } = useSession();
  const userId = session?.user?.id;

  useEffect(() => {
    captureAttribution();
  }, []);

  useEffect(() => {
    if (!userId) return;
    try {
      if (window.localStorage.getItem(syncedKey(userId))) return;
    } catch {
      return;
    }
    const attribution = getStoredAttribution() ?? captureAttribution();
    if (!attribution) return;

    void fetch("/api/attribution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(attribution),
      keepalive: true,
    })
      .then((response) => {
        // Retry on the next page load if the server failed.
        if (response.ok) window.localStorage.setItem(syncedKey(userId), "1");
      })
      .catch(() => {});
  }, [userId]);

  return null;
}
