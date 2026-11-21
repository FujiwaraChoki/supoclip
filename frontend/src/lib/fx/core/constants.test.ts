// @vitest-environment node
import { describe, expect, it } from "vitest";

import { CAPTION_SAFE, HOOK_BAND, LLM_ALLOWED_EMOJI, TONE_EMOJI, captionSafe, reactionRect, unit } from "./constants";

const ASPECTS: [number, number][] = [[1080, 1920], [1080, 1080], [1920, 1080]];

describe("fx-core geometry constants (§11.3)", () => {
  it("unit is the short side over 1080", () => {
    expect(unit(1080, 1920)).toBe(1);
    expect(unit(1920, 1080)).toBe(1);
    expect(unit(720, 1280)).toBeCloseTo(2 / 3, 12);
    expect(unit(1080, 1080)).toBe(1);
  });

  it("reactionRect keeps the 9:16 placement and fits every aspect", () => {
    const v = reactionRect(1080, 1920);
    expect(v.x).toBeCloseTo(0.15 * 1080, 9);
    expect(v.y).toBeCloseTo(0.2 * 1920, 9);
    expect(v.w).toBeCloseTo(0.7 * 1080, 9);
    expect(v.h).toBeCloseTo(0.525 * 1080, 9);
    for (const [w, h] of ASPECTS) {
      const r = reactionRect(w, h);
      expect(r.w / r.h).toBeCloseTo(4 / 3, 9);
      expect(r.w).toBeLessThanOrEqual(0.7 * w + 1e-9);
      expect(r.h).toBeLessThanOrEqual(0.5 * h + 1e-9);
      expect(r.x + r.w / 2).toBeCloseTo(w / 2, 9);
      expect(r.y + r.h).toBeLessThanOrEqual(h);
      expect(r.x).toBeGreaterThanOrEqual(0);
    }
    const land = reactionRect(1920, 1080);
    expect(land.h).toBeCloseTo(540, 9);       // limited by the 0.50H box height, no overflow (AR-M7)
    expect(land.w).toBeCloseTo(720, 9);
  });

  it("captionSafe mirrors caption_safe: side fraction, px top/bottom", () => {
    expect(captionSafe(1080, 1920)).toEqual({ side: 0.06, top: 96, bottom: 192 });
    expect(captionSafe(1080, 1080)).toEqual({ side: 0.06, top: 54, bottom: 120 });
    expect(captionSafe(1920, 1080)).toEqual({ side: 0.06, top: 54, bottom: 120 });
    const small = captionSafe(720, 1280);
    expect(small.top).toBeCloseTo(64, 9);
    expect(small.bottom).toBeCloseTo(128, 9);
    expect(CAPTION_SAFE.side).toBe(0.06);
  });

  it("hook band and emoji tables match the Python constants", () => {
    expect(HOOK_BAND).toBe(0.2);
    expect(Object.keys(TONE_EMOJI).sort()).toEqual(
      ["angry", "awkward", "funny", "hype", "neutral", "sad", "shocked", "smug", "suspense", "wholesome"],
    );
    expect(TONE_EMOJI.hype).toBe("🔥");
    expect(TONE_EMOJI.neutral).toBe("✨");
    expect(LLM_ALLOWED_EMOJI).toHaveLength(42);
    expect(new Set(LLM_ALLOWED_EMOJI).size).toBe(42);
    expect(LLM_ALLOWED_EMOJI).toContain("❤️");
    expect(LLM_ALLOWED_EMOJI).toContain("⚠️");
  });
});
