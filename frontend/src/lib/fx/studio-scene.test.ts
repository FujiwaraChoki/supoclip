// @vitest-environment node
// The studio's FxScene (studioScene) against the server's scene-from-document. Part 1 replays the cross-language
// fixtures in shared/fx/fixtures/scene/ (written by backend/scripts/fx/gen_scene_fixtures.py from editor_fx_render's
// build_scene path); skipped when the folder is missing unless FX_REQUIRE_FIXTURES=1. Part 2 pins the rules directly.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { DEFAULT_DOC_FX, type CaptionLook, type DocFx, type EditDocument } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";
import type { FaceTrack, FxCue } from "./core/types";
import {
  captionPlacements,
  faceRowSampler,
  placeFaceTrack,
  pyRound,
  resampleFaceTrack,
  storedCueId,
  studioCaptionRows,
  studioCaptionStyle,
  studioScene,
  type StudioSceneOptions,
} from "./studio-scene";

const SHARED = process.env.FX_SHARED_DIR
  ? resolve(process.env.FX_SHARED_DIR)
  : resolve(fileURLToPath(new URL(".", import.meta.url)), "../../../../shared/fx");
const SCENE_DIR = join(SHARED, "fixtures", "scene");
const REQUIRED = process.env.FX_REQUIRE_FIXTURES === "1";
const TOL = 1e-6;

interface SceneFixture {
  family: "scene"; case: string; fn: string;
  input: {
    document: EditDocument; source: { width: number; height: number }; base: "layout" | "clean";
    faceTrack: StudioSceneOptions["faceTrack"]; matte: boolean; broll: boolean;
    /** The server template fields the caption look reads (what caption_looks sends as a CaptionLook). */
    captionTemplate: Record<string, unknown> | null;
  };
  expected: unknown;
}

/** The session's CaptionLook for the fixture's template (== editor_service.caption_looks for that template). */
function lookOf(template: Record<string, unknown>): CaptionLook {
  return {
    animation: "none", wordPop: false, wordBox: false, wordBoxColor: null, emphasisColor: null, glow: false,
    highlightColor: (template.highlight_color as string | null) ?? null,
    strokeColor: (template.stroke_color as string | null) ?? null,
    strokeWidth: Math.trunc(Number("stroke_width" in template ? template.stroke_width : 3) || 0),
    shadow: !!template.shadow,
    backgroundColor: (template.background_color as string | null) ?? null,
  };
}

