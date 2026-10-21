"use client";

import { useCallback, useEffect, useState } from "react";

export interface BillingSummary {
  monetization_enabled: boolean;
  plan: string;
  subscription_status: string;
  subscription_provider?: string | null;
  cancel_at?: string | null;
  period_end?: string | null;
  usage_count: number;
  /** Legacy per-task cap; plans are metered in minutes, so this is null for them. */
  usage_limit: number | null;
  remaining: number | null;
  /** Minutes of source video charged this month. */
  usage_minutes?: number;
  minute_limit?: number | null;
  remaining_minutes?: number | null;
  /** Short side in pixels of delivered clips (720 or 1080); null when uncapped. */
  output_short_side?: number | null;
  legacy_hd?: boolean;
  billing_interval?: "month" | "year";
  can_schedule_posts?: boolean;
  can_create_task: boolean;
  upgrade_required: boolean;
  reason: string | null;
  max_youtube_duration_seconds?: number;
  max_upload_duration_seconds?: number;
}

// One in-flight request shared by the shell and the page it wraps.
let pending: Promise<BillingSummary | null> | null = null;

function loadBillingSummary() {
  pending ??= fetch("/api/tasks/billing-summary", { cache: "no-store" })
    .then((response) => (response.ok ? (response.json() as Promise<BillingSummary>) : null))
    .catch(() => null)
    .finally(() => { setTimeout(() => { pending = null; }, 5_000); });
  return pending;
}

export function useBillingSummary(enabled: boolean) {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void loadBillingSummary().then((data) => { if (!cancelled) setSummary(data); });
    return () => { cancelled = true; };
  }, [enabled]);
  const update = useCallback((patch: Partial<BillingSummary>) => {
    setSummary((current) => (current ? { ...current, ...patch } : current));
  }, []);
  return [summary, update] as const;
}
