import type { PublicBillingPlan } from "./billing-plans";
import { estimateClips, formatRuntime, isLockedPreview, previewOf, suggestedPlan } from "./onboarding";

const plan = (id: "pro" | "scale", minuteLimit: number, youtubeMaxMinutes: number) =>
  ({ id, minuteLimit, youtubeMaxMinutes } as PublicBillingPlan);
const PLANS = [plan("pro", 600, 120), plan("scale", 1500, 180)];

describe("onboarding helpers", () => {
  it("reads a task's preview, as JSON or text", () => {
    expect(previewOf({ preview: { found: 6 } })).toEqual({ found: 6 });
    expect(previewOf({ preview: '{"found": 3}' })).toEqual({ found: 3 });
    expect(previewOf({ preview: null })).toBeNull();
    expect(isLockedPreview({ preview: { found: 6 } })).toBe(true);
    expect(isLockedPreview({ preview: { found: 6, unlocked_at: "2026-11-24T00:00:00Z" } })).toBe(false);
    expect(isLockedPreview({})).toBe(false);
  });

  it("suggests Creator for streamers and long videos, Pro otherwise", () => {
    expect(suggestedPlan(PLANS, "podcasts", 45 * 60)).toBe("pro");
    expect(suggestedPlan(PLANS, "streams", 30 * 60)).toBe("scale");
    expect(suggestedPlan(PLANS, "podcasts", 150 * 60)).toBe("scale");
    expect(suggestedPlan(PLANS, undefined, null)).toBe("pro");
  });

  it("estimates clips like the backend and formats lengths", () => {
    expect(estimateClips(10 * 60)).toBe(5);
    expect(estimateClips(26 * 60)).toBe(6);
    expect(estimateClips(4 * 3600)).toBe(10);
    expect(formatRuntime(1566)).toBe("26:06");
    expect(formatRuntime(3725)).toBe("1:02:05");
  });
});
