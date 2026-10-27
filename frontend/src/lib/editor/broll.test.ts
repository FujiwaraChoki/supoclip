import { describe, expect, it } from "vitest";
import {
  brollAlpha,
  brollAt,
  insertBroll,
  mappedBroll,
  placeBroll,
  type BRollClip,
  type BRollFootage,
  type EditDocument,
} from "./document";
import { draft } from "./document.test-fixture";

const footage: BRollFootage = {
  provider: "pexels",
  id: 42,
  width: 1080,
  height: 1920,
  duration: 6,
  fileUrl: "https://videos.pexels.com/video-files/42/a.mp4",
  pageUrl: "",
  thumbnail: "",
  credit: "Someone",
};
const clip = (id: string, start: number, end: number, offset = 0): BRollClip => ({
  id,
  start,
  end,
  offset,
  query: "",
  footage,
});
const withBroll = (broll: BRollClip[], segments = [{ id: "s", start: 0, end: 20 }]): EditDocument => ({
  ...draft,
  segments,
  broll,
});

describe("B-roll on the timeline", () => {
  it("follows cuts and keeps playing through them, matching the export", () => {
    const doc = withBroll([clip("a", 1, 4, 0.5)], [
      { id: "one", start: 0, end: 2 },
      { id: "two", start: 3, end: 6 },
    ]);
    const pieces = mappedBroll(doc).map(({ start, end, offset, fadeIn, fadeOut }) => ({
      start,
      end,
      offset,
      fadeIn,
      fadeOut,
    }));
    expect(pieces).toEqual([
      { start: 1, end: 2, offset: 0.5, fadeIn: true, fadeOut: false },
      { start: 2, end: 3, offset: 2.5, fadeIn: false, fadeOut: true },
    ]);
  });

  it("dissolves only at its own edges", () => {
    const b = clip("a", 2, 5);
    expect(brollAlpha(b, 2)).toBe(0);
    expect(brollAlpha(b, 2.125)).toBeCloseTo(0.5);
    expect(brollAlpha(b, 3.5)).toBe(1);
    expect(brollAlpha(b, 4.95)).toBeCloseTo(0.2);
    expect(brollAt(withBroll([b]), 4.99)?.id).toBe("a");
    expect(brollAt(withBroll([b]), 5)).toBeNull();
  });

  it("inserts into the free space at the playhead, never over other B-roll", () => {
    const doc = withBroll([clip("a", 2, 5), clip("b", 6, 9)]);
    const added = insertBroll(doc, footage, 5.2, 20, "city");
    expect(added?.doc.broll?.map((b) => [b.start, b.end])).toEqual([
      [2, 5],
      [5.2, 6],
      [6, 9],
    ]);
    expect(insertBroll(doc, footage, 3, 20)).toBeNull();
    // Footage shorter than the default length limits the clip.
    expect(insertBroll(withBroll([]), { ...footage, duration: 1.5 }, 10, 20)?.doc.broll?.[0].end).toBe(11.5);
  });

  it("moves and resizes within its neighbours and its footage", () => {
    const doc = withBroll([clip("a", 2, 5), clip("b", 8, 10, 1)]);
    const moved = placeBroll(doc, "b", 4, 6, 20).broll!.find((b) => b.id === "b")!;
    expect([moved.start, moved.end]).toEqual([5, 7]);
    const grown = placeBroll(doc, "b", 8, 30, 20).broll!.find((b) => b.id === "b")!;
    // 6 s of footage minus the 1 s offset.
    expect([grown.start, grown.end]).toEqual([8, 13]);
    const shrunk = placeBroll(doc, "a", 2, 2.05, 20).broll!.find((b) => b.id === "a")!;
    expect(shrunk.end - shrunk.start).toBeCloseTo(0.2);
  });

  it("treats drafts saved before B-roll as having none", () => {
    expect(mappedBroll(draft)).toEqual([]);
    expect(brollAt(draft, 1)).toBeNull();
  });
});

describe("sequence timeline", () => {
  it("lays segments out back to back and maps edit time to source time", async () => {
    const { sequence, locate } = await import("./document");
    const doc = withBroll([], [
      { id: "a", start: 5, end: 8 },
      { id: "b", start: 0, end: 2 },
    ]);
    expect(sequence(doc).map((s) => [s.start, s.end])).toEqual([[0, 3], [3, 5]]);
    expect(locate(doc, 1)).toEqual({ index: 0, source: 6 });
    expect(locate(doc, 4)).toEqual({ index: 1, source: 1 });
    expect(locate(doc, 99)).toEqual({ index: 1, source: expect.closeTo(1.999, 3) });
  });
});
