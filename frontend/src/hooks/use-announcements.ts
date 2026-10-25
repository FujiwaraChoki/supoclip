"use client";

import { useEffect, useState } from "react";
import { ANNOUNCEMENTS_UPDATED_EVENT, type Announcement } from "@/lib/announcements";

let pending: Promise<Announcement[] | null> | null = null;

function loadAnnouncements() {
  // A 502/503/504 or a network error resolves to null. Callers keep the last
  // list and the hook retries on the next interval, focus, or visibility change.
  pending ??= fetch("/api/announcements", { cache: "no-store" })
    .then(async response => {
      if (!response.ok) return null;
      const data = (await response.json()) as { announcements?: Announcement[] };
      return Array.isArray(data.announcements) ? data.announcements : null;
    })
    .catch(() => null)
    .finally(() => { pending = null; });
  return pending;
}

export function useAnnouncements() {
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      if (document.hidden) return;
      void loadAnnouncements().then(items => { if (!cancelled && items) setAnnouncements(items); });
    };
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener(ANNOUNCEMENTS_UPDATED_EVENT, refresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener(ANNOUNCEMENTS_UPDATED_EVENT, refresh);
    };
  }, []);
  return announcements;
}
