"use client";

import { useCallback, useEffect, useState } from "react";

export const WORKSPACE_CHANGED_EVENT = "supoclip:workspace-changed";

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
  /** Configured upload size cap in bytes. */
  max_upload_bytes?: number;
  /** Workspace scope ("personal" or "team") and team metadata when active. */
  workspace_type?: "personal" | "team";
  team_id?: string | null;
  team_name?: string | null;
  seat_limit?: number | null;
  included_seats?: number | null;
  /** Optional per-member monthly generation cap configured by a team admin. */
  member_minute_limit?: number | null;
  member_usage_minutes?: number | null;
  member_remaining_minutes?: number | null;
  member_limit_reached?: boolean;
}

// One in-flight request shared by the shell and the page it wraps.
let pending: Promise<BillingSummary | null> | null = null;

function loadBillingSummary(force = false) {
  if (force) pending = null;
  pending ??= fetch("/api/tasks/billing-summary", { cache: "no-store" })
    .then((response) => (response.ok ? (response.json() as Promise<BillingSummary>) : null))
    .catch(() => null)
    .finally(() => { setTimeout(() => { pending = null; }, 5_000); });
  return pending;
}

export function invalidateBillingSummary() {
  pending = null;
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(WORKSPACE_CHANGED_EVENT));
  }
}

export function useBillingSummary(enabled: boolean) {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const fetchNow = (force = false) => {
      void loadBillingSummary(force).then((data) => {
        if (!cancelled) setSummary(data);
      });
    };
    fetchNow();
    const onWorkspaceChanged = () => fetchNow(true);
    window.addEventListener(WORKSPACE_CHANGED_EVENT, onWorkspaceChanged);
    return () => {
      cancelled = true;
      window.removeEventListener(WORKSPACE_CHANGED_EVENT, onWorkspaceChanged);
    };
  }, [enabled]);
  const update = useCallback((patch: Partial<BillingSummary>) => {
    setSummary((current) => (current ? { ...current, ...patch } : current));
  }, []);
  return [summary, update] as const;
}
