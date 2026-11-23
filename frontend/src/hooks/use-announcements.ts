"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { ANNOUNCEMENTS_UPDATED_EVENT, type Announcement } from "@/lib/announcements";

const announcementsKey = ["announcements"] as const;

/**
 * A 502/503/504 or a network error throws, so the query keeps the last list and
 * tries again on the next interval, focus, or visibility change.
 */
async function loadAnnouncements(): Promise<Announcement[]> {
  const response = await fetch("/api/announcements", { cache: "no-store" });
  if (!response.ok) throw new Error(`Failed to fetch announcements: ${response.status}`);
  const data = (await response.json()) as { announcements?: Announcement[] };
  if (!Array.isArray(data.announcements)) throw new Error("Malformed announcements response");
  return data.announcements;
}

export function useAnnouncements() {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: announcementsKey,
    queryFn: loadAnnouncements,
    // Only while the tab is visible; focus and visibility changes refetch too.
    refetchInterval: 60_000,
    retry: false,
  });

  useEffect(() => {
    const onUpdated = () => { void queryClient.invalidateQueries({ queryKey: announcementsKey }); };
    // The query refetches on visibility changes; a window focus (another app, devtools) counts too.
    window.addEventListener("focus", onUpdated);
    window.addEventListener(ANNOUNCEMENTS_UPDATED_EVENT, onUpdated);
    return () => {
      window.removeEventListener("focus", onUpdated);
      window.removeEventListener(ANNOUNCEMENTS_UPDATED_EVENT, onUpdated);
    };
  }, [queryClient]);

  return data ?? [];
}
