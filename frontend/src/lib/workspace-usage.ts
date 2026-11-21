import type { BillingSummary } from "@/hooks/use-billing-summary";

/** One workspace in `GET /api/billing/usage`: personal (`teamId: null`) or a team the user belongs to. */
export type WorkspaceUsage = {
  teamId: string | null;
  name: string | null;
  role: "owner" | "admin" | "member" | null;
  summary: BillingSummary;
};

export type WorkspaceUsagePayload = {
  activeTeamId: string | null;
  workspaces: WorkspaceUsage[];
};

/**
 * What one person can still generate in a workspace this month.
 *
 * In a team the pool and an admin-set member limit both apply; whichever runs
 * out first decides `left`, the ring and the low-allowance warning.
 */
export function workspaceAllowance(summary: BillingSummary) {
  const isTeam = summary.workspace_type === "team";
  const poolLimit = summary.minute_limit ?? null;
  const poolUsed = summary.usage_minutes ?? 0;
  const poolLeft = poolLimit === null ? null : summary.remaining_minutes ?? Math.max(poolLimit - poolUsed, 0);

  const memberLimit = isTeam ? summary.member_minute_limit ?? null : null;
  const memberUsed = summary.member_usage_minutes ?? 0;
  const memberLeft = memberLimit === null ? null : summary.member_remaining_minutes ?? Math.max(memberLimit - memberUsed, 0);

  const bounds = [poolLeft, memberLeft].filter((value): value is number => value !== null);
  const left = bounds.length ? Math.min(...bounds) : null;
  const binding: "member" | "pool" = memberLeft !== null && (poolLeft === null || memberLeft <= poolLeft) ? "member" : "pool";
  const [used, bindingLimit] = binding === "member" ? [memberUsed, memberLimit] : [poolUsed, poolLimit];
  const fraction = bindingLimit ? Math.min(1, used / bindingLimit) : bindingLimit === 0 ? 1 : 0;

  return {
    isTeam,
    poolUsed,
    poolLimit,
    memberUsed,
    memberLimit,
    /** An admin set this member's limit to 0. */
    paused: memberLimit === 0,
    left,
    /** 0..1 of the allowance that drives the ring. */
    fraction,
    /** At or within 20% of whichever limit binds first. */
    low: left !== null && (left <= 0 || (bindingLimit !== null && bindingLimit > 0 && left <= Math.ceil(bindingLimit * 0.2))),
    /** Which limit runs out first. */
    binding,
  };
}
