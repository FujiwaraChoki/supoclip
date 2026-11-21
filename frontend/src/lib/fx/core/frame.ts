// One output frame (§6.5): frameOps() turns a prepared scene and a clock into the full, ordered DrawOp list.
// Pure and pixel-free (tested as JSON); rasterize() draws the result. The draw order, bottom → top:
//   picture (zoom × shake × transition, raised to coverScale; grade + desaturate filter; whip blur; glitch slices/RGB)
//   → behind (big text, then the person cutout) → pixel overlays (grade extras, grain: over the cutout too, D7)
//   → accents/stickers/overlays → captions → flash → bleep cover → retention → loop.
import "./all";
import { fnv1a32, hash01, mulberry32 } from "./rng";
import { TRANSITION_HALF, isFxTemplate, type FxCue } from "./types";
import { quantize, toEdit } from "./warp";
import type { DrawOp } from "./host";
import {
  IDENTITY_PICTURE, LAYERS, accentDrawerFor, captionStyleFor, cueDrawersFor, listGlobalDrawers, pictureModifiersFor,
  type CaptionChunk, type DrawerContext, type Layer, type PictureModifier, type PictureState,
} from "./registry";
import { applyMat, isIdentity, pictureMatrix, pictureOps, pictureScale, transformRect } from "./layout";
import type { FaceBox, PreparedScene } from "./scene";
import { baseCaptionOps } from "./captions/base";

export interface FxClock { e: number; o: number; frame: number }   // edit s, final s, final frame index

/** e = toEdit(warp, o), frame = round(o·30). */
export function clockAt(p: PreparedScene, o: number): FxClock {
  return { e: toEdit(p.warp, o), o, frame: Math.round(o * 30) };
}

/** Picture modifiers compose in this fixed order, then coverScale (WP9). Grade runs once with cue = null. */
export const PICTURE_ORDER: readonly PictureModifier["kind"][] = ["grade", "desaturate", "zoom", "shake", "transition", "glitch"];

/** Window used when no drawer declares one: spans [start, end); transitions ±TRANSITION_HALF around the cut. */
export function defaultWindow(cue: FxCue): [number, number] {
  if (cue.kind === "transition") {
    const half = TRANSITION_HALF[cue.style] ?? 0.1;
    return [cue.start - half, cue.start + half];
  }
  return [cue.start, cue.end];
}

/**
 * [a, b) for spans. An instant window (a == b) is active on the 30 fps frame the instant snaps to: the warp holds a
 * freeze at quantize(start) while cue times are stored rounded to the millisecond (1.233 vs 37/30), so an exact
 * compare would miss the hold. A freeze's window is therefore its whole hold plus the frame that plays on at e0
 * (and, under a speed ramp, the frame nearest the instant); F-clock drawers narrow it with clock.o.
 */
export function inWindow(t: number, w: readonly [number, number]): boolean {
  if (w[1] - w[0] <= 1e-9) return Math.abs(quantize(t) - quantize(w[0])) <= 1e-9;
  return t >= w[0] - 1e-9 && t < w[1];
}

const errors = new WeakMap<PreparedScene, string[]>();
/** Drawer exceptions caught while building frames of `p` (a broken decorative drawer never costs the frame). */
export const drawerErrors = (p: PreparedScene): readonly string[] => errors.get(p) ?? [];
function guard<T>(p: PreparedScene, what: string, fn: () => T, fallback: T): T {
  try {
    return fn();
  } catch (error) {
    const list = errors.get(p) ?? [];
    if (list.length < 50) list.push(`${what}: ${error instanceof Error ? error.message : String(error)}`);
    errors.set(p, list);
    return fallback;
  }
}

function makeContext(
  p: PreparedScene, clock: FxClock, seed: number, local: number, dur: number,
  face: DrawerContext["face"], picture: PictureState,
): DrawerContext {
  const s = seed >>> 0;
  return {
    scene: p, clock, t: clock.e, local, dur, face, picture, u: p.u,
    rng: mulberry32(s),
    hash: (n: number) => hash01(s, n),
  };
}

const cloneState = (s: Readonly<PictureState>): PictureState => ({ ...s, filter: [...s.filter], slices: s.slices.map((x) => ({ ...x })) });

/** The composed picture state at t (modifiers in PICTURE_ORDER; scale NOT yet raised to coverScale). */
export function pictureStateAt(p: PreparedScene, clock: FxClock, rawFace: FaceBox | null): PictureState {
  let state = cloneState(IDENTITY_PICTURE);
  const t = clock.e;
  for (const kind of PICTURE_ORDER) {
    const mods = pictureModifiersFor(kind);
    if (!mods.length) continue;
    if (kind === "grade") {
      if (p.scene.grade === "none") continue;
      for (const m of mods) {
        const dc = makeContext(p, clock, p.scene.seed, t, p.scene.edit_duration, rawFace, state);
        state = guard(p, "picture:grade", () => m.apply(state, null, dc), state);
      }
      continue;
    }
    for (const cue of p.cuesByKind.get(kind) ?? []) {
      if (!inWindow(t, defaultWindow(cue))) continue;
      for (const m of mods) {
        const dc = makeContext(p, clock, cue.seed, t - cue.start, cue.end - cue.start, rawFace, state);
        state = guard(p, `picture:${cue.id}`, () => m.apply(state, cue, dc), state);
      }
    }
  }
  return state;
}

