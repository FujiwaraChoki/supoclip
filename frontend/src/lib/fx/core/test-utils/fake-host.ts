// Test hosts without pixels: a deterministic text measure for frameOps snapshots, and a recording 2D context for
// rasterize() call-order tests. Test-only (never imported by fx-core itself).
import type { DrawOp, EmojiAtlas, FxCanvas, FxContext2D, FxHost } from "../host";
import type { SceneMeasureHost } from "../scene";
import type { FxCaptionStyle, FxCue, FxScene, FxWord } from "../types";

/** 0.55 em per character (code points), so layout snapshots do not depend on installed fonts. */
export const fakeMeasure = (text: string, _font: string, px: number): number => Math.round(Array.from(text).length * px * 0.55 * 1000) / 1000;

export const fakeMeasureHost: SceneMeasureHost = { fontFamily: (stem) => `FX-${stem}`, measure: fakeMeasure };

export type Call = [string, ...unknown[]];

/** A context that records every method call and property assignment, in order, as [name, ...args]. */
export function recordingContext(width = 1080, height = 1920): { ctx: FxContext2D; calls: Call[]; canvas: { width: number; height: number } } {
  const calls: Call[] = [];
  const canvas = { width, height };
  const state: Record<string, unknown> = { canvas, filter: "none", globalAlpha: 1, globalCompositeOperation: "source-over", letterSpacing: "0px" };
  const gradient = () => ({ addColorStop: (...a: unknown[]) => calls.push(["addColorStop", ...a]) });
  const special: Record<string, (...a: unknown[]) => unknown> = {
    measureText: (text: unknown) => ({ width: String(text).length * 10 }),
    getImageData: (_x: unknown, _y: unknown, w: unknown, h: unknown) => ({ data: new Uint8ClampedArray(Number(w) * Number(h) * 4), width: w, height: h }),
    createLinearGradient: () => gradient(),
    createRadialGradient: () => gradient(),
  };
  const ctx = new Proxy(state, {
    get(target, key: string) {
      if (key in target) return target[key];
      return (...args: unknown[]) => {
        calls.push([key, ...args]);
        return special[key]?.(...args);
      };
    },
    set(target, key: string, value) {
      target[key] = value;
      calls.push([`=${key}`, value]);
      return true;
    },
    has: (target, key) => key in target || ["letterSpacing"].includes(String(key)),
  }) as unknown as FxContext2D;
  return { ctx, calls, canvas };
}

/** A host whose canvases are recording contexts; drawPicture logs the op it received. */
export function fakeHost(options: { supportsFilter?: boolean; atlas?: EmojiAtlas | null } = {}): FxHost & { pictures: Extract<DrawOp, { op: "picture" }>[] } {
  const pictures: Extract<DrawOp, { op: "picture" }>[] = [];
  return {
    pictures,
    supportsFilter: options.supportsFilter ?? true,
    createCanvas: (w, h): FxCanvas => {
      const { ctx } = recordingContext(w, h);
      return { width: w, height: h, getContext: () => ctx };
    },
    atlas: () => options.atlas ?? null,
    fontFamily: (stem) => `FX-${stem}`,
    drawPicture: (_ctx, op) => { pictures.push(op); },
    frame0: () => null,
  };
}

// ----------------------------------------------------------------------------------------------- scene builders


/** "Our revenue grew 300% in one year and honestly nobody saw that coming" — 12 words, ~0.36 s each from 0.2 s. */
export function sampleWords(text = "Our revenue grew 300% in one year and honestly nobody saw that coming", start = 0.2, step = 0.36): FxWord[] {
  return text.split(" ").map((w, i) => ({
    id: `w${i}`, start: Math.round((start + i * step) * 1000) / 1000, end: Math.round((start + i * step + step * 0.85) * 1000) / 1000,
    text: w, highlight: /\d/.test(w), run: "clip",
  }));
}

export function captionStyle(overrides: Partial<FxCaptionStyle> = {}, width = 1080): FxCaptionStyle {
  const u = width / 1080;
  return {
    template: "marker_circle", font: "TikTokSans-Bold", size_px: 78 * u, color: "#FFFFFF", accent: "#FFD23F",
    stroke_color: "#000000", stroke_px: 9 * u, shadow: true, background: null, y: 0.7, words_per_line: 3,
    uppercase: false, enabled: true, placements: [], ...overrides,
  };
}

/** A minimal valid scene; captions on with `template` unless `caption: null` is passed. */
export function makeScene(overrides: Partial<FxScene> = {}): FxScene {
  const width = overrides.width ?? 1080, height = overrides.height ?? 1920;
  const template = overrides.template ?? "marker_circle";
  const duration = overrides.edit_duration ?? 5;
  return {
    version: 1, width, height, edit_duration: duration, out_duration: overrides.out_duration ?? duration, warp: [],
    template, cues: [], words: sampleWords(), caption: captionStyle({ template }, Math.min(width, height)),
    grade: "none", grain: 0, progress_bar: false, progress_color: "#FF7A1A", loop_ending: "none", hook_window: null,
    hook_title_suppressed: false, face_track: null, broll_windows: [], matte_windows: [], seed: 1234,
    ...overrides,
  };
}

const base = { seed: 7, beat: "b0", origin: "auto" as const };
/** One cue of every kind (13), spread over 0.5–4.5 s, for "every drawer registers and runs" tests. */
export function everyCueKind(): FxCue[] {
  return [
    { ...base, id: "b0.accent.circle", kind: "accent", start: 0.5, end: 1.4, variant: "circle", text: "revenue", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "" },
    { ...base, id: "b0.accent.counter", kind: "accent", start: 0.9, end: 2.0, variant: "counter", text: "300%", emoji: "", doodle: "sparkles", value: 300, prefix: "", suffix: "%" },
    { ...base, id: "b0.zoom", kind: "zoom", start: 0.9, end: 1.6, mode: "punch", scale: 1.2, anchor: "face" },
    { ...base, id: "b0.shake", kind: "shake", start: 0.9, end: 1.2, amp: 18, freq: 18 },
    { ...base, id: "b0.flash", kind: "flash", start: 0.9, end: 1.0, color: "#FFFFFF", peak: 0.85 },
    { ...base, id: "b1.glitch", kind: "glitch", start: 1.8, end: 2.0, amount: 0.6 },
    { ...base, id: "b1.desat", kind: "desaturate", start: 2.0, end: 2.6, amount: 0.8, dim: 0.2 },
    { ...base, id: "g.transition", kind: "transition", start: 2.5, end: 2.5, style: "whip" },
    { ...base, id: "b1.sfx", kind: "sfx", start: 0.9, end: 0.9, sound: "impact", variant: 0, gain_db: 0 },
    { ...base, id: "b2.bleep", kind: "bleep", start: 3.0, end: 3.3, tone: true, cover: "bar" },
    { ...base, id: "b2.speed", kind: "speed", start: 3.4, end: 3.8, rate: 2 },
    { ...base, id: "b3.freeze", kind: "freeze", start: 4.0, end: 4.0, hold: 0.5, label: "WAIT" },
    { ...base, id: "g.sticker", kind: "sticker", start: 1.0, end: 2.4, sticker: "wait_for_it", text: "" },
    { ...base, id: "b3.reaction", kind: "reaction", start: 4.1, end: 4.5, query: "shocked", provider: "giphy", asset_id: "", url: "" },
  ];
}
