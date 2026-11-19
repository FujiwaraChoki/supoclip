// Drawer registry (§4.3, VERBATIM types): how the caption, picture, overlay and retention drawer files plug into
// frameOps(). Each drawer file registers itself on import; core/all.ts imports every one of them.
import type { AccentCue, AccentVariant, CueOf, FxCaptionTemplate, FxCue, FxKind, FxWord } from "./types";
import type { DrawOp } from "./host";
import type { FxClock } from "./frame";
import type { PreparedScene } from "./scene";

export type Layer = "picture" | "pixel" | "behind" | "overlay" | "caption" | "hook" | "flash" | "cover" | "retention" | "loop";
/** Layers bottom → top (§6.5). "picture" holds no drawer ops; the base picture op is built by frame.ts.
 *  "behind" (big text + the person cutout) sits BELOW "pixel" (grade tint/vignette/glow, grain) so the person redrawn
 *  over the text keeps the frame's grade and grain, and the text sits in the graded scene like it was filmed (D7). */
export const LAYERS: readonly Layer[] = ["picture", "behind", "pixel", "overlay", "caption", "hook", "flash", "cover", "retention", "loop"];

export interface DrawerContext {
  scene: PreparedScene; clock: FxClock;
  t: number;            // edit time (clock.e)
  local: number;        // t - cue.start (seconds into the cue)
  dur: number;          // cue.end - cue.start (instants: drawer-defined window)
  face: { x: number; y: number; w: number; h: number; landmarks: number[] | null } | null;
        // OUTPUT px, interpolated, AFTER this frame's PictureState matrix (zoom × shake × transition) is applied to the
        // box and landmarks, so bars, doodles and behind text follow the moving face (AR-M6)
  picture: PictureState; // the frame's composed picture state (read-only), for overlays that move with the picture
  u: number;             // min(W,H)/1080 unit (AP-H10, AR-M7)
  rng: () => number;    // mulberry32(cue.seed) fresh per call
  hash: (n: number) => number;   // hash01(cue.seed, n)
}
export interface CueDrawer<K extends FxKind = FxKind> {
  kind: K; layer: Layer;
  window(cue: CueOf<K>): [number, number];                  // E interval in which it may draw (incl. intros/outros)
  ops(cue: CueOf<K>, dc: DrawerContext): DrawOp[];
}
export interface AccentDrawer { variant: AccentVariant; layer: Layer; window(cue: AccentCue): [number, number];
  ops(cue: AccentCue, dc: DrawerContext): DrawOp[]; hidesCaptions?: boolean }
export interface CaptionStyleDrawer {        // base captions for an FX template
  template: FxCaptionTemplate;
  line(chunk: CaptionChunk, dc: Omit<DrawerContext, "local" | "dur">): DrawOp[];   // draws one visible caption chunk
}
export interface PictureModifier {           // zoom/shake/transition geometry & filters folded into the picture op
  kind: "zoom" | "shake" | "transition" | "desaturate" | "glitch" | "grade";
  apply(state: PictureState, cue: FxCue | null, dc: DrawerContext): PictureState;
}
/** scale: zoom factor about the anchor; ax, ay: anchor as a FRACTION of the output frame (0.5, 0.5 = centre);
 *  dx, dy: output px translation after scaling; rot: radians about the frame centre; blurX: horizontal motion blur
 *  (output px); filter: CSS filter functions, joined in order; rgbSplit: channel offset (px, R left / B right);
 *  slices: horizontal bands (output px) redrawn shifted by dx. */
export interface PictureState { scale: number; ax: number; ay: number; dx: number; dy: number; rot: number;
  blurX: number; filter: string[]; rgbSplit: number; slices: { y: number; h: number; dx: number }[] }

/** The untouched picture: what every frame starts from before the modifiers run. */
export const IDENTITY_PICTURE: Readonly<PictureState> = Object.freeze({
  scale: 1, ax: 0.5, ay: 0.5, dx: 0, dy: 0, rot: 0, blurX: 0, filter: [], rgbSplit: 0, slices: [],
}) as Readonly<PictureState>;

/** AR-M5: after all modifiers, frame.ts raises `scale` to coverScale(state, W, H) so no frame corner is ever uncovered:
 *  s ≥ max((W|cosθ|+H|sinθ|)/W, (W|sinθ|+H|cosθ|)/H) + 2·max(|dx|/W, |dy|/H) + blurX/W. One rule for zoom, shake, whip,
 *  spin, zoom_through. Exported for tests. */
