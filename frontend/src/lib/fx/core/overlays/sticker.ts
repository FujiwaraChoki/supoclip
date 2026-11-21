// Sticker cues (§11.2), both in the shared die-cut look (looks/sticker.ts, AP-H4):
//   text         ("overlay" layer) a sticker with cue.text, seeded colour and tilt (±8°), placed off the face, off the
//                caption and inside the safe area (out of the hook band while the hook title burns);
//   wait_for_it  ("retention" layer) "WAIT FOR IT…" in sun yellow in the top third, a gentle 2u bob at 2 Hz.
// Both slap in (1.3 → 1 in 90 ms) and pop out over the last 100 ms. Placement is decided once per cue (from the face
// and caption at the cue start), so a sticker never jumps while it is on screen.
import type { DrawOp } from "../host";
import type { Rect } from "../constants";
import { clamp01, easeInQuad } from "../easing";
import { inflate, placeAvoiding, round3, safeRect } from "../layout";
import { registerCue, type CueDrawer, type DrawerContext } from "../registry";
import type { PreparedScene } from "../scene";
import { slapAlpha, slapScale, stickerBox, stickerOps, stickerPalette, stickerTilt, STICKER_PALETTES } from "../looks/sticker";
import type { CueOf } from "../types";

type StickerCue = CueOf<"sticker">;

export const WAIT_FOR_IT_TEXT = "WAIT FOR IT…";
export const STICKER_EXIT = 0.1;
export const WAIT_FOR_IT_BOB = 2;      // u px
export const WAIT_FOR_IT_BOB_HZ = 2;
/** Resting sizes (× the sticker look's 72u letters): retention stickers read first, text stickers a touch smaller. */
export const WAIT_FOR_IT_SCALE = 1.15;
export const TEXT_STICKER_SCALE = 1.1;

/** What must stay clear at time t: the face (padded) and the caption on screen. Output px, no picture transform. */
export function stickerObstacles(p: PreparedScene, t: number): Rect[] {
  const out: Rect[] = [];
  const face = p.faceAt(t);
  if (face) out.push(inflate({ x: face.x, y: face.y, w: face.w, h: face.h }, 0.18 * Math.max(face.w, face.h)));
  const chunk = p.chunkAt(t);
  if (chunk && chunk.words.length) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const w of chunk.words) {
      x0 = Math.min(x0, w.x); y0 = Math.min(y0, w.y); x1 = Math.max(x1, w.x + w.w); y1 = Math.max(y1, w.y + w.h);
    }
    out.push(inflate({ x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, chunk.px * 0.25));
  } else if (p.scene.caption?.enabled) {
    // No FX caption on screen (legacy template or a gap): keep the ASS caption band clear.
    const cy = (p.captionYAt(t) ?? p.scene.caption.y) * p.height, band = p.scene.caption.size_px * 2.6;
    out.push({ x: 0, y: cy - band / 2, w: p.width, h: band });
  }
  return out;
}

/** True when the hook title burns (ASS, above everything) at any time during [start, end). */
export function hookOverlaps(p: PreparedScene, start: number, end: number): boolean {
  const hook = p.scene.hook_window;
  return !!hook && !p.scene.hook_title_suppressed && start < hook[1] && Math.max(end, start + 1e-6) > hook[0];
}

/** Centre and fitted scale of a sticker; deterministic for a cue. */
export function stickerPlacement(p: PreparedScene, cue: { start: number; end: number }, text: string, candidates: readonly [number, number][],
  rest = 1): { x: number; y: number; scale: number } {
  const area = safeRect(p.width, p.height, hookOverlaps(p, cue.start, cue.end));
  let scale = rest;
  let box = stickerBox(text, scale, p.u, p.measure);
  // Room for the slap-in overshoot and the tilt.
  const fit = Math.min(area.w / (box.w * 1.12), (area.h * 0.5) / box.h);
  if (fit < 1) { scale *= fit; box = stickerBox(text, scale, p.u, p.measure); }
  const placed = placeAvoiding(box.w, box.h, candidates, stickerObstacles(p, cue.start), area);
  return { x: placed.x + placed.w / 2, y: placed.y + placed.h / 2, scale };
}

