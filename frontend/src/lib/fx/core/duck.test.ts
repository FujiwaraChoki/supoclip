// @vitest-environment node
import { describe, expect, it } from "vitest";

import { duckEnvelope, envelopeDbAt, speechSpans, type DuckOptions } from "./duck";

const bed: DuckOptions = {
  gain_db: -20, duck_db: -12, attack: 0.08, release: 0.35, fade_in: 0.5, fade_out: 0.8, out_duration: 10,
};

const strictlyIncreasing = (points: [number, number][]) =>
  points.every((p, i) => i === 0 || p[0] > points[i - 1][0]);

describe("fx-core duck (mirror of edit_fx/duck.py)", () => {
  describe("speechSpans", () => {
    it("merges words closer than the gap and sorts", () => {
      expect(speechSpans([[2.4, 2.8], [0, 0.3], [0.5, 0.9], [1.4, 1.8]])).toEqual([[0, 0.9], [1.4, 1.8], [2.4, 2.8]]);
      expect(speechSpans([[2, 2.4], [1.4, 1.8]])).toEqual([[1.4, 2.4]]);
    });
    it("merges exactly at the gap and respects a custom gap", () => {
      expect(speechSpans([[0, 1], [1.25, 2]])).toEqual([[0, 2]]);
      expect(speechSpans([[0, 1], [1.25, 2]], 0.1)).toEqual([[0, 1], [1.25, 2]]);
      expect(speechSpans([[0, 1], [1.5, 2]])).toEqual([[0, 1], [1.5, 2]]);
    });
    it("drops inverted words, keeps zero-length ones and handles overlaps", () => {
      expect(speechSpans([[1, 1], [2, 1.5], [0, 2], [0.5, 1]])).toEqual([[0, 2]]);
      expect(speechSpans([[3, 3]])).toEqual([[3, 3]]);
      expect(speechSpans([])).toEqual([]);
    });
  });

  describe("envelopeDbAt", () => {
    const pts: [number, number][] = [[0, -60], [1, -20], [2, -32]];
    it("interpolates linearly in dB and holds the ends", () => {
      expect(envelopeDbAt(pts, -1)).toBe(-60);
      expect(envelopeDbAt(pts, 0.5)).toBe(-40);
      expect(envelopeDbAt(pts, 1)).toBe(-20);
      expect(envelopeDbAt(pts, 1.5)).toBe(-26);
      expect(envelopeDbAt(pts, 9)).toBe(-32);
      expect(envelopeDbAt([], 1)).toBe(0);
    });
  });

  describe("duckEnvelope", () => {
    it("is fade in → base → fade out with no speech", () => {
      expect(duckEnvelope([], bed)).toEqual([[0, -60], [0.5, -20], [9.2, -20], [10, -60]]);
    });

    it("ducks under speech with attack before and release after", () => {
      const pts = duckEnvelope([[2, 4]], bed);
      expect(strictlyIncreasing(pts)).toBe(true);
      expect(pts).toEqual([[0, -60], [0.5, -20], [1.92, -20], [2, -32], [4, -32], [4.35, -20], [9.2, -20], [10, -60]]);
      expect(envelopeDbAt(pts, 3)).toBe(-32);
      expect(envelopeDbAt(pts, 1.96)).toBeCloseTo(-26, 9);
    });

    it("stays ducked between spans whose ramps would touch", () => {
      const pts = duckEnvelope([[2, 3], [3.3, 4]], bed);
      expect(envelopeDbAt(pts, 3.15)).toBe(-32);
      const apart = duckEnvelope([[2, 3], [4, 5]], bed);
      expect(envelopeDbAt(apart, 3.6)).toBe(-20);
    });

    it("adds the fades as a dB offset on top of the duck, never below the floor", () => {
      const pts = duckEnvelope([[0, 1]], bed);
      expect(pts[0]).toEqual([0, -60]);          // max(floor, -32 + (-40)) = floor
      expect(pts[1]).toEqual([0.5, -32]);        // ducked level where the fade in ends
      expect(envelopeDbAt(pts, 0.25)).toBeCloseTo(-46, 9);
      expect(strictlyIncreasing(pts)).toBe(true);
      const tail = duckEnvelope([[9.5, 10]], bed);
      expect(tail.map(([t]) => +t.toFixed(9))).toEqual([0, 0.5, 9.2, 9.42, 9.5, 10]);
      expect(tail[tail.length - 1]).toEqual([10, -60]);
      // At 9.42 the ramp has not started (-20) and the fade is 0.22/0.8 in: -20 + (-40)(0.275) = -31.
      expect(tail[3][1]).toBeCloseTo(-31, 9);
    });

    it("scales fades down when they do not fit", () => {
      const pts = duckEnvelope([], { ...bed, out_duration: 1, fade_in: 1, fade_out: 1 });
      expect(pts).toEqual([[0, -60], [0.5, -20], [1, -60]]);
    });

    it("without fades starts and ends on the level", () => {
      const pts = duckEnvelope([[0, 1]], { ...bed, fade_in: 0, fade_out: 0, out_duration: 3 });
      expect(pts).toEqual([[0, -32], [1, -32], [1.35, -20], [3, -20]]);
    });

    it("honours a custom floor and is the floor alone for an empty render", () => {
      expect(duckEnvelope([], { ...bed, floor_db: -80 })[0]).toEqual([0, -80]);
      expect(duckEnvelope([], { ...bed, out_duration: 0 })).toEqual([[0, -60]]);
    });

    it("clips speech to the render", () => {
      const pts = duckEnvelope([[-1, 0.2], [9.9, 12]], { ...bed, fade_in: 0, fade_out: 0 });
      expect(pts[0]).toEqual([0, -32]);
      expect(pts[pts.length - 1]).toEqual([10, -32]);
    });

    it("never produces a level outside [floor, base]", () => {
      const spans = speechSpans(Array.from({ length: 40 }, (_, i) => [i * 0.37, i * 0.37 + 0.2] as [number, number]), 0.1);
      const pts = duckEnvelope(spans, { ...bed, out_duration: 15 });
      expect(strictlyIncreasing(pts)).toBe(true);
      for (const [, db] of pts) {
        expect(db).toBeGreaterThanOrEqual(-60);
        expect(db).toBeLessThanOrEqual(-20);
      }
    });
  });
});
