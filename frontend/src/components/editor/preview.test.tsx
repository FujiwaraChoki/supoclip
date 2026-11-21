import { describe, expect, it } from "vitest";
import { DEFAULT_DOC_FX, fxWarp, type EditDocument } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";
import type { FxCue } from "@/lib/fx/core/types";
import { editTimeAt, warpPlaybackStep } from "./preview";

const base = { seed: 1, beat: "", origin: "auto" as const };
const freeze = (at: number, hold: number): FxCue => ({ ...base, id: `f${at}`, kind: "freeze", start: at, end: at, hold, label: "" });
const slow = (start: number, end: number, rate: number): FxCue => ({ ...base, id: `s${start}`, kind: "speed", start, end, rate });
const doc = (cues: FxCue[], segments = [{ id: "a", start: 0, end: 4 }]): EditDocument => ({
  ...draft,
  segments,
  fx: { ...DEFAULT_DOC_FX, style: { ...DEFAULT_DOC_FX.style, intensity: "bold" }, cues },
});

describe("the warped preview clock (final time F is the master)", () => {
  const held = doc([freeze(1, 0.5)]);
  const warp = fxWarp(held);

  it("advances F by wall time and the preview speed while the video keeps up", () => {
    const tick = warpPlaybackStep(held, warp, 0.5, 0.1, 1, { index: 0, source: 0.6 });
    expect(tick).toMatchObject({ rate: 1, seek: false, ended: false, index: 0 });
    expect(tick.o).toBeCloseTo(0.6);
    expect(tick.e).toBeCloseTo(0.6);
    expect(warpPlaybackStep(held, warp, 0.5, 0.1, 2, { index: 0, source: 0.7 }).o).toBeCloseTo(0.7);
  });

  it("follows the video within a frame instead of seeking", () => {
    const tick = warpPlaybackStep(held, warp, 0.5, 0.1, 1, { index: 0, source: 0.62 });
    expect(tick.o).toBeCloseTo(0.62);
    expect(tick.seek).toBe(false);
  });

  it("holds a freeze: rate 0, the frozen frame, F keeps running for the whole hold", () => {
    const entering = warpPlaybackStep(held, warp, 0.95, 0.1, 1, { index: 0, source: 1.04 });
    expect(entering.rate).toBe(0);
    expect(entering.e).toBe(1);
    expect(entering.o).toBeCloseTo(1.05);       // never pulled past the hold by the running video
    expect(entering.seek).toBe(true);            // back onto the frozen frame
    const inside = warpPlaybackStep(held, warp, 1.2, 0.1, 1, { index: 0, source: 1 });
    expect(inside).toMatchObject({ rate: 0, e: 1, seek: false });
    expect(inside.o).toBeCloseTo(1.3);
    const after = warpPlaybackStep(held, warp, 1.45, 0.1, 1, { index: 0, source: 1 });
    expect(after.rate).toBe(1);
    expect(after.e).toBeCloseTo(1.05);
  });

  it("sets the playback rate inside a speed ramp", () => {
    const ramp = doc([slow(2, 3, 0.5)]);
    const w = fxWarp(ramp);
    const tick = warpPlaybackStep(ramp, w, 2.5, 0.1, 1, { index: 0, source: 2.3 });
    expect(tick.rate).toBeCloseTo(0.5);
    expect(tick.e).toBeCloseTo(2.3);
    expect(tick.seek).toBe(false);
  });

  it("re-seeks when the video drifts more than a frame, and jumps segments", () => {
    expect(warpPlaybackStep(held, warp, 0.5, 0.1, 1, { index: 0, source: 0.4 }).seek).toBe(true);
    const cut = doc([freeze(0.5, 0.2)], [{ id: "a", start: 0, end: 2 }, { id: "b", start: 5, end: 7 }]);
    const w = fxWarp(cut);
    const tick = warpPlaybackStep(cut, w, 2.2, 0.02, 1, { index: 0, source: 2.01 });
    expect(tick.index).toBe(1);
    expect(tick.source).toBeCloseTo(5.01);   // F follows the video (within a frame), then jumps the cut
    expect(tick.seek).toBe(true);
    expect(warpPlaybackStep(cut, w, 2.2, 0.02, 1, null).seek).toBe(false);   // still seeking: no new seek
  });

  it("ends at the final duration and caps long pauses between ticks", () => {
    const end = warpPlaybackStep(held, warp, warp.outDuration - 0.01, 0.1, 1, { index: 0, source: 3.99 });
    expect(end.ended).toBe(true);
    expect(end.o).toBe(warp.outDuration);
    expect(warpPlaybackStep(held, warp, 0, 5, 1, null).o).toBeCloseTo(0.25);
  });
});

it("finds the edit time of a source time, preferring the playing segment", () => {
  const repeated = { ...draft, segments: [{ id: "a", start: 0, end: 2 }, { id: "b", start: 0, end: 2 }] };
  expect(editTimeAt(repeated, 1, 0.5)).toBeCloseTo(2.5);
  expect(editTimeAt(repeated, 0, 0.5)).toBeCloseTo(0.5);
  const cut = { ...draft, segments: [{ id: "a", start: 0, end: 1 }, { id: "b", start: 3, end: 4 }] };
  expect(editTimeAt(cut, 0, 3.5)).toBeCloseTo(1.5);
});
