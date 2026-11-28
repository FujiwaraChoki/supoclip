"use client";

import { useQuery } from "@tanstack/react-query";

/** Feature flags evaluated for the caller (`GET /api/feature-flags`); only an explicit true is on. */
async function fetchFeatureFlags(): Promise<Record<string, boolean>> {
  const response = await fetch("/api/feature-flags", { cache: "no-store" });
  if (!response.ok) return {};
  const data: unknown = await response.json();
  const flags = data && typeof data === "object" ? (data as { flags?: unknown }).flags : null;
  const known: Record<string, boolean> = {};
  if (flags && typeof flags === "object") {
    for (const [key, value] of Object.entries(flags)) if (value === true) known[key] = true;
  }
  return known;
}

export function useFeatureFlag(key: string, enabled = true): boolean {
  const query = useQuery({
    queryKey: ["feature-flags"],
    queryFn: fetchFeatureFlags,
    enabled,
    staleTime: 60_000,
  });
  return query.data?.[key] === true;
}