/** Slap in, hold, pop out: [scale factor, alpha] at `local` seconds into a sticker that lasts `dur`. */
export function stickerMotion(local: number, dur: number): [number, number] {
  const left = dur - local;
  const out = left < STICKER_EXIT ? clamp01(left / STICKER_EXIT) : 1;
  const scale = slapScale(local) * (0.86 + 0.14 * easeInQuad(out));
  return [round3(scale), round3(slapAlpha(local) * out)];
}

const withAlpha = (ops: DrawOp[], alpha: number): DrawOp[] =>
  alpha >= 1 ? ops : ops.map((o) => (o.op === "text" ? { ...o, alpha: round3(o.alpha * alpha) } : o));

function textSticker(cue: StickerCue, dc: DrawerContext): DrawOp[] {
  const text = cue.text.trim();
  if (!text) return [];
  const p = dc.scene, W = p.width, H = p.height;
  const order: [number, number][] = [[0.5, 0.3], [0.27, 0.24], [0.73, 0.24], [0.5, 0.52], [0.28, 0.5], [0.72, 0.5], [0.5, 0.18],
    [0.18, 0.3], [0.82, 0.3], [0.18, 0.55], [0.82, 0.55], [0.18, 0.12], [0.82, 0.12], [0.5, 0.62], [0.18, 0.66], [0.82, 0.66]];
  // Seeded start among the three preferred spots, so a run of stickers does not stack in one place.
  const first = Math.floor(dc.hash(3) * 3);
  const candidates = [...order.slice(first, 3), ...order.slice(0, first), ...order.slice(3)].map(([x, y]) => [x * W, y * H] as [number, number]);
  const at = stickerPlacement(p, cue, text, candidates, TEXT_STICKER_SCALE);
  const [k, alpha] = stickerMotion(dc.local, dc.dur);
  if (alpha <= 0) return [];
  return withAlpha(stickerOps(text, at.x, at.y, stickerTilt(dc.hash(1), 8), at.scale * k, stickerPalette(dc.hash(2)), dc.u), alpha);
}

function waitForIt(cue: StickerCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene, W = p.width, H = p.height;
  const area = safeRect(W, H, hookOverlaps(p, cue.start, cue.end));
  const top = area.y;
  // Centres stay in the top third; sides are tried when the face fills the middle (square and landscape frames).
  const rows = [top + 0.09 * H, top + 0.12 * H].map((y) => Math.min(y, H / 3));
  const candidates: [number, number][] = [
    ...rows.map((y) => [0.5 * W, y] as [number, number]),
    ...rows.flatMap((y) => [[0.3 * W, y], [0.7 * W, y], [0.2 * W, y], [0.8 * W, y]] as [number, number][]),
  ];
  const at = stickerPlacement(p, cue, WAIT_FOR_IT_TEXT, candidates, WAIT_FOR_IT_SCALE);
  const [k, alpha] = stickerMotion(dc.local, dc.dur);
  if (alpha <= 0) return [];
  const bob = WAIT_FOR_IT_BOB * dc.u * Math.sin(2 * Math.PI * WAIT_FOR_IT_BOB_HZ * dc.local);
  return withAlpha(stickerOps(WAIT_FOR_IT_TEXT, at.x, round3(at.y + bob), stickerTilt(dc.hash(1), 6), at.scale * k, STICKER_PALETTES[1], dc.u), alpha);
}

registerCue({
  kind: "sticker",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: (cue, dc) => (cue.sticker === "text" ? textSticker(cue, dc) : []),
} satisfies CueDrawer<"sticker"> as unknown as CueDrawer);

registerCue({
  kind: "sticker",
  layer: "retention",
  window: (cue) => [cue.start, cue.end],
  ops: (cue, dc) => (cue.sticker === "wait_for_it" ? waitForIt(cue, dc) : []),
} satisfies CueDrawer<"sticker"> as unknown as CueDrawer);
