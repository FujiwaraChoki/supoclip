import { estimateRetainerIncome, estimateWhopCampaign } from "./income-calculator";

describe("estimateWhopCampaign", () => {
  it("calculates gross views, applies the 10% Content Rewards fee, and selects a hosted plan", () => {
    const result = estimateWhopCampaign({
      clipsPerWeek: 12,
      viewsPerClip: 5000,
      ratePerThousand: 1.5,
      capPerClip: 150,
      sourceHours: 8,
    });
    expect(result.clipsPerMonth).toBe(52);
    expect(result.grossPerClip).toBeCloseTo(7.5);
    expect(result.netPerClip).toBeCloseTo(6.75);
    expect(result.grossMonthly).toBeCloseTo(390);
    expect(result.platformFee).toBeCloseTo(39);
    expect(result.afterFeeMonthly).toBeCloseTo(351);
    expect(result.capped).toBe(false);
    expect(result.plan?.name).toBe("Pro");
    expect(result.netAfterPlan).toBeCloseTo(341.01);
  });

  it("enforces the per-clip cap and treats zero as uncapped", () => {
    const capped = estimateWhopCampaign({
      clipsPerWeek: 3,
      viewsPerClip: 300000,
      ratePerThousand: 1,
      capPerClip: 150,
      sourceHours: 2,
    });
    expect(capped.grossPerClip).toBe(150);
    expect(capped.netPerClip).toBeCloseTo(135);
    expect(capped.capped).toBe(true);

    const uncapped = estimateWhopCampaign({
      clipsPerWeek: 3,
      viewsPerClip: 300000,
      ratePerThousand: 1,
      capPerClip: 0,
      sourceHours: 2,
    });
    expect(uncapped.grossPerClip).toBe(300);
    expect(uncapped.capped).toBe(false);
  });

  it("returns null hosted plan above Creator's 1,500-minute allowance", () => {
    const over = estimateWhopCampaign({
      clipsPerWeek: 10,
      viewsPerClip: 1000,
      ratePerThousand: 1,
      capPerClip: 0,
      sourceHours: 26,
    });
    expect(over.plan).toBeNull();
    expect(over.netAfterPlan).toBeNull();
  });
});

describe("estimateRetainerIncome", () => {
  it("computes monthly retainer gross, effective per-clip rate, and hosted plan fit", () => {
    const result = estimateRetainerIncome({
      clients: 2,
      retainerPerClient: 1200,
      clipsPerClient: 24,
      sourceHours: 20,
    });
    expect(result.grossMonthly).toBe(2400);
    expect(result.totalClips).toBe(48);
    expect(result.effectivePerClip).toBe(50);
    expect(result.plan?.name).toBe("Creator");
    expect(result.netAfterPlan).toBeCloseTo(2375.01);
  });

  it("scales up to the Team plan for high-hour agency workloads", () => {
    const team = estimateRetainerIncome({
      clients: 4,
      retainerPerClient: 1500,
      clipsPerClient: 30,
      sourceHours: 40,
    });
    expect(team.plan?.name).toBe("Team");
    expect(team.netAfterPlan).toBe(5931);

    const custom = estimateRetainerIncome({
      clients: 6,
      retainerPerClient: 1500,
      clipsPerClient: 30,
      sourceHours: 65,
    });
    expect(custom.plan).toBeNull();
    expect(custom.netAfterPlan).toBeNull();
  });
});
