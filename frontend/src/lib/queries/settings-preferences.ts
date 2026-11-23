"use client";

import { useQuery } from "@tanstack/react-query";
import type { BillingSummary } from "@/hooks/use-billing-summary";
import { usePollUntil } from "@/lib/queries/settings-polling";

export interface UserPreferences {
  fontFamily: string;
  fontSize: number;
  fontColor: string;
  notifyOnCompletion: boolean;
  notifyOnPublish: boolean;
}

export const settingsKeys = {
  preferences: (userId: string) => ["settings", "preferences", userId] as const,
  /** Polls the billing summary after checkout until the paid plan shows up. */
  awaitPaidPlan: ["settings", "await-paid-plan"] as const,
};

/**
 * The saved caption and email defaults; null when the server would not say (the form then
 * starts from the stock defaults, as before). A network failure throws.
 */
async function fetchPreferences(): Promise<UserPreferences | null> {
  const response = await fetch("/api/preferences");
  return response.ok ? ((await response.json()) as UserPreferences) : null;
}

/**
 * Read once per visit: the page seeds its editable form from this, so it is not refetched
 * under someone's edits (staleTime), and is not kept between visits (gcTime) because the
 * create screen saves preferences too.
 */
export function usePreferences(userId: string | undefined) {
  return useQuery({
    queryKey: settingsKeys.preferences(userId ?? ""),
    queryFn: fetchPreferences,
    enabled: Boolean(userId),
    staleTime: Infinity,
    gcTime: 0,
  });
}

async function fetchBillingSummaryOrNull(): Promise<BillingSummary | null> {
  try {
    const response = await fetch("/api/tasks/billing-summary", { cache: "no-store" });
    return response.ok ? ((await response.json()) as BillingSummary) : null;
  } catch {
    return null;
  }
}

/**
 * Back from checkout: Stripe's webhook can land a moment after the redirect, so ask for the
 * billing summary now and every 1.5s, nine times in all (the old 8 ticks plus the first look).
 */
export function useAwaitPaidPlan(enabled: boolean, isPaid: (summary: BillingSummary) => boolean) {
  return usePollUntil({
    queryKey: settingsKeys.awaitPaidPlan,
    queryFn: fetchBillingSummaryOrNull,
    enabled,
    done: isPaid,
    attempts: 9,
    intervalMs: 1500,
  });
}
