import { describe, expect, it } from "vitest";
import {
  DEFAULT_DOC_FX,
  FX_KINDS,
  censoredMappedWords,
  documentNeedsFx,
  editDuration,
  finalDuration,
  fxWarp,
  hookTitleSuppressed,
  inertDocFx,
  mappedBleepSpans,
  mappedFx,
  mappedFxBeats,
  type DocFx,
  type EditDocument,
} from "./document";
import { draft } from "./document.test-fixture";
import type { Beat, FxCue } from "@/lib/fx/core/types";

const base = { seed: 1, beat: "", origin: "auto" as const };
const zoom = (id: string, start: number, end: number): FxCue =>
  ({ ...base, id, kind: "zoom", start, end, mode: "punch", scale: 1.2, anchor: "face" });
const sfx = (id: string, at: number): FxCue =>
  ({ ...base, id, kind: "sfx", start: at, end: at, sound: "whoosh", variant: 0, gain_db: 0 });
const bleep = (id: string, start: number, end: number): FxCue =>
  ({ ...base, id, kind: "bleep", start, end, tone: true, cover: "bar" });
const freeze = (id: string, at: number, hold: number): FxCue =>
  ({ ...base, id, kind: "freeze", start: at, end: at, hold, label: "" });
const speed = (id: string, start: number, end: number, rate: number): FxCue =>
  ({ ...base, id, kind: "speed", start, end, rate });

const withFx = (cues: FxCue[], segments = [{ id: "s", start: 0, end: 20 }], fx: Partial<DocFx> = {}): EditDocument => ({
  ...draft,
  segments,
  fx: { ...DEFAULT_DOC_FX, style: { ...DEFAULT_DOC_FX.style, intensity: "bold" }, cues, ...fx },
});
const times = (cues: FxCue[]) => cues.map(({ id, start, end }) => ({ id, start, end }));

describe("dynamic edits on the edit timeline (mappedFx, mirrors mapped_fx)", () => {
  it("follows cuts like B-roll: a span is cut by each segment and placed back to back", () => {
    const doc = withFx([zoom("z", 1, 4)], [{ id: "one", start: 0, end: 2 }, { id: "two", start: 3, end: 6 }]);
    expect(times(mappedFx(doc))).toEqual([
      { id: "z", start: 1, end: 2 },
      { id: "z@1", start: 2, end: 3 },
    ]);
  });

  it("drops a cue that a cut removes and a sliver shorter than the minimum piece", () => {
    const doc = withFx([zoom("gone", 2.2, 2.8), zoom("sliver", 1.99, 3.5)], [{ id: "one", start: 0, end: 2 }, { id: "two", start: 3, end: 6 }]);
    expect(times(mappedFx(doc))).toEqual([{ id: "sliver", start: 2, end: 2.5 }]);
  });

  it("maps instants by their start: kept inside a segment, rescued just before a cut, dropped inside a removed span", () => {
    const doc = withFx([sfx("in", 1.5), sfx("cut", 2.5), sfx("lead", 2.97)],
      [{ id: "one", start: 0, end: 2 }, { id: "two", start: 3, end: 6 }]);
    expect(times(mappedFx(doc))).toEqual([
      { id: "in", start: 1.5, end: 1.5 },
      { id: "lead", start: 2, end: 2 },
    ]);
  });

  it("plays repeated footage's cues once per repeat with suffixed ids", () => {
    const doc = withFx([sfx("hit", 0.5), zoom("z", 0, 1)], [{ id: "a", start: 0, end: 1 }, { id: "b", start: 0, end: 1 }]);
    expect(times(mappedFx(doc))).toEqual([
      { id: "z", start: 0, end: 1 },
      { id: "hit", start: 0.5, end: 0.5 },
      { id: "z@1", start: 1, end: 2 },
      { id: "hit@1", start: 1.5, end: 1.5 },
    ]);
  });

  it("follows reordered segments", () => {
    const doc = withFx([sfx("late", 4.5), sfx("early", 0.5)], [{ id: "b", start: 4, end: 5 }, { id: "a", start: 0, end: 1 }]);
    expect(times(mappedFx(doc))).toEqual([
      { id: "late", start: 0.5, end: 0.5 },
      { id: "early", start: 1.5, end: 1.5 },
    ]);
  });

  it("treats a split without a source jump as one continuous run (no extra pieces)", () => {
    const doc = withFx([zoom("z", 0.5, 2)], [{ id: "a", start: 0, end: 1 }, { id: "b", start: 1, end: 3 }]);
    expect(times(mappedFx(doc))).toEqual([{ id: "z", start: 0.5, end: 2 }]);
  });

  it("leaves out kinds switched off and drafts without dynamic edits", () => {
    const doc = withFx([zoom("z", 0, 1), sfx("s", 0.5)], undefined, { disabled: ["zoom"] });
    expect(mappedFx(doc).map((cue) => cue.kind)).toEqual(["sfx"]);
    expect(mappedFx(draft)).toEqual([]);
    expect(mappedFx({ ...draft, fx: null })).toEqual([]);
  });

  it("maps beats with the same span rules", () => {
    const beat: Beat = {
      id: "b0", kind: "hook", start: 1, end: 4, text: "x", strength: 2, tone: "hype", emoji: "", label: "", value: null,
      prefix: "", suffix: "", source: "llm", src_start: null, src_end: null, words: [],
    };
    const doc = withFx([], [{ id: "one", start: 0, end: 2 }, { id: "two", start: 3, end: 6 }], { beats: [beat] });
    expect(mappedFxBeats(doc).map(({ id, start, end }) => ({ id, start, end }))).toEqual([
      { id: "b0", start: 1, end: 2 },
      { id: "b0@1", start: 2, end: 3 },
    ]);
  });
});

