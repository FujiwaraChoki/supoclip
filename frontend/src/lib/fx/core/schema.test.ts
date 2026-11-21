// @vitest-environment node
// The TS contract mirror (types.ts) against the JSON Schemas exported from Pydantic (backend/scripts/fx/gen_fixtures.py):
// a sample of every cue kind and a full FxScene, typed as the TS interfaces, must validate. Skipped when the schemas
// are missing unless FX_REQUIRE_FIXTURES=1. FX_SHARED_DIR overrides the shared/fx root.
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020";
import { describe, expect, it } from "vitest";

import type { FxCue, FxKind, FxScene } from "./types";

const SHARED = process.env.FX_SHARED_DIR
  ? resolve(process.env.FX_SHARED_DIR)
  : resolve(fileURLToPath(new URL(".", import.meta.url)), "../../../../../shared/fx");
const CUE_SCHEMA = join(SHARED, "fx_cue.schema.json");
const SCENE_SCHEMA = join(SHARED, "fx_scene.schema.json");
const REQUIRED = process.env.FX_REQUIRE_FIXTURES === "1";
const present = existsSync(CUE_SCHEMA) && existsSync(SCENE_SCHEMA);

const base = { seed: 2961888143, beat: "b3", origin: "auto" as const };

// One sample per kind, every field present (the TS types have no optional fields), typed so tsc checks the mirror.
const SAMPLES: { [K in FxKind]: Extract<FxCue, { kind: K }> } = {
  accent: { ...base, id: "b3.accent", kind: "accent", start: 1.2, end: 2.4, variant: "counter", text: "$2.5M", emoji: "💰",
    doodle: "sparkles", value: 2500000, prefix: "$", suffix: "M" },
  zoom: { ...base, id: "b3.zoom", kind: "zoom", start: 1.2, end: 1.9, mode: "punch", scale: 1.25, anchor: "face" },
  shake: { ...base, id: "b3.shake", kind: "shake", start: 1.2, end: 1.5, amp: 18, freq: 18 },
  flash: { ...base, id: "b3.flash", kind: "flash", start: 1.2, end: 1.3, color: "#FFFFFF", peak: 0.85 },
  glitch: { ...base, id: "b3.glitch", kind: "glitch", start: 1.2, end: 1.4, amount: 0.6 },
  desaturate: { ...base, id: "g.desat", kind: "desaturate", start: 0, end: 3, amount: 0.8, dim: 0.2, beat: "" },
  transition: { ...base, id: "g.cut1", kind: "transition", start: 4, end: 4, style: "whip", beat: "" },
  sfx: { ...base, id: "b3.sfx", kind: "sfx", start: 1.2, end: 1.2, sound: "cash_register", variant: 1, gain_db: -3 },
  bleep: { ...base, id: "b5.bleep", kind: "bleep", start: 5.1, end: 5.4, tone: true, cover: "bar" },
  speed: { ...base, id: "b6.speed", kind: "speed", start: 6, end: 7.5, rate: 1.25 },
  freeze: { ...base, id: "b7.freeze", kind: "freeze", start: 8, end: 8, hold: 0.6, label: "WAIT" },
  sticker: { ...base, id: "u1", kind: "sticker", start: 0.5, end: 2, sticker: "wait_for_it", text: "", origin: "user", beat: "" },
  reaction: { ...base, id: "b8.reaction", kind: "reaction", start: 9, end: 10.5, query: "mind blown", provider: "giphy",
    asset_id: "abc123", url: "https://media.giphy.com/media/abc123/giphy.mp4" },
};

const SCENE: FxScene = {
  version: 1, width: 1080, height: 1920, edit_duration: 12, out_duration: 12.3,
  warp: [
    { e0: 0, e1: 8, o0: 0, rate: 1, hold: 0 },
    { e0: 8, e1: 8, o0: 8, rate: 0, hold: 0.6 },
    { e0: 8, e1: 12, o0: 8.6, rate: 1.0810810810810811, hold: 0 },
  ],
  template: "stat_counter",
  cues: Object.values(SAMPLES).sort((a, b) => a.start - b.start || (a.id < b.id ? -1 : 1)),
  words: [
    { id: "w0", start: 0.1, end: 0.4, text: "we", highlight: false, run: "clip" },
    { id: "w1", start: 0.4, end: 0.9, text: "made", highlight: true, run: "clip" },
  ],
  caption: {
    template: "stat_counter", font: "Poppins-ExtraBold", size_px: 86, color: "#FFFFFF", accent: "#FFD400",
    stroke_color: "#000000", stroke_px: 8, shadow: true, background: null, y: 0.72, words_per_line: 3, uppercase: false,
    enabled: true, placements: [{ key: "[\"clip\",\"w0\"]", x: 540, y: 1300, scale: 1 }],
  },
  grade: "punchy", grain: 0.2, progress_bar: true, progress_color: "#FF7A1A", loop_ending: "crossfade",
  hook_window: [0.12, 3.5], hook_title_suppressed: false,
  face_track: { version: 1, space: "output", fps: 6, samples: [
    [0, [[0.4, 0.2, 0.2, 0.15, 0.98]]],
    [0.1667, [[0.41, 0.2, 0.2, 0.15, 0.97, 0.45, 0.25, 0.55, 0.25, 0.5, 0.3, 0.46, 0.33, 0.54, 0.33]]],
    [0.3333, []],
  ] },
  broll_windows: [[2, 3.5]], matte_windows: [{ start: 1, end: 2, person: true }], seed: 7,
};

function compile(path: string) {
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  return ajv.compile(JSON.parse(readFileSync(path, "utf8")));
}

describe.skipIf(!present && !REQUIRED)("fx-core types ↔ Pydantic JSON Schemas", () => {
  it("the schema files exist", () => {
    expect(existsSync(CUE_SCHEMA), CUE_SCHEMA).toBe(true);
    expect(existsSync(SCENE_SCHEMA), SCENE_SCHEMA).toBe(true);
  });

  it.each(Object.keys(SAMPLES) as FxKind[])("a %s cue validates", (kind) => {
    const validate = compile(CUE_SCHEMA);
    const ok = validate(SAMPLES[kind]);
    expect(validate.errors ?? [], kind).toEqual([]);
    expect(ok).toBe(true);
  });

  it("covers all 13 cue kinds", () => {
    expect(Object.keys(SAMPLES)).toHaveLength(13);
  });

  it("rejects an unknown key, a wrong literal and an unknown kind", () => {
    const validate = compile(CUE_SCHEMA);
    expect(validate({ ...SAMPLES.flash, extra: 1 })).toBe(false);
    expect(validate({ ...SAMPLES.zoom, mode: "dolly" })).toBe(false);
    expect(validate({ ...SAMPLES.flash, kind: "wobble" })).toBe(false);
    expect(validate({ ...SAMPLES.sfx, sound: "kazoo" })).toBe(false);
  });

  it("a full FxScene validates", () => {
    const validate = compile(SCENE_SCHEMA);
    const ok = validate(SCENE);
    expect(validate.errors ?? []).toEqual([]);
    expect(ok).toBe(true);
  });

  it("an FxScene with every optional part empty validates", () => {
    const validate = compile(SCENE_SCHEMA);
    const minimal: FxScene = { ...SCENE, warp: [], cues: [], words: [], caption: null, hook_window: null, face_track: null,
      broll_windows: [], matte_windows: [], grade: "none", grain: 0, progress_bar: false, loop_ending: "none" };
    expect(validate(minimal)).toBe(true);
    expect(validate({ ...minimal, version: 2 })).toBe(false);
  });
});
