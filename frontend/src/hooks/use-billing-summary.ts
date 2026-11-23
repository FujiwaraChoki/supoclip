"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect } from "react";

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
  max_youtube_duration_seconds?: number | null;
  max_upload_duration_seconds?: number | null;
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

export const billingSummaryKey = ["billing-summary"] as const;

async function loadBillingSummary(): Promise<BillingSummary | null> {
  try {
    const response = await fetch("/api/tasks/billing-summary", { cache: "no-store" });
    return response.ok ? ((await response.json()) as BillingSummary) : null;
  } catch {
    return null;
  }
}

/** Refetch the summary everywhere it is shown (after a plan, team or workspace change). */
export function invalidateBillingSummary() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(WORKSPACE_CHANGED_EVENT));
  }
}

export function useBillingSummary(enabled: boolean) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: billingSummaryKey,
    queryFn: loadBillingSummary,
    enabled,
    // The shell and the page it wraps both ask on mount; one request serves both.
    staleTime: 5_000,
  });

  useEffect(() => {
    if (!enabled) return;
    const onWorkspaceChanged = () => {
      void queryClient.invalidateQueries({ queryKey: billingSummaryKey }, { cancelRefetch: false });
    };
    window.addEventListener(WORKSPACE_CHANGED_EVENT, onWorkspaceChanged);
    return () => window.removeEventListener(WORKSPACE_CHANGED_EVENT, onWorkspaceChanged);
  }, [enabled, queryClient]);

  const update = useCallback((patch: Partial<BillingSummary>) => {
    queryClient.setQueryData<BillingSummary | null>(billingSummaryKey, (current) =>
      current ? { ...current, ...patch } : current,
    );
  }, [queryClient]);

  return [enabled ? data ?? null : null, update] as const;
}