function diff(actual: unknown, expected: unknown, path = "$", out: string[] = []): string[] {
  if (typeof expected === "number" && typeof actual === "number") {
    if (!(Math.abs(actual - expected) <= TOL)) out.push(`${path}: ${actual} != ${expected}`);
  } else if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) out.push(`${path}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
    else expected.forEach((v, i) => diff(actual[i], v, `${path}[${i}]`, out));
  } else if (expected && typeof expected === "object") {
    if (!actual || typeof actual !== "object" || Array.isArray(actual)) out.push(`${path}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
    else {
      const a = actual as Record<string, unknown>, e = expected as Record<string, unknown>;
      for (const k of [...new Set([...Object.keys(a), ...Object.keys(e)])].sort()) {
        if (!(k in e)) out.push(`${path}.${k}: unexpected key`);
        else if (!(k in a)) out.push(`${path}.${k}: missing`);
        else diff(a[k], e[k], `${path}.${k}`, out);
      }
    }
  } else if (actual !== expected) out.push(`${path}: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
  return out;
}

const fixtures: { file: string; fixture: SceneFixture }[] = existsSync(SCENE_DIR)
  ? readdirSync(SCENE_DIR).filter((f) => f.endsWith(".json")).sort()
    .map((file) => ({ file, fixture: JSON.parse(readFileSync(join(SCENE_DIR, file), "utf8")) as SceneFixture }))
  : [];

describe.skipIf(!fixtures.length && !REQUIRED)("studioScene parity with shared/fx/fixtures/scene", () => {
  it("has fixtures", () => {
    expect(fixtures.length, `no scene fixtures in ${SCENE_DIR}`).toBeGreaterThanOrEqual(1);
  });
  for (const { file, fixture } of fixtures) {
    it(`${file}`, () => {
      const { document, source, base, faceTrack, matte, broll, captionTemplate } = fixture.input;
      const looks = captionTemplate ? { [document.captions.template ?? "default"]: lookOf(captionTemplate) } : undefined;
      const scene = studioScene(document, looks, {
        sourceWidth: source.width, sourceHeight: source.height, base, faceTrack, matte, broll,
        reactions: [],   // the fixtures render without reaction files
      });
      expect(diff(JSON.parse(JSON.stringify(scene)), fixture.expected)).toEqual([]);
    });
  }
});

// ----------------------------------------------------------------------------------------------- the rules

const base = { seed: 3, beat: "", origin: "auto" as const };
const cue = {
  zoom: (id: string, start: number, end: number): FxCue => ({ ...base, id, kind: "zoom", start, end, mode: "punch", scale: 1.2, anchor: "face" }),
  bleep: (id: string, start: number, end: number): FxCue => ({ ...base, id, kind: "bleep", start, end, tone: true, cover: "bar" }),
  freeze: (id: string, at: number, hold: number): FxCue => ({ ...base, id, kind: "freeze", start: at, end: at, hold, label: "" }),
  speed: (id: string, start: number, end: number, rate: number): FxCue => ({ ...base, id, kind: "speed", start, end, rate }),
  behind: (id: string, start: number, end: number): FxCue => ({ ...base, id, kind: "accent", start, end, variant: "behind",
    text: "WOW", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "" }),
  reaction: (id: string, start: number, end: number, url = "https://media.giphy.com/media/x/giphy.mp4"): FxCue => ({ ...base,
    id, kind: "reaction", start, end, query: "wow", provider: "giphy", asset_id: url ? "x" : "", url }),
};
const fxOf = (over: Partial<DocFx>): DocFx => ({ ...DEFAULT_DOC_FX, style: { ...DEFAULT_DOC_FX.style, intensity: "bold" }, ...over });
const opts: StudioSceneOptions = { width: 1080, height: 1920, sourceWidth: 1080, sourceHeight: 1920, base: "layout" };
const looks: Record<string, CaptionLook> = {
  slam: {
    animation: "pop", wordPop: true, wordBox: false, wordBoxColor: null, highlightColor: "#FF3B30", emphasisColor: null,
    strokeColor: "#000000", strokeWidth: 4, shadow: true, glow: false, backgroundColor: null, fx: true, fxStyle: "slam",
  },
};
const cutDoc = (over: Partial<EditDocument> = {}): EditDocument => ({
  ...draft,
  segments: [{ id: "one", start: 0, end: 2 }, { id: "two", start: 2.5, end: 3 }],
  words: [
    { id: "a", start: 0, end: 0.5, text: "Hello", highlight: false },
    { id: "b", start: 1, end: 1.5, text: "damn", highlight: true },
    { id: "c", start: 2.6, end: 2.9, text: "today", highlight: false },
    { id: "d", start: 2.1, end: 2.4, text: "cut", highlight: false },
  ],
  ...over,
});

describe("studioScene", () => {
  it("puts cues and words on the edit timeline, with run ids and censored bleeps", () => {
    const doc = cutDoc({ fx: fxOf({ cues: [cue.zoom("z", 1.8, 2.7), cue.bleep("b", 1, 1.5)] }) });
    const scene = studioScene(doc, looks, opts);
    expect(scene.edit_duration).toBeCloseTo(2.5);
    expect(scene.out_duration).toBeCloseTo(2.5);
    expect(scene.warp).toEqual([]);
    expect(scene.cues.map(({ id, start, end }) => [id, start, end])).toEqual([
      ["b", 1, 1.5], ["z", 1.8, 2], ["z@1", 2, 2.2],
    ]);
    expect(scene.words.map(({ text, run, start }) => [text, run, +start.toFixed(6)])).toEqual([
      ["Hello", "one", 0], ["d***", "one", 1], ["today", "two", 2.1],
    ]);
    expect(scene.caption).toBeNull();            // legacy template: captions are the text layer / ASS
    expect(scene.template).toBe("default");
    expect(scene.seed).toBe(0);
  });

  it("keeps speed and freeze cues only when they warp, and stretches the final duration", () => {
    const frozen = studioScene(cutDoc({ fx: fxOf({ cues: [cue.freeze("f", 0.5, 0.4)] }) }), looks, opts);
    expect(frozen.out_duration).toBeCloseTo(2.9, 6);
    expect(frozen.warp.length).toBeGreaterThan(1);
    expect(frozen.cues.map((c) => c.kind)).toEqual(["freeze"]);
    const inert = studioScene(cutDoc({ fx: fxOf({ cues: [cue.speed("s", 0.2, 1, 1)] }) }), looks, opts);
    expect(inert.cues).toEqual([]);
    expect(inert.warp).toEqual([]);
  });

  it("resolves the FX caption style from the editor's captions over the template look", () => {
    const doc = cutDoc({ captions: { ...draft.captions, template: "slam", size: 55, wordsPerLine: 3, uppercase: true, background: false } });
    const style = studioCaptionStyle(doc, looks, 1080)!;
    expect(style).toMatchObject({
      template: "slam", font: "TikTokSans-Regular", size_px: 55, color: "#FFFFFF", accent: "#FACC15",
      stroke_color: "#000000", stroke_px: pyRound((55 * 4) / 26), shadow: true, background: null, y: 0.78,
      words_per_line: 3, uppercase: true, enabled: true, placements: [],
    });
    expect(studioCaptionStyle({ ...doc, captions: { ...doc.captions, background: true } }, looks, 1080)!.background).toBe("#00000080");
    expect(studioCaptionStyle({ ...doc, captions: { ...doc.captions, enabled: false } }, looks, 1080)).toBeNull();
    expect(studioCaptionStyle(doc, looks, 540)!.size_px).toBe(28);   // round(27.5) == 28 (half to even)
    expect(studioScene(doc, looks, opts).caption?.template).toBe("slam");
  });

  it("turns caption transforms into placements; the captions layer moves and scales the style", () => {
    const doc = cutDoc({ captions: { ...draft.captions, template: "slam", wordsPerLine: 2 } });
    expect(captionPlacements(doc)).toEqual([]);
    const own = { ...doc, captionTransforms: { '["one","c"]': { x: 0, y: 0, scale: 1, size: 81 }, '["one","a"]': { x: 0.1, y: -0.2, scale: 1.5, size: null } } };
    expect(captionPlacements(own)).toEqual([
      { key: '["one","a"]', x: 0.1, y: -0.2, scale: 1.5 },
      { key: '["one","c"]', x: 0, y: 0, scale: 1.5 },
    ]);
    const layer = studioCaptionStyle({ ...doc, textTransforms: { captions: { x: 0.2, y: 0.05, scale: 1.2 } } }, looks, 1080)!;
    expect(layer.y).toBeCloseTo(0.83);
    expect(layer.size_px).toBe(pyRound(54 * 1.2));
    expect(layer.placements).toEqual([]);
    expect(layer.x).toBeCloseTo(0.2);
  });

  it("moves FX captions sideways with the captions layer; a caption's own transform replaces the layer's", () => {
    const doc = cutDoc({
      captions: { ...draft.captions, template: "slam", wordsPerLine: 2 },
      textTransforms: { captions: { x: -0.2, y: 0.05, scale: 1.25 } },
      captionTransforms: { '["one","a"]': { x: 0.1, y: -0.1, scale: 1.5, size: null } },
    });
    const style = studioCaptionStyle(doc, looks, 1080)!;
    expect(style.x).toBeCloseTo(-0.2);
    const [own] = style.placements;
    // Drawn at style + placement == the caption's own transform, not the layer's plus its own.
    expect(style.x! + own.x).toBeCloseTo(0.1);
    expect(style.y + own.y).toBeCloseTo(doc.captions.y - 0.1);
    expect(own.scale * 1.25).toBeCloseTo(1.5);
    expect(studioCaptionStyle(cutDoc({ captions: { ...draft.captions, template: "slam" } }), looks, 1080)!.x).toBe(0);
  });

  it("windows behind cues for the matte and B-roll, and the hook title", () => {
    const doc = cutDoc({
      hookTitle: "Big news",
      fx: fxOf({ cues: [cue.behind("w", 0.2, 1.2)] }),
      broll: [{ id: "br", start: 1.5, end: 2.8, offset: 0, query: "", footage: {
        provider: "pexels", id: 1, width: 1080, height: 1920, duration: 9, fileUrl: "", pageUrl: "", thumbnail: "", credit: "" } }],
    });
    expect(studioScene(doc, looks, opts).matte_windows).toEqual([{ start: 0.2, end: 1.2, person: false }]);
    const scene = studioScene(doc, looks, { ...opts, matte: true });
    expect(scene.matte_windows).toEqual([{ start: 0.2, end: 1.2, person: true }]);
    expect(scene.broll_windows.map(([a, b]) => [+a.toFixed(6), +b.toFixed(6)])).toEqual([[1.5, 2], [2, 2.3]]);
    expect(scene.hook_window).toEqual([0.12, 2.25]);
    expect(studioScene({ ...doc, fx: fxOf({ hookTitleSuppressed: true }) }, looks, opts).hook_title_suppressed).toBe(true);
  });

  it("draws a reaction only when its GIF is there", () => {
    const doc = cutDoc({ segments: [{ id: "a", start: 0, end: 1 }, { id: "b", start: 0, end: 1 }],
      fx: fxOf({ cues: [cue.reaction("r1", 0.2, 0.8), cue.reaction("r2", 0.1, 0.3, "")] }) });
    expect(studioScene(doc, looks, opts).cues.map((c) => c.id)).toEqual(["r1", "r1@1"]);
    expect(studioScene(doc, looks, { ...opts, reactions: [] }).cues).toEqual([]);
    expect(studioScene(doc, looks, { ...opts, reactions: ["r1"] }).cues.map((c) => c.id)).toEqual(["r1", "r1@1"]);
    expect(storedCueId("r1@1")).toBe("r1");
  });

  it("carries the globals of the draft", () => {
    const doc = cutDoc({ fx: fxOf({ seed: 42, grade: "teal_orange", grain: 0.4, progressBar: true, progressColor: "#00FF00", loopEnding: "crossfade" }) });
    expect(studioScene(doc, looks, opts)).toMatchObject({
      seed: 42, grade: "teal_orange", grain: 0.4, progress_bar: true, progress_color: "#00FF00", loop_ending: "crossfade",
    });
  });
});

describe("the session face track on the edit timeline", () => {
  const track: FaceTrack = {
    version: 1, space: "picture", fps: 6,
    samples: Array.from({ length: 61 }, (_, k) => [k / 6, [[k / 600, 0.2, 0.3, 0.2, 0.9]]] as [number, number[][]]),
  };
  const doc = cutDoc({ segments: [{ id: "one", start: 0, end: 2 }, { id: "two", start: 5, end: 7 }] });

  it("resamples at 6 fps of E through the segment cuts, plus both sides of each cut", () => {
    const resampled = resampleFaceTrack(doc, track);
    expect(resampled.fps).toBe(6);
    expect(resampled.samples.map(([t]) => t)).toContain(1.999);
    expect(resampled.samples.length).toBe(26);   // 0 .. 4 s in steps of 1/6, and 2 − 0.001 before the cut
    const at = (e: number) => resampled.samples.find(([t]) => Math.abs(t - e) < 1e-6)![1][0][0];
    expect(at(1)).toBeCloseTo(1 / 100, 6);
    expect(at(1.999)).toBeCloseTo(1.999 / 100, 6);
    expect(at(2)).toBeCloseTo(5 / 100, 6);        // E 2 plays S 5
    expect(at(2.5)).toBeCloseTo(5.5 / 100, 6);
  });

  it("interpolates between near samples and gives up across a long gap", () => {
    const sparse: FaceTrack = { version: 1, space: "picture", fps: 6, samples: [[0, [[0, 0, 0.1, 0.1, 1]]], [0.3, [[0.3, 0, 0.1, 0.1, 1]]], [2, [[1, 0, 0.1, 0.1, 1]]]] };
    const sample = faceRowSampler(sparse);
    expect(sample(0.15)[0][0]).toBeCloseTo(0.15);
    expect(sample(1.2)).toEqual([]);
    expect(sample(2.4)[0][0]).toBe(1);
    expect(sample(3)).toEqual([]);
  });

  it("places the track with the picture rect, and only for the base it was measured on", () => {
    const contained = { ...doc, framing: { aspect: "vertical" as const, fit: "contain" as const, zoom: 1, x: 0.5, y: 0.5 } };
    const scene = studioScene(contained, looks, { ...opts, sourceWidth: 1920, sourceHeight: 1080, base: "clean",
      faceTrack: { base: "clean", track } });
    expect(scene.face_track?.space).toBe("output");
    const [x, y, w, h] = scene.face_track!.samples.find(([t]) => t === 1)![1][0];
    expect(x).toBeCloseTo(0.01, 4);
    expect(y).toBeCloseTo(pyRound(((656 + 0.2 * 608) / 1920) * 1e4) / 1e4, 9);
    expect(w).toBeCloseTo(0.3, 4);
    expect(h).toBeCloseTo(pyRound(((0.2 * 608) / 1920) * 1e4) / 1e4, 9);
    expect(studioScene(contained, looks, { ...opts, sourceWidth: 1920, sourceHeight: 1080, base: "clean",
      faceTrack: { base: "layout", track } }).face_track).toBeNull();
    // Face cues without a usable track get an empty one: they fall back at draw time, like the server export.
    const zoomed = { ...contained, fx: fxOf({ cues: [cue.zoom("z", 0, 1)] }) };
    expect(studioScene(zoomed, looks, { ...opts, base: "clean", faceTrack: { base: "layout", track } }).face_track)
      .toEqual({ version: 1, space: "output", fps: 6, samples: [] });
    expect(placeFaceTrack(track, [0, 0, 1, 1])).toEqual({ ...track, space: "output" });
  });
});

describe("interview caption rows in the studio", () => {
  const rows = { y: 0.5, rows: [{ start: 0, end: 1, y: 0.5 }, { start: 1, end: 2.5, y: 0.65 }, { start: 2.5, end: 3, y: 0.76 }] };
  const interview = (over: Partial<EditDocument> = {}): EditDocument => cutDoc({
    captions: { ...draft.captions, template: "highlighter_swipe", y: 0.5 }, captionRows: rows, ...over,
  });

  it("maps the source rows through the cuts (a cut-out shot leaves no row, a reorder brings its rows along)", () => {
    expect(studioCaptionRows(interview())).toEqual([
      { start: 0, end: 1, y: 0.5 }, { start: 1, end: 2, y: 0.65 }, { start: 2, end: 2.5, y: 0.76 },
    ]);
    const reordered = interview({ segments: [{ id: "late", start: 2.5, end: 3 }, { id: "early", start: 0.5, end: 1.5 }] });
    expect(studioCaptionRows(reordered).map((r) => [r.start, r.end, r.y])).toEqual([[0, 0.5, 0.76], [0.5, 1, 0.5], [1, 1.5, 0.65]]);
    expect(studioCaptionStyle(interview(), looks, 1080)!.rows).toEqual(studioCaptionRows(interview()));
  });

  it("stops as soon as the user places the captions, and never applies to other looks or frames", () => {
    expect(studioCaptionRows(interview({ captions: { ...draft.captions, template: "highlighter_swipe", y: 0.62 } }))).toEqual([]);
    expect(studioCaptionRows(interview({ textTransforms: { captions: { x: 0, y: -0.05, scale: 1 } } }))).toEqual([]);
    expect(studioCaptionRows(interview({ textTransforms: { captions: { x: 0.1, y: 0, scale: 1.3 } } }))).not.toEqual([]);
    expect(studioCaptionRows(interview({ framing: { ...draft.framing, aspect: "square" } }))).toEqual([]);
    expect(studioCaptionRows(interview({ framing: { ...draft.framing, zoom: 1.2 } }))).toEqual([]);
    expect(studioCaptionRows(interview({ captions: { ...draft.captions, template: "slam", y: 0.5 } }))).toEqual([]);
    expect(studioCaptionRows(interview({ captions: { ...draft.captions, template: "highlighter_swipe", y: 0.5, enabled: false } }))).toEqual([]);
    expect(studioCaptionRows(interview({ captionRows: null }))).toEqual([]);   // sessions prepared before rows
    expect(studioCaptionStyle(interview({ captionRows: undefined }), looks, 1080)!.rows).toEqual([]);
  });
});
