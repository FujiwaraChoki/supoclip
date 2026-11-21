// @vitest-environment node
import { describe, expect, it } from "vitest";

import { fnv1a32, hash01, mulberry32 } from "./rng";

// Canonical vectors from §4.2, identical in CPython and V8.
describe("fx-core rng", () => {
  it("fnv1a32 matches the canonical vectors", () => {
    expect(fnv1a32("")).toBe(2166136261);
    expect(fnv1a32("a")).toBe(3826002220);
    expect(fnv1a32("fx-v1")).toBe(2961888143);
    expect(fnv1a32("ünï")).toBe(458365526);
  });

  it("mulberry32(42) draws the canonical sequence", () => {
    const next = mulberry32(42);
    expect(next().toFixed(10)).toBe("0.6011037519");
    expect(next().toFixed(10)).toBe("0.4482905590");
    expect(next().toFixed(10)).toBe("0.8524657935");
  });

  it("mulberry32 is bit-exact with the Python reference", () => {
    const next = mulberry32(42);
    expect([next(), next(), next()]).toEqual([0.6011037519201636, 0.44829055899754167, 0.8524657934904099]);
  });

  it("hash01 matches the canonical vector and Python for large n", () => {
    expect(hash01(7, 3)).toBe(0.44320501177571714);
    expect(hash01(0xdeadbeef, 0)).toBe(0.9413696140982211);
    expect(hash01(0xdeadbeef, 1)).toBe(0.9373586478177458);
    expect(hash01(0xdeadbeef, 1000)).toBe(0.3416429585777223);
    expect(hash01(0xdeadbeef, 2 ** 31)).toBe(0.7433412983082235);
  });

  it("stays in [0, 1) and is deterministic per seed", () => {
    const a = mulberry32(123);
    const b = mulberry32(123);
    for (let i = 0; i < 1000; i += 1) {
      const x = a();
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThan(1);
      expect(b()).toBe(x);
    }
  });

  it("treats seeds as uint32", () => {
    expect(mulberry32(-1)()).toBe(mulberry32(0xffffffff)());
    expect(mulberry32(2 ** 32 + 5)()).toBe(mulberry32(5)());
  });
});
