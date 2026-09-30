"use client";

import { useCallback, useEffect, useState } from "react";

export interface BillingSummary {
  monetization_enabled: boolean;
  plan: string;
  subscription_status: string;
  subscription_provider?: string | null;
  cancel_at?: string | null;
  usage_count: number;
  usage_limit: number | null;
  remaining: number | null;
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
