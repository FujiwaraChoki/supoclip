// @vitest-environment node
import { describe, expect, it } from "vitest";

import { mappedBeats, mappedCues, suffixedId } from "./mapping";
import type { Beat, FlashCue, FxCue, SfxCue } from "./types";

const base = { seed: 1, beat: "b0", origin: "auto" as const };
const flash = (id: string, start: number, end: number): FlashCue =>
  ({ ...base, id, kind: "flash", start, end, color: "#FFFFFF", peak: 0.85 });
const sfx = (id: string, at: number): SfxCue => ({ ...base, id, kind: "sfx", start: at, end: at, sound: "pop", variant: 0, gain_db: 0 });
const span = (c: FxCue) => [c.id, +c.start.toFixed(9), +c.end.toFixed(9)];

describe("fx-core mappedCues (mirror of edit_fx/mapping.py)", () => {
  it("is the identity for one generation segment", () => {
    const cues = [flash("a", 1, 2), sfx("b", 0.5)];
    const out = mappedCues([["clip", 0, 10]], cues);
    expect(out).toEqual([sfx("b", 0.5), flash("a", 1, 2)]);   // sorted by start
    expect(out[0]).not.toBe(cues[1]);                          // copies
  });

  it("accepts object segments too", () => {
    expect(mappedCues([{ id: "s", start: 2, end: 5 }], [flash("a", 3, 4)]).map(span)).toEqual([["a", 1, 2]]);
  });

  // The B-roll cases of broll.test.ts / mapped_broll, replayed with cues.
  it("cuts spans at segment boundaries and places them on the cursor", () => {
    const segs: [string, number, number][] = [["s1", 0, 4], ["s2", 6, 10]];
    expect(mappedCues(segs, [flash("x", 3, 7)]).map(span)).toEqual([["x", 3, 4], ["x@1", 4, 5]]);
    // A span entirely inside the removed gap disappears.
    expect(mappedCues(segs, [flash("gap", 4.5, 5.5)])).toEqual([]);
  });

  it("drops pieces shorter than minPiece", () => {
    const segs: [string, number, number][] = [["s1", 0, 4], ["s2", 6, 10]];
    expect(mappedCues(segs, [flash("x", 3.98, 7)]).map(span)).toEqual([["x", 4, 5]]);
    expect(mappedCues(segs, [flash("x", 3.96, 7)]).map(span)).toEqual([["x", 3.96, 4], ["x@1", 4, 5]]);
    expect(mappedCues(segs, [flash("x", 3.98, 7)], 0.01).map(span)).toEqual([["x", 3.98, 4], ["x@1", 4, 5]]);
  });

  it("follows reordered segments", () => {
    const segs: [string, number, number][] = [["late", 5, 8], ["early", 0, 3]];
    expect(mappedCues(segs, [flash("a", 1, 2), flash("b", 6, 7)]).map(span)).toEqual([["b", 1, 2], ["a", 4, 5]]);
  });

  it("emits a cue in a repeated segment once per play with @k ids", () => {
    const segs: [string, number, number][] = [["a", 0, 2], ["b", 4, 6], ["a2", 0, 2]];
    const out = mappedCues(segs, [flash("f", 0.5, 1), sfx("s", 1.5)]);
    expect(out.map(span)).toEqual([["f", 0.5, 1], ["s", 1.5, 1.5], ["f@1", 4.5, 5], ["s@1", 5.5, 5.5]]);
  });

  it("maps instants by start: 50 ms lead, clamped into the segment", () => {
    const segs: [string, number, number][] = [["s1", 1, 3], ["s2", 5, 7]];
    const out = mappedCues(segs, [sfx("lead", 0.97), sfx("late", 4.96), sfx("out", 4)]);
    expect(out.map(span)).toEqual([["lead", 0, 0], ["late", 2, 2]]);
  });

  it("gives an instant at a segment end to the segment it ends unless play continues seamlessly", () => {
    // Gap after s1: the instant at 3 belongs to s1 (end of its piece, E = 2).
    expect(mappedCues([["s1", 1, 3], ["s2", 5, 7]], [sfx("e", 3)]).map(span)).toEqual([["e", 2, 2]]);
    // Last segment: same rule.
    expect(mappedCues([["s1", 1, 3]], [sfx("e", 3)]).map(span)).toEqual([["e", 2, 2]]);
    // Seamless continuation (not merged): the next segment owns it, emitted once.
    expect(mappedCues([["s1", 1, 3], ["s2", 3, 5]], [sfx("e", 3)]).map(span)).toEqual([["e", 2, 2]]);
    expect(mappedCues([["s1", 1, 3], ["s2", 3, 5]], [sfx("e", 3)])).toHaveLength(1);
  });

  it("sorts by (start, id) and keeps every other field", () => {
    const out = mappedCues([["s", 0, 5]], [flash("b", 1, 2), flash("a", 1, 3)]);
    expect(out.map((c) => c.id)).toEqual(["a", "b"]);
    expect(out[0]).toMatchObject({ kind: "flash", color: "#FFFFFF", peak: 0.85, seed: 1, beat: "b0", origin: "auto" });
  });

  it("keeps repeated ids within the id limit", () => {
    const long = "x".repeat(32);
    expect(suffixedId("abc", "@1", 32)).toBe("abc@1");
    expect(suffixedId(long, "@2", 32)).toBe(`${"x".repeat(30)}@2`);
    expect(suffixedId(long, "@12", 32)).toHaveLength(32);
    const out = mappedCues([["a", 0, 2], ["b", 0, 2]], [flash(long, 0.5, 1)]);
    expect(out.map((c) => c.id)).toEqual([long, `${"x".repeat(30)}@1`]);
  });

  it("maps beats as spans", () => {
    const beat: Beat = {
      id: "b0", kind: "emphasis", start: 1, end: 3, text: "", strength: 2, tone: "neutral", emoji: "", label: "",
      value: null, prefix: "", suffix: "", source: "llm", src_start: null, src_end: null, words: [],
    };
    expect(mappedBeats([["s", 2, 6]], [beat]).map((b) => [b.id, b.start, b.end])).toEqual([["b0", 0, 1]]);
  });
});
