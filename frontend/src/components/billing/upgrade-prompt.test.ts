import { getUpgradeState, upgradeReasonForError } from "./upgrade-prompt";

const base = {
  plan: "pro",
  subscription_status: "active",
  subscription_provider: "stripe",
  usage_count: 10,
  usage_limit: 50,
  remaining: 40,
  period_end: "2026-11-01T00:00:00Z",
  upgrade_required: false,
};

describe("getUpgradeState", () => {
  it("offers Pro to free users", () => {
    const state = getUpgradeState({ ...base, plan: "free", subscription_status: "inactive", upgrade_required: true });
    expect(state?.isPaid).toBe(false);
    expect(state?.nextPlan?.id).toBe("pro");
    expect(state?.atLimit).toBe(false);
  });

  it("offers Scale to Pro users and flags the last few videos", () => {
    const state = getUpgradeState({ ...base, usage_count: 45, remaining: 5 });
    expect(state?.nextPlan?.id).toBe("scale");
    expect(state?.nearLimit).toBe(true);
    expect(state?.atLimit).toBe(false);
  });

  it("does not nag with plenty of room left", () => {
    expect(getUpgradeState(base)?.nearLimit).toBe(false);
  });

  it("marks a Pro user who used everything as at the limit", () => {
    const state = getUpgradeState({ ...base, usage_count: 50, remaining: 0, upgrade_required: true });
    expect(state?.atLimit).toBe(true);
    expect(state?.nearLimit).toBe(false);
  });

  it("has nothing above Scale", () => {
    expect(getUpgradeState({ ...base, plan: "scale" })?.nextPlan).toBeNull();
  });

  it("never offers Stripe upgrades to App Store subscribers", () => {
    const state = getUpgradeState({ ...base, subscription_provider: "apple" });
    expect(state?.managedByAppStore).toBe(true);
    expect(state?.nextPlan).toBeNull();
  });
});

describe("upgradeReasonForError", () => {
  it("maps a paid user's 402 to the limit prompt", () => {
    expect(
      upgradeReasonForError("SUBSCRIPTION_REQUIRED", { billing: { plan: "pro", subscription_status: "active" } }, undefined),
    ).toEqual({ kind: "limit" });
  });

  it("maps a free user's 402 to the start prompt", () => {
    expect(upgradeReasonForError("SUBSCRIPTION_REQUIRED", { billing: { plan: "free" } }, undefined)).toEqual({ kind: "start" });
  });

  it("offers a bigger plan for a video it can handle", () => {
    expect(
      upgradeReasonForError("VIDEO_TOO_LONG", { duration_seconds: 7200, max_duration_seconds: 5400 }, 180),
    ).toEqual({ kind: "video_too_long", durationSeconds: 7200, maxSeconds: 5400 });
  });

  it("stays out of the way when no plan could take the video", () => {
    expect(upgradeReasonForError("VIDEO_TOO_LONG", { duration_seconds: 20000, max_duration_seconds: 5400 }, 180)).toBeNull();
    expect(upgradeReasonForError("VIDEO_TOO_LONG", { duration_seconds: 7200, max_duration_seconds: 10800 }, undefined)).toBeNull();
  });

  it("ignores unrelated errors", () => {
    expect(upgradeReasonForError("VIDEO_METADATA_UNAVAILABLE", null, 180)).toBeNull();
  });
});