describe("final duration", () => {
  it("is the edit duration without speed or freeze cues", () => {
    expect(finalDuration(draft)).toBe(editDuration(draft));
    expect(finalDuration(withFx([zoom("z", 0, 1)]))).toBeCloseTo(20);
  });

  it("adds freeze holds and follows speed ramps, after cuts", () => {
    const doc = withFx([freeze("f", 1, 0.5)], [{ id: "a", start: 0, end: 2 }, { id: "b", start: 3, end: 5 }]);
    expect(finalDuration(doc)).toBeCloseTo(4.5, 6);
    const slow = withFx([speed("sp", 1, 2, 0.5)], [{ id: "a", start: 0, end: 4 }]);
    expect(finalDuration(slow)).toBeCloseTo(5, 6);
    expect(fxWarp(slow)).toBe(fxWarp(slow));   // memoised per document
  });
});

describe("censored captions and globals", () => {
  it("censors words under a bleep, only with word timing", () => {
    const doc = withFx([bleep("b", 1, 1.5)], [{ id: "s", start: 0, end: 3 }]);
    expect(mappedBleepSpans(doc)).toEqual([[1, 1.5]]);
    expect(censoredMappedWords(doc).map((w) => w.text)).toEqual(["Hello", "w****", "today"]);
    const approx = { ...doc, fx: { ...doc.fx!, timing: "approx" as const } };
    expect(censoredMappedWords(approx).map((w) => w.text)).toEqual(["Hello", "world", "today"]);
    expect(censoredMappedWords(draft)).toEqual(draft.words);
  });

  it("knows when a draft needs fx-core", () => {
    expect(documentNeedsFx(draft)).toBe(false);
    expect(documentNeedsFx({ ...draft, captions: { ...draft.captions, template: "hormozi" } })).toBe(false);
    expect(documentNeedsFx({ ...draft, captions: { ...draft.captions, template: "slam" } })).toBe(true);
    expect(documentNeedsFx(withFx([zoom("z", 0, 1)]))).toBe(true);
    expect(documentNeedsFx(withFx([zoom("z", 0, 1)], undefined, { disabled: ["zoom"] }))).toBe(false);
    expect(documentNeedsFx(withFx([], undefined, { grade: "noir" }))).toBe(true);
    expect(documentNeedsFx(withFx([], undefined, { progressBar: true }))).toBe(true);
    // Only cues that survive the cuts count (== document_needs_fx's mapped_fx).
    expect(documentNeedsFx(withFx([zoom("z", 2.2, 2.8)], [{ id: "one", start: 0, end: 2 }, { id: "two", start: 3, end: 6 }]))).toBe(false);
  });

  it("writes the inert DocFx for a removal: off, no cues, every kind disabled, nothing to draw", () => {
    const removed = inertDocFx(withFx([zoom("z", 0, 1)], undefined, { grade: "noir", seed: 9 }).fx);
    expect(removed.style.intensity).toBe("off");
    expect(removed.cues).toEqual([]);
    expect(removed.beats).toEqual([]);
    expect(removed.disabled).toEqual([...FX_KINDS]);
    expect(removed.seed).toBe(9);
    expect(documentNeedsFx({ ...draft, fx: removed })).toBe(false);
  });

  it("hides the hook title only when the plan suppressed it", () => {
    expect(hookTitleSuppressed(draft)).toBe(false);
    const slam = (doc: EditDocument): EditDocument => ({ ...doc, captions: { ...doc.captions, template: "slam" } });
    expect(hookTitleSuppressed(slam(withFx([], undefined, { hookTitleSuppressed: true })))).toBe(true);
    // A draft that renders the legacy way (legacy template, nothing left to draw) keeps its title, as on the server.
    expect(hookTitleSuppressed(withFx([], undefined, { hookTitleSuppressed: true }))).toBe(false);
    expect(hookTitleSuppressed(withFx([zoom("z", 1, 2)], undefined, { hookTitleSuppressed: true }))).toBe(true);
  });
});
