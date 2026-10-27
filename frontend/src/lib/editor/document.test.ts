import { describe, expect, it } from "vitest";
import {
  activeWordIndex,
  captionGroups,
  editDuration,
  frameRect,
  mappedWords,
  outputSize,
  replaceWords,
  replaceCaptionGroup,
  renderSegments,
  splitSegment,
} from "./document";
import { draft } from "./document.test-fixture";

it("edits a caption phrase in source time without changing surrounding words or highlights", () => {
  const before = structuredClone(draft);
  const edited = replaceCaptionGroup(draft, ["b"], "bright world", 3);
  expect(edited.words.map(word => word.text)).toEqual(["Hello", "bright", "world", "today"]);
  expect(edited.words[0]).toEqual(draft.words[0]);
  expect(edited.words[2]).toEqual(draft.words[1]);
  expect(edited.words[3]).toEqual(draft.words[2]);
  expect(edited.words[1].start).toBeGreaterThanOrEqual(0);
  expect(edited.words[1].end).toBeLessThanOrEqual(1);
  expect(draft).toEqual(before);
  expect(replaceCaptionGroup(draft, ["b"], "", 3).words.map(word => word.text)).toEqual(["Hello", "today"]);
});

it("edits a phrase across reordered source segments in its displayed order", () => {
  const edited = replaceCaptionGroup(draft, ["c", "a"], "Last first", 3);
  expect(edited.words.find(word => word.text === "Last")?.start).toBeGreaterThanOrEqual(2);
  expect(edited.words.find(word => word.text === "first")?.end).toBeLessThanOrEqual(0.5);
  expect(edited.words.find(word => word.id === "b")).toEqual(draft.words[1]);
});

it("renders continuous splits together while preserving cuts, repeats, and the draft", () => {
  const segments = [
    { id: "first", start: 0, end: 1.02 },
    { id: "split", start: 1.02, end: 3 },
    { id: "cut", start: 4, end: 5 },
    { id: "repeat", start: 4, end: 5 },
    { id: "tail", start: 5, end: 6 },
  ];
  const before = structuredClone(segments);
  expect(renderSegments(segments)).toEqual([
    { id: "first", start: 0, end: 3 },
    { id: "cut", start: 4, end: 5 },
    { id: "repeat", start: 4, end: 6 },
  ]);
  expect(segments).toEqual(before);
});

describe("non-destructive edits", () => {
  it("splits without changing duration or original state", () => {
    const next = splitSegment(draft, 0, 1.1);
    expect(next.segments).toHaveLength(2);
    expect(editDuration(next)).toBeCloseTo(3);
    expect(draft.segments).toHaveLength(1);
    expect(splitSegment(draft, 0, 0.01)).toBe(draft);
  });
  it("maps captions through reordered and duplicated cuts", () => {
    const doc = {
      ...draft,
      segments: [
        { id: "a", start: 1, end: 2 },
        { id: "b", start: 0, end: 1 },
      ],
    };
    const words = mappedWords(doc);
    expect(words.map((w) => [w.text, w.start, w.end])).toEqual([
      ["world", 0, 0.5],
      ["Hello", 1, 1.5],
    ]);
    expect(words[0].highlight).toBe(true);
    expect(captionGroups(words, 1)).toHaveLength(2);
  });
  it("does not repeat a word split across contiguous segments, including overlapping speech", () => {
    const doc = { ...draft, words: [
      { id: "a", start: 0, end: 1, text: "blue", highlight: true },
      { id: "b", start: 0.2, end: 0.8, text: "light", highlight: false },
    ], segments: [{ id: "one", start: 0, end: 0.5 }, { id: "two", start: 0.5, end: 1 }] };
    const original = JSON.stringify(doc);
    expect(mappedWords(doc)).toEqual(doc.words);
    expect(JSON.stringify(doc)).toBe(original);
    expect(mappedWords({ ...doc, segments: [{ id: "one", start: 0, end: 1 }, { id: "two", start: 0, end: 1 }] }).map(w => w.text)).toEqual(["blue", "light", "blue", "light"]);
  });
  it("retains downstream word timings after a phrase correction", () => {
    const next = replaceWords(draft.words, "Hello beautiful world today", 3);
    expect(next.find((w) => w.text === "world")).toEqual(draft.words[1]);
    expect(next.find((w) => w.text === "today")).toEqual(draft.words[2]);
    expect(next.every((w) => w.end > w.start && w.end <= 3)).toBe(true);
    expect(replaceWords(draft.words, "Hello earth today", 3)[1]).toMatchObject({
      text: "earth",
      start: 1,
      end: 1.5,
    });
  });
  it("positions crop anchors and preserves original dimensions", () => {
    expect(
      frameRect({ ...draft.framing, x: 0 }, 1920, 1080, 1080, 1920).x,
    ).toBeCloseTo(0);
    const right = frameRect({ ...draft.framing, x: 1 }, 1920, 1080, 1080, 1920);
    expect(right.x + right.width).toBe(1080);
    expect(
      outputSize(
        { ...draft, framing: { ...draft.framing, aspect: "original" } },
        1280,
        720,
      ),
    ).toEqual({ width: 1280, height: 720 });
  });
  it("keeps the spoken word active until the next one starts", () => {
    const [group] = captionGroups(draft.words, 4);
    expect(activeWordIndex(group, 0)).toBe(0);
    expect(activeWordIndex(group, 0.9)).toBe(0);
    expect(activeWordIndex(group, 1)).toBe(1);
    expect(activeWordIndex(group, 2.4)).toBe(2);
  });
});
