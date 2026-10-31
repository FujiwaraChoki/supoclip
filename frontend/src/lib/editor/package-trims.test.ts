import { describe, expect, it } from "vitest";
import { draft } from "./document.test-fixture";
import { applyKeepRanges, packageToKeepRanges, rescueKeepRanges, parseClock, type SurgicalEdits } from "./package-trims";

const edits = (o: Partial<SurgicalEdits> = {}): SurgicalEdits => ({
  new_start: "00:02.500", new_end: "00:28.000", internal_cuts: [], ...o,
});

describe("parseClock", () => {
  it("parses every supported format", () => {
    expect(parseClock("00:02.500")).toBe(2.5);
    expect(parseClock("01:05")).toBe(65);
    expect(parseClock("1:01:05.250")).toBe(3665.25);
    expect(parseClock("12.5")).toBe(12.5);
    expect(parseClock(7)).toBe(7);
  });
  it("rejects garbage", () => {
    for (const bad of ["", "abc", "1:2:3:4", "-3", "1:xx", null, undefined]) expect(parseClock(bad)).toBeNaN();
  });
});

describe("packageToKeepRanges", () => {
  it("trims to the window", () => {
    expect(packageToKeepRanges(edits(), 30)).toEqual([{ start: 2.5, end: 28 }]);
  });
  it("removes internal cuts (fails if cuts are ignored)", () => {
    const r = packageToKeepRanges(edits({ internal_cuts: [{ cut_start: "00:10.000", cut_end: "00:11.500" }] }), 30);
    expect(r).toEqual([{ start: 2.5, end: 10 }, { start: 11.5, end: 28 }]);
  });
  it("clamps cuts into the window and merges overlaps", () => {
    const r = packageToKeepRanges(edits({ internal_cuts: [
      { cut_start: "00:01", cut_end: "00:04" }, { cut_start: "00:10", cut_end: "00:12" },
      { cut_start: "00:11", cut_end: "00:14" }, { cut_start: "00:27", cut_end: "00:40" },
    ] }), 30);
    expect(r).toEqual([{ start: 4, end: 10 }, { start: 14, end: 27 }]);
  });
  it("drops fragments under 0.15s", () => {
    const r = packageToKeepRanges(edits({ internal_cuts: [{ cut_start: "00:03.000", cut_end: "00:20" }, { cut_start: "00:20.100", cut_end: "00:28" }] }), 30);
    expect(r).toEqual([{ start: 2.5, end: 3 }]);
  });
  it("returns [] when the result is the whole clip", () => {
    expect(packageToKeepRanges(edits({ new_start: "0:00", new_end: "00:30" }), 30)).toEqual([]);
    expect(packageToKeepRanges(edits({ new_start: "0:00", new_end: "00:45" }), 30)).toEqual([]);
  });
  it("returns [] for unusable input", () => {
    expect(packageToKeepRanges(null, 30)).toEqual([]);
    expect(packageToKeepRanges(edits({ new_start: "x" }), 30)).toEqual([]);
    expect(packageToKeepRanges(edits({ new_start: "00:10", new_end: "00:05" }), 30)).toEqual([]);
    expect(packageToKeepRanges(edits(), 0)).toEqual([]);
    expect(packageToKeepRanges(edits({ internal_cuts: [{ cut_start: "00:00", cut_end: "00:40" }] }), 30)).toEqual([]);
  });
  it("ignores unparseable cuts", () => {
    expect(packageToKeepRanges(edits({ internal_cuts: [{ cut_start: "bad", cut_end: "00:10" }] }), 30)).toEqual([{ start: 2.5, end: 28 }]);
  });
});

describe("applyKeepRanges", () => {
  it("maps clip time through the original timeline and replaces segments", () => {
    const original = { ...draft, segments: [{ id: "a", start: 10, end: 14 }, { id: "b", start: 20, end: 24 }] };
    const out = applyKeepRanges(draft, original, [{ start: 3, end: 5 }]);
    expect(out.segments.map(({ start, end }) => [start, end])).toEqual([[13, 14], [20, 21]]);
    expect(out.words).toBe(draft.words);
  });
  it("is a no-op for empty ranges", () => {
    expect(applyKeepRanges(draft, draft, [])).toBe(draft);
  });
});

describe("rescueKeepRanges", () => {
  it("handles missing packages", () => {
    expect(rescueKeepRanges(null, 30)).toEqual([]);
    expect(rescueKeepRanges(undefined, 30)).toEqual([]);
  });
  it("returns [] for an untouched REJECTED clip but keeps trims", () => {
    const untouched = edits({ new_start: "00:00", new_end: "00:30", total_seconds_shaved: 0 });
    expect(rescueKeepRanges({ clip_status: "REJECTED", surgical_edits: untouched }, 30)).toEqual([]);
    expect(rescueKeepRanges({ clip_status: "REJECTED", surgical_edits: edits({ total_seconds_shaved: 2.5 }) }, 30))
      .toEqual([{ start: 2.5, end: 28 }]);
    expect(rescueKeepRanges({ clip_status: "RESCUE_REQUIRED", surgical_edits: edits() }, 30))
      .toEqual([{ start: 2.5, end: 28 }]);
  });
});
