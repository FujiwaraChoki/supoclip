"use client";

import { useQuery } from "@tanstack/react-query";
import { useInvalidateOnWindowEvent } from "@/lib/queries/window-events";
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
  const { data } = useQuery({
    queryKey: announcementsKey,
    queryFn: loadAnnouncements,
    // Only while the tab is visible; focus and visibility changes refetch too.
    refetchInterval: 60_000,
    retry: false,
  });

  // The query refetches on visibility changes; a window focus (another app, devtools) counts too.
  useInvalidateOnWindowEvent("focus", announcementsKey);
  useInvalidateOnWindowEvent(ANNOUNCEMENTS_UPDATED_EVENT, announcementsKey);

  return data ?? [];
}