export function coverScale(state: PictureState, w: number, h: number): number {
  const c = Math.abs(Math.cos(state.rot)), s = Math.abs(Math.sin(state.rot));
  const formula = Math.max((w * c + h * s) / w, (w * s + h * c) / h)
    + 2 * Math.max(Math.abs(state.dx) / w, Math.abs(state.dy) / h) + state.blurX / w;
  // The formula above is the documented bound. Under rotation AND translation together (and for the channel /
  // slice / blur offsets the picture ops add) it is not always enough, so take the exact requirement too: the
  // inverse-mapped frame (rotated, shifted by every horizontal offset the picture ops draw) must fit the picture.
  const sideX = Math.abs(state.dx) + pictureSpreadX(state), sideY = Math.abs(state.dy);
  const exact = Math.max(
    (w * c + h * s + 2 * (sideX * c + sideY * s)) / w,
    (w * s + h * c + 2 * (sideX * s + sideY * c)) / h,
  );
  return Math.max(1, formula, exact);
}

/** Largest extra horizontal offset any picture draw of this state adds (blur ghosts, RGB split, glitch slices). */
export function pictureSpreadX(state: PictureState): number {
  let slice = 0;
  for (const s of state.slices) slice = Math.max(slice, Math.abs(s.dx));
  return Math.max(state.blurX / 2, Math.abs(state.rgbSplit), slice);
}

/** Visual for scene-level settings (grain, progress bar, loop crossfade, grade extras) that are not cues. */
export interface GlobalDrawer {
  id: string; layer: Layer;
  active(scene: PreparedScene, clock: FxClock): boolean;
  ops(dc: DrawerContext): DrawOp[];          // local = t, dur = edit duration; rng/hash seeded per (scene seed, id)
}

export interface CaptionChunk { key: string; words: (FxWord & { x: number; y: number; w: number; h: number; line: number;
    /** Look captions: the time the word appears (spoken onset + the look's lead), and whether it is the keyword. */
    shown?: number; kw?: boolean })[];
  start: number; end: number; px: number; centerX: number; centerY: number; scale: number;
  /** Look captions: the gap between laid-out words (px), so a partly revealed line can re-centre. */
  space?: number;
  /** The caption row (fraction of H) this chunk was placed on, when per-shot rows placed it (no placement). */
  row?: number }

const cueDrawers = new Map<FxKind, CueDrawer[]>();
const accentDrawers = new Map<AccentVariant, AccentDrawer>();
const captionStyles = new Map<FxCaptionTemplate, CaptionStyleDrawer>();
const pictureModifiers = new Map<PictureModifier["kind"], PictureModifier[]>();
const globalDrawers = new Map<string, GlobalDrawer>();

/** Several drawers may share a kind (e.g. a transition's geometry and its light-leak overlay); a re-registration
 *  with the same kind AND layer replaces the earlier one, so hot reloads and test re-imports do not stack. */
export function registerCue(d: CueDrawer): void {
  const list = (cueDrawers.get(d.kind) ?? []).filter((x) => x.layer !== d.layer);
  list.push(d);
  cueDrawers.set(d.kind, list);
}
export function registerAccent(d: AccentDrawer): void { accentDrawers.set(d.variant, d); }
export function registerCaptionStyle(d: CaptionStyleDrawer): void { captionStyles.set(d.template, d); }
/** One modifier per kind (the last registration wins). */
export function registerPicture(m: PictureModifier): void { pictureModifiers.set(m.kind, [m]); }
export function registerGlobal(d: GlobalDrawer): void { globalDrawers.set(d.id, d); }

export const cueDrawersFor = (kind: FxKind): readonly CueDrawer[] => cueDrawers.get(kind) ?? [];
export const accentDrawerFor = (variant: AccentVariant): AccentDrawer | undefined => accentDrawers.get(variant);
export const captionStyleFor = (template: string): CaptionStyleDrawer | undefined =>
  captionStyles.get(template as FxCaptionTemplate);
export const pictureModifiersFor = (kind: PictureModifier["kind"]): readonly PictureModifier[] => pictureModifiers.get(kind) ?? [];
export const listGlobalDrawers = (): readonly GlobalDrawer[] => [...globalDrawers.values()];

/** Which drawers are registered (tests and the deno selftest use it to prove every stub loaded). */
export function registrySnapshot() {
  return {
    cues: [...cueDrawers.entries()].map(([k, v]) => [k, v.map((d) => d.layer)] as const),
    accents: [...accentDrawers.keys()],
    captionStyles: [...captionStyles.keys()],
    picture: [...pictureModifiers.keys()],
    globals: [...globalDrawers.keys()],
  };
}
