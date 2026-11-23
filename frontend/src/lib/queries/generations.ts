"use client";

import { useQuery } from "@tanstack/react-query";
import { ACTIVE_TASK_STATUSES } from "@/components/app/status-badge";
import { fetchGenerations, type GenerationSummary } from "@/lib/generations";

/** Polling interval while any generation is still processing. */
export const ACTIVE_GENERATIONS_POLL_MS = 5000;

export const generationKeys = {
  all: (userId: string) => ["generations", userId] as const,
};

function hasActive(generations: GenerationSummary[] | undefined) {
  return Boolean(generations?.some((g) => ACTIVE_TASK_STATUSES.includes(g.status)));
}

/** The signed-in user's generations; polls every 5s while one is still processing. */
export function useGenerations(userId: string | undefined) {
  return useQuery({
    queryKey: generationKeys.all(userId ?? ""),
    queryFn: fetchGenerations,
    enabled: Boolean(userId),
    refetchInterval: (query) => (hasActive(query.state.data) ? ACTIVE_GENERATIONS_POLL_MS : false),
  });
}
