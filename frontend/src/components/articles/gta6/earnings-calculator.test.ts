import { estimateEarnings } from "./earnings-calculator";

describe("estimateEarnings", () => {
  it("pays approved views at the campaign rate, per clip, up to the cap", () => {
    const result = estimateEarnings({ clipsPerWeek: 12, viewsPerClip: 3000, ratePerThousand: 1, capPerClip: 150, vodHours: 8 });
    expect(result.clipsPerMonth).toBe(52);
    expect(result.perClip).toBeCloseTo(3);
    expect(result.payout).toBeCloseTo(156);
    expect(result.capped).toBe(false);
  });

  it("stops each clip at the cap, and treats a zero cap as none", () => {
    const capped = estimateEarnings({ clipsPerWeek: 3, viewsPerClip: 400000, ratePerThousand: 1, capPerClip: 150, vodHours: 1 });
    expect(capped.perClip).toBe(150);
    expect(capped.capped).toBe(true);
    const uncapped = estimateEarnings({ clipsPerWeek: 3, viewsPerClip: 400000, ratePerThousand: 1, capPerClip: 0, vodHours: 1 });
    expect(uncapped.perClip).toBe(400);
  });

  it("picks the smallest hosted plan whose minutes cover the VODs", () => {
    const base = { clipsPerWeek: 1, viewsPerClip: 0, ratePerThousand: 1, capPerClip: 0 };
    expect(estimateEarnings({ ...base, vodHours: 10 }).plan?.name).toBe("Pro");
    expect(estimateEarnings({ ...base, vodHours: 11 }).plan?.name).toBe("Creator");
    expect(estimateEarnings({ ...base, vodHours: 25 }).plan?.name).toBe("Creator");
    const over = estimateEarnings({ ...base, vodHours: 26 });
    expect(over.plan).toBeNull();
    expect(over.net).toBeNull();
  });
});
