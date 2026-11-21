// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  EASES, clamp, easeOutBack, easeOutCubic, easeOutQuad, inHoldOut, keyframes, lerp, progress, stepIndex,
} from "./easing";

describe("fx-core easing", () => {
  it("every ease runs 0 → 1 and clamps its input", () => {
    for (const [name, ease] of Object.entries(EASES)) {
      expect(ease(0), name).toBeCloseTo(0, 9);
      expect(ease(1), name).toBeCloseTo(1, 9);
      expect(ease(-3), name).toBeCloseTo(0, 9);
      expect(ease(7), name).toBeCloseTo(1, 9);
    }
  });

  it("monotone eases never go backwards", () => {
    const monotone = ["linear", "easeInQuad", "easeOutQuad", "easeInOutQuad", "easeInCubic", "easeOutCubic",
      "easeInOutCubic", "easeInSine", "easeOutSine", "easeInOutSine", "easeOutExpo", "easeInExpo"] as const;
    for (const name of monotone) {
      let prev = -Infinity;
      for (let x = 0; x <= 1.0001; x += 0.01) {
        const v = EASES[name](x);
        expect(v).toBeGreaterThanOrEqual(prev - 1e-12);
        prev = v;
      }
    }
  });

  it("easeOutCubic and easeOutBack have their shapes", () => {
    expect(easeOutCubic(0.5)).toBeCloseTo(0.875, 12);
    const peak = Math.max(...Array.from({ length: 101 }, (_, i) => easeOutBack(i / 100)));
    expect(peak).toBeGreaterThan(1.09);
    expect(peak).toBeLessThan(1.11);
  });

  it("helpers", () => {
    expect(clamp(5, 0, 1)).toBe(1);
    expect(lerp(2, 4, 0.25)).toBe(2.5);
    expect(progress(1.5, 1, 2)).toBe(0.5);
    expect(progress(3, 2, 2)).toBe(1);
    expect(progress(1, 2, 2)).toBe(0);
    expect(stepIndex(0.375, 8)).toBe(3);
    expect(stepIndex(3 / 8, 8)).toBe(3);   // exact boundaries stay on the new step despite float error
  });

  it("keyframes reproduce the slam scale curve (1.6 → 0.95 → 1.0 over 180 ms)", () => {
    const slam = [{ t: 0, v: 1.6 }, { t: 0.11, v: 0.95, ease: easeOutQuad }, { t: 0.18, v: 1, ease: easeOutQuad }];
    expect(keyframes(slam, -1)).toBe(1.6);
    expect(keyframes(slam, 0)).toBe(1.6);
    expect(keyframes(slam, 0.11)).toBeCloseTo(0.95, 12);
    expect(keyframes(slam, 0.18)).toBeCloseTo(1, 12);
    expect(keyframes(slam, 5)).toBe(1);
    expect(keyframes(slam, 0.055)).toBeCloseTo(1.6 - 0.65 * 0.75, 12);
    expect(keyframes([], 1)).toBe(0);
  });

  it("inHoldOut fades in, holds and fades out", () => {
    expect(inHoldOut(-0.1, 1, 0.2, 0.2)).toBe(0);
    expect(inHoldOut(0.1, 1, 0.2, 0.2)).toBeCloseTo(0.5, 12);
    expect(inHoldOut(0.5, 1, 0.2, 0.2)).toBe(1);
    expect(inHoldOut(0.95, 1, 0.2, 0.2)).toBeCloseTo(0.25, 12);
    expect(inHoldOut(1.1, 1, 0.2, 0.2)).toBe(0);
    expect(inHoldOut(0, 1, 0, 0)).toBe(1);
  });
});
