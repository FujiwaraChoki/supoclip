// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { FreezeCue, FxCue, SpeedCue, WarpPieceJson } from "./types";
import {
  buildWarp, identityWarp, isNontrivial, quantize, toEdit, toOutput, warpFromJson, warpToJson, type Warp,
} from "./warp";

const base = { seed: 0, beat: "", origin: "auto" as const };
const speed = (id: string, start: number, end: number, rate: number): SpeedCue => ({ ...base, id, kind: "speed", start, end, rate });
const freeze = (id: string, at: number, hold: number): FreezeCue => ({ ...base, id, kind: "freeze", start: at, end: at, hold, label: "" });

const isWholeFrames = (t: number) => Math.abs(t * 30 - Math.round(t * 30)) < 1e-9;

function checkInvariants(w: Warp) {
  // Pieces tile [0, D] in E and [0, out] in F with no gaps.
  let e = 0;
  let o = 0;
  for (const p of w.pieces) {
    expect(p.e0).toBeCloseTo(e, 9);
    expect(p.o0).toBeCloseTo(o, 9);
    if (p.rate === 0) {
      expect(p.e1).toBe(p.e0);
      expect(p.hold).toBeGreaterThan(0);
      o += p.hold;
    } else {
      expect(p.e1).toBeGreaterThan(p.e0);
      o += (p.e1 - p.e0) / p.rate;
      e = p.e1;
    }
  }
  expect(e).toBeCloseTo(w.editDuration, 9);
  expect(o).toBeCloseTo(w.outDuration, 9);
}