/** A face box carried through the picture matrix: box → axis-aligned bounds, landmarks point by point (AR-M6). */
function transformFace(face: FaceBox | null, m: ReturnType<typeof pictureMatrix>): DrawerContext["face"] {
  if (!face) return null;
  if (isIdentity(m)) return { ...face, landmarks: face.landmarks ? [...face.landmarks] : null };
  const box = transformRect(m, face);
  let landmarks: number[] | null = null;
  if (face.landmarks) {
    landmarks = [];
    for (let i = 0; i + 1 < face.landmarks.length; i += 2) landmarks.push(...applyMat(m, face.landmarks[i], face.landmarks[i + 1]));
  }
  return { ...box, landmarks };
}

const frameCache = new WeakMap<PreparedScene, { e: number; o: number; frame: number; ops: DrawOp[] }>();

/** Full frame, bottom → top (§6.5 order). Memoised for the last clock per prepared scene (isPassthrough + render). */
export function frameOps(p: PreparedScene, clock: FxClock): DrawOp[] {
  const hit = frameCache.get(p);
  if (hit && hit.e === clock.e && hit.o === clock.o && hit.frame === clock.frame) return hit.ops;
  const ops = buildFrame(p, clock);
  frameCache.set(p, { e: clock.e, o: clock.o, frame: clock.frame, ops });
  return ops;
}

function buildFrame(p: PreparedScene, clock: FxClock): DrawOp[] {
  const t = clock.e, W = p.width, H = p.height;
  const rawFace = p.faceAt(t);
  const raw = pictureStateAt(p, clock, rawFace);
  const state: PictureState = { ...raw, scale: pictureScale(raw, W, H) };
  const matrix = pictureMatrix(raw, W, H);
  const face = transformFace(rawFace, matrix);

  const buckets = new Map<Layer, DrawOp[]>(LAYERS.map((l) => [l, []]));
  const push = (layer: Layer, ops: DrawOp[]) => { if (ops.length) buckets.get(layer)!.push(...ops); };
  let hideCaptions = false;
  const captionCueOps: DrawOp[] = [];

  for (const cue of p.cues) {
    if (cue.kind === "accent") {
      const d = accentDrawerFor(cue.variant);
      if (!d) continue;
      const w = guard(p, `window:${cue.id}`, () => d.window(cue), null);
      if (!w || !inWindow(t, w)) continue;
      if (d.hidesCaptions) hideCaptions = true;
      const dc = makeContext(p, clock, cue.seed, t - cue.start, cue.end - cue.start, face, state);
      const ops = guard(p, `accent:${cue.id}`, () => d.ops(cue, dc), []);
      if (d.layer === "caption") captionCueOps.push(...ops);
      else push(d.layer, ops);
      continue;
    }
    for (const d of cueDrawersFor(cue.kind)) {
      const w = guard(p, `window:${cue.id}`, () => d.window(cue as never), null);
      if (!w || !inWindow(t, w)) continue;
      const dc = makeContext(p, clock, cue.seed, t - cue.start, cue.end - cue.start, face, state);
      const ops = guard(p, `cue:${cue.id}`, () => d.ops(cue as never, dc), []);
      if (d.layer === "caption") captionCueOps.push(...ops);
      else push(d.layer, ops);
    }
  }

  const chunk = hideCaptions ? null : p.chunkAt(t);
  if (chunk) push("caption", captionOps(p, clock, chunk, face, state));
  push("caption", captionCueOps);

  for (const g of listGlobalDrawers()) {
    if (!guard(p, `global:${g.id}`, () => g.active(p, clock), false)) continue;
    const dc = makeContext(p, clock, fnv1a32(`${p.scene.seed}:${g.id}`), t, p.scene.edit_duration, face, state);
    push(g.layer, guard(p, `global:${g.id}`, () => g.ops(dc), []));
  }

  const out: DrawOp[] = [...pictureOps(raw, W, H)];
  for (const layer of LAYERS) out.push(...buckets.get(layer)!);
  return out;
}

function captionOps(p: PreparedScene, clock: FxClock, chunk: CaptionChunk, face: DrawerContext["face"], state: PictureState): DrawOp[] {
  if (!isFxTemplate(p.scene.template)) return [];
  const dc = makeContext(p, clock, fnv1a32(`${p.scene.seed}:${chunk.key}`), clock.e - chunk.start, chunk.end - chunk.start, face, state);
  const drawer = captionStyleFor(p.scene.template);
  return guard(p, `caption:${chunk.key}`, () => (drawer ? drawer.line(chunk, dc) : baseCaptionOps(chunk, dc)), []);
}

/** True ⇒ the output frame equals the input picture (P2 writes the input buffer as is, G8). */
export function isPassthrough(p: PreparedScene, clock: FxClock): boolean {
  const ops = frameOps(p, clock);
  if (ops.length !== 1) return false;
  const op = ops[0];
  return op.op === "picture" && isIdentity(op.transform) && op.filter === "" && op.alpha === 1 && op.blend === "source-over"
    && op.channel === "rgb" && op.dx === 0 && op.dy === 0 && !op.cutout && op.clip === null;
}

/** Output-px boxes of the caption on screen at the clock (studio drag / resize for FX templates). */
export function captionHitBoxes(p: PreparedScene, clock: FxClock): { key: string; x: number; y: number; w: number; h: number }[] {
  const chunk = p.chunkAt(clock.e);
  if (!chunk || !chunk.words.length) return [];
  const pad = Math.max(8 * p.u, chunk.px * 0.2);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const w of chunk.words) {
    x0 = Math.min(x0, w.x); y0 = Math.min(y0, w.y); x1 = Math.max(x1, w.x + w.w); y1 = Math.max(y1, w.y + w.h);
  }
  return [{ key: chunk.key, x: x0 - pad, y: y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad }];
}
