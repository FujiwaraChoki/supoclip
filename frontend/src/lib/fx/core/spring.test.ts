// @vitest-environment node
import { describe, expect, it } from "vitest";

import { decayingWobble, spring, springOvershoot, springSettleTime, springVelocity } from "./spring";

describe("fx-core spring", () => {
  it("starts at `from` and settles at `to` for every damping regime", () => {
    for (const zeta of [0.2, 0.35, 1, 2]) {
      const p = { omega: 18, zeta, from: 60, to: 0 };
      expect(spring(0, p)).toBe(60);
      expect(spring(-1, p)).toBe(60);
      expect(Math.abs(spring(3, p))).toBeLessThan(0.05);
    }
  });

  it("matches the analytic step-response overshoot (ω=18, ζ=0.35)", () => {
    const p = { omega: 18, zeta: 0.35, from: 0, to: 1 };
    let peak = 0;
    for (let t = 0; t < 1; t += 0.0005) peak = Math.max(peak, spring(t, p));
    expect(peak).toBeCloseTo(1 + springOvershoot(0.35), 3);
    // The peak time is π / ωd.
    const td = Math.PI / (18 * Math.sqrt(1 - 0.35 ** 2));
    expect(spring(td, p)).toBeCloseTo(peak, 3);
  });

  it("critically and over-damped springs never overshoot", () => {
    for (const zeta of [1, 1.5]) {
      for (let t = 0; t < 2; t += 0.01) expect(spring(t, { omega: 12, zeta, from: 0, to: 1 })).toBeLessThanOrEqual(1 + 1e-12);
    }
    expect(springOvershoot(1)).toBe(0);
  });

  it("honours an initial velocity", () => {
    const p = { omega: 10, zeta: 0.5, from: 0, to: 0, velocity: 5 };
    expect(springVelocity(0, p)).toBeCloseTo(5, 2);
    expect(spring(0.05, p)).toBeGreaterThan(0);
  });

  it("settle time bounds the remaining motion", () => {
    const p = { omega: 18, zeta: 0.35, from: 1, to: 0 };
    const ts = springSettleTime(p, 0.01);
    for (let t = ts; t < ts + 1; t += 0.01) expect(Math.abs(spring(t, p))).toBeLessThanOrEqual(0.01 + 1e-12);
  });

  it("decaying wobble decays with tau", () => {
    expect(decayingWobble(-1, 10, 2, 0.25)).toBe(0);
    expect(decayingWobble(0.125, 10, 2, 0.25)).toBeCloseTo(10 * Math.exp(-0.5), 9);
    expect(Math.abs(decayingWobble(2, 10, 2, 0.25, 1))).toBeLessThan(0.01);
  });
});