describe("fx-core warp (mirror of edit_fx/warp.py)", () => {
  it("quantize rounds half up to the frame grid, never banker's", () => {
    expect(quantize(2.5 / 30)).toBe(3 / 30);     // Python round() would give 2 frames
    expect(quantize(0)).toBe(0);
    expect(quantize(1.016)).toBeCloseTo(1.0, 12);
    expect(quantize(1.02)).toBeCloseTo(31 / 30, 12);
  });

  it("is identity without speed or freeze cues", () => {
    const w = buildWarp([], 12.5);
    expect(w).toEqual(identityWarp(12.5));
    expect(w.pieces).toEqual([{ e0: 0, e1: 12.5, o0: 0, rate: 1, hold: 0 }]);
    expect(isNontrivial(w)).toBe(false);
    expect(warpToJson(w)).toEqual([]);
    expect(toOutput(w, 3.3)).toBe(3.3);
    expect(toEdit(w, 3.3)).toBe(3.3);
    expect(w.outDuration).toBe(12.5);
  });

  it("a freeze adds exactly its quantised hold and repeats the frozen instant", () => {
    const w = buildWarp([freeze("f", 2.01, 0.5)], 10);
    checkInvariants(w);
    expect(w.pieces).toEqual([
      { e0: 0, e1: 2, o0: 0, rate: 1, hold: 0 },
      { e0: 2, e1: 2, o0: 2, rate: 0, hold: 0.5 },
      { e0: 2, e1: 10, o0: 2.5, rate: 1, hold: 0 },
    ]);
    expect(w.outDuration).toBeCloseTo(10.5, 12);
    expect(toOutput(w, 2)).toBe(2);                  // start of the hold
    expect(toOutput(w, 3)).toBeCloseTo(3.5, 12);
    expect(toEdit(w, 2.2)).toBe(2);                  // constant across the hold
    expect(toEdit(w, 2.49)).toBe(2);
    expect(toEdit(w, 2.5)).toBeCloseTo(2, 12);
    expect(toEdit(w, 3.5)).toBeCloseTo(3, 12);
    expect(isNontrivial(w)).toBe(true);
  });

  it("holds snap to whole frames", () => {
    const w = buildWarp([freeze("f", 1, 0.71)], 4);
    expect(w.pieces[1].hold).toBeCloseTo(21 / 30, 12);
    expect(isWholeFrames(w.outDuration)).toBe(true);
  });

  it("handles a freeze at 0 and at the very end", () => {
    const atZero = buildWarp([freeze("a", 0, 0.4)], 3);
    checkInvariants(atZero);
    expect(atZero.pieces[0]).toEqual({ e0: 0, e1: 0, o0: 0, rate: 0, hold: 0.4 });
    expect(toOutput(atZero, 0)).toBe(0);
    expect(toOutput(atZero, 1)).toBeCloseTo(1.4, 12);

    const atEnd = buildWarp([freeze("z", 3, 0.4)], 3);
    checkInvariants(atEnd);
    expect(atEnd.pieces[atEnd.pieces.length - 1]).toEqual({ e0: 3, e1: 3, o0: 3, rate: 0, hold: 0.4 });
    expect(atEnd.outDuration).toBeCloseTo(3.4, 12);
    expect(toEdit(atEnd, 3.2)).toBe(3);
  });

  it("speed pieces end on whole frames and shorten the output", () => {
    const w = buildWarp([speed("s", 1, 3.01, 1.3)], 6);
    checkInvariants(w);
    const piece = w.pieces[1];
    expect(piece.e0).toBe(1);
    expect(piece.e1).toBe(3);
    const final = (piece.e1 - piece.e0) / piece.rate;
    expect(isWholeFrames(final)).toBe(true);
    expect(final).toBeCloseTo(quantize(2 / 1.3), 12);
    expect(isWholeFrames(w.outDuration)).toBe(true);
    expect(w.outDuration).toBeLessThan(6);
  });

  it("slow motion lengthens the output", () => {
    const w = buildWarp([speed("s", 2, 3, 0.5)], 5);
    checkInvariants(w);
    expect(w.outDuration).toBeCloseTo(6, 9);
    expect(toOutput(w, 2.5)).toBeCloseTo(3, 9);
  });

  it("clips speed cues to the edit; a later overlapping cue starts where the earlier one ends", () => {
    const w = buildWarp([speed("b", 2, 4, 2), speed("a", 1, 3, 1.5), speed("c", 4.5, 99, 2), speed("d", 5, 5.5, 3)], 6);
    checkInvariants(w);
    const speeds = w.pieces.filter((p) => p.rate !== 1 && p.rate !== 0);
    expect(speeds.map((p) => [p.e0, p.e1])).toEqual([[1, 3], [3, 4], [4.5, 6]]);   // "d" is swallowed by "c"
  });

  it("treats rate 1 speed cues as no-ops and gives every speed piece at least one frame", () => {
    expect(isNontrivial(buildWarp([speed("s", 1, 2, 1)], 3))).toBe(false);
    const tiny = buildWarp([speed("s", 1, 1 + 1 / 30, 4)], 3);
    expect(tiny.pieces[1].e1 - tiny.pieces[1].e0).toBeCloseTo(1 / 30, 12);
    expect(tiny.outDuration).toBeCloseTo(3, 9);
  });

  it("drops freezes inside a speed span but keeps one on its edge", () => {
    const w = buildWarp([speed("s", 1, 3, 2), freeze("in", 2, 0.5), freeze("edge", 3, 0.5)], 5);
    checkInvariants(w);
    const holds = w.pieces.filter((p) => p.rate === 0);
    expect(holds).toHaveLength(1);
    expect(holds[0].e0).toBe(3);
  });

  it("keeps only the first freeze at one instant", () => {
    const w = buildWarp([freeze("b", 1, 1), freeze("a", 1.001, 0.5)], 3);
    const holds = w.pieces.filter((p) => p.rate === 0);
    expect(holds).toHaveLength(1);
    expect(holds[0].hold).toBe(1);     // "b" starts first (raw start); "a" snaps onto the same frame and is dropped
    const tie = buildWarp([freeze("b", 1, 1), freeze("a", 1, 0.5)], 3).pieces.filter((p) => p.rate === 0);
    expect(tie.map((p) => p.hold)).toEqual([0.5]);   // equal starts: id order
  });

  it("round-trips E → F → E outside holds and is monotone", () => {
    const w = buildWarp([freeze("f1", 0.5, 0.3), speed("s", 1, 2.5, 1.25), freeze("f2", 3, 1), speed("t", 4, 5, 0.5)], 7);
    checkInvariants(w);
    for (let e = 0; e <= 7; e += 0.0137) {
      expect(toEdit(w, toOutput(w, e))).toBeCloseTo(e, 9);
    }
    let prev = -Infinity;
    for (let o = 0; o <= w.outDuration + 0.5; o += 0.01) {
      const e = toEdit(w, o);
      expect(e).toBeGreaterThanOrEqual(prev - 1e-12);
      prev = e;
    }
    expect(isWholeFrames(w.outDuration)).toBe(true);
  });

  it("extrapolates at rate 1 outside the timeline", () => {
    const w = buildWarp([freeze("f", 1, 0.5)], 2);
    expect(toOutput(w, -1)).toBe(-1);
    expect(toOutput(w, 3)).toBeCloseTo(3.5, 12);
    expect(toEdit(w, 3)).toBeCloseTo(2.5, 12);
  });

  it("warpFromJson restores what warpToJson wrote; [] is identity", () => {
    const w = buildWarp([freeze("f", 1, 0.5), speed("s", 2, 3, 2)], 4);
    const back = warpFromJson(warpToJson(w), 4);
    expect(back.pieces).toEqual(w.pieces);
    expect(back.outDuration).toBeCloseTo(w.outDuration, 12);
    expect(warpFromJson([], 4)).toEqual(identityWarp(4));
    expect(warpFromJson(null, 4)).toEqual(identityWarp(4));
    expect(identityWarp(0).pieces).toEqual([]);
  });

  it("warpFromJson reads malformed pieces as identity", () => {
    const gap = [{ e0: 0, e1: 1, o0: 0, rate: 1, hold: 0 }, { e0: 2, e1: 3, o0: 1, rate: 1, hold: 0 }];
    expect(warpFromJson(gap, 3)).toEqual(identityWarp(3));
    expect(warpFromJson([{ e0: 0, e1: 1, o0: 0, rate: Number.NaN, hold: 0 }], 1)).toEqual(identityWarp(1));
    expect(warpFromJson([{ e0: 0, e1: 0, o0: 0, rate: 0, hold: 0 }], 1)).toEqual(identityWarp(1));
  });

  it("warpFromJson never throws on corrupt stored JSON (like warp_from_json)", () => {
    const corrupt = (v: unknown) => warpFromJson(v as WarpPieceJson[], 2);
    expect(corrupt({ e0: 0 })).toEqual(identityWarp(2));                                    // not a list
    expect(corrupt([null])).toEqual(identityWarp(2));
    expect(corrupt(["x"])).toEqual(identityWarp(2));
    expect(corrupt([{ e0: 0, e1: 2, o0: 0, rate: 1, hold: null }])).toEqual(identityWarp(2));  // float(None) fails
    expect(corrupt([{ e0: "", e1: 2, o0: 0, rate: 1, hold: 0 }])).toEqual(identityWarp(2));    // float("") fails
    expect(corrupt([{ e0: 0, e1: 2, o0: 0, rate: 1 }]).pieces).toEqual([{ e0: 0, e1: 2, o0: 0, rate: 1, hold: 0 }]);
    expect(corrupt([{ e0: "0", e1: "2", o0: 0, rate: "0.5", hold: 0 }]).outDuration).toBe(4);
  });

  it("ignores cues of other kinds", () => {
    const other: FxCue = { ...base, id: "x", kind: "flash", start: 1, end: 1.2, color: "#FFFFFF", peak: 0.8 };
    expect(isNontrivial(buildWarp([other], 3))).toBe(false);
  });
});
