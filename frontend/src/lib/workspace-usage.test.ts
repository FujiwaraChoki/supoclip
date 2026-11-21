import { describe, expect, it } from "vitest";

import type { BillingSummary } from "@/hooks/use-billing-summary";
import { workspaceAllowance } from "./workspace-usage";

function summary(patch: Partial<BillingSummary>): BillingSummary {
  return {
    monetization_enabled: true,
    plan: "pro",
    subscription_status: "active",
    usage_count: 0,
    usage_limit: null,
    remaining: null,
    can_create_task: true,
    upgrade_required: false,
    reason: null,
    workspace_type: "personal",
    ...patch,
  };
}

describe("workspaceAllowance", () => {
  it("uses the plan's minutes in a personal workspace", () => {
    const allowance = workspaceAllowance(summary({ usage_minutes: 312, minute_limit: 600, remaining_minutes: 288 }));
    expect(allowance).toMatchObject({ isTeam: false, left: 288, binding: "pool", low: false, paused: false });
    expect(allowance.fraction).toBeCloseTo(0.52);
  });

  it("follows the member limit when it runs out before the team pool", () => {
    const allowance = workspaceAllowance(summary({
      workspace_type: "team", plan: "team", usage_minutes: 1840, minute_limit: 3600, remaining_minutes: 1760,
      member_minute_limit: 600, member_usage_minutes: 562, member_remaining_minutes: 38,
    }));
    expect(allowance).toMatchObject({ isTeam: true, left: 38, binding: "member", low: true });
    expect(allowance.fraction).toBeCloseTo(562 / 600);
  });

  it("follows the team pool when it runs out first", () => {
    const allowance = workspaceAllowance(summary({
      workspace_type: "team", plan: "team", usage_minutes: 3500, minute_limit: 3600, remaining_minutes: 100,
      member_minute_limit: 600, member_usage_minutes: 120, member_remaining_minutes: 480,
    }));
    expect(allowance).toMatchObject({ left: 100, binding: "pool", low: true });
    expect(allowance.fraction).toBeCloseTo(3500 / 3600);
  });

  it("treats a member limit of 0 as a paused seat", () => {
    const allowance = workspaceAllowance(summary({
      workspace_type: "team", plan: "team", usage_minutes: 10, minute_limit: 3600, remaining_minutes: 3590,
      member_minute_limit: 0, member_usage_minutes: 0, member_remaining_minutes: 0,
    }));
    expect(allowance).toMatchObject({ paused: true, left: 0, fraction: 1, low: true });
  });

  it("has nothing left to count without a limit", () => {
    const allowance = workspaceAllowance(summary({ plan: "self_host", usage_minutes: 45, minute_limit: null, remaining_minutes: null }));
    expect(allowance).toMatchObject({ left: null, fraction: 0, low: false });
  });
});
