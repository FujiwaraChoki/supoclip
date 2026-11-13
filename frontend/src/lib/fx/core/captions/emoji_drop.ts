// `emoji_drop` (WP7, §11.1): karaoke captions (the spoken word pops in the accent colour) with emoji that land under
// the key word. `emoji`: one 160u atlas sprite springs up from 60u below (ω = 18, ζ = 0.35), scales 0 → 1.15 → 1 on
// the same spring, wobbles from ±10° with a 0.25 s decay, and pops away in its last 150 ms. `emoji_burst`: N sprites
// (5 or 8, from the cue value) at 96u fly out of the word like confetti: seeded launch angle (−150°..−30°) and speed
// (900–1500u/s), gravity 2400u/s², spin ±360°/s, fading over 0.7 s.
import type { DrawOp } from "../host";
import type { DrawerContext } from "../registry";
import type { AccentCue } from "../types";
import { captionSafe, type Rect } from "../constants";
import { clamp, clamp01, easeInQuad, easeOutBack, easeOutQuad, keyframes, lerp, progress } from "../easing";
import { spring, springOvershoot } from "../spring";
import { placeAt, spriteOp } from "../layout";
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";
import { anchorAt, chunkBox, standaloneAnchor, type WordAnchor } from "./anchor";
import type { PreparedScene } from "../scene";

const DEG = Math.PI / 180;

/** §11.1 emoji constants (px in u). */
export const EMOJI = {
  sizePx: 160,
  riseFrom: 60,
  omega: 18,
  zeta: 0.35,
  peakScale: 1.15,
  wobbleDeg: 10,
  wobbleHz: 2.5,
  wobbleTau: 0.25,
  popAway: 0.15,
  gap: 14,
} as const;

/** §11.1 burst constants (px in u, angles in degrees). */
export const BURST = {
  sizePx: 96,
  minAngle: -150,
  maxAngle: -30,
  minSpeed: 900,
  maxSpeed: 1500,
  gravity: 2400,
  spinDeg: 360,
  fade: 0.7,
  popIn: 0.06,
  defaultCount: 5,
  maxCount: 12,
} as const;

const SPRING = { omega: EMOJI.omega, zeta: EMOJI.zeta };
const OVERSHOOT = springOvershoot(EMOJI.zeta);
/** Time of the spring's first peak: π / ω_d. */
export const EMOJI_PEAK_T = Math.PI / (EMOJI.omega * Math.sqrt(1 - EMOJI.zeta * EMOJI.zeta));

/**
 * Landing scale `local` s into the cue: the ω = 18, ζ = 0.35 step response reshaped so its first peak is exactly 1.15
 * (the raw step overshoots to 1.31): scaled by 1.15 / 1.31 on the way up, then the remaining oscillation is scaled to
 * a 0.15 overshoot. The two halves meet at the peak (value 1.15, zero slope), so the motion stays a spring.
 */
export function emojiLandScale(local: number): number {
  if (local <= 0) return 0;
  const s = spring(local, { ...SPRING, from: 0, to: 1 });
  if (local < EMOJI_PEAK_T) return (s * EMOJI.peakScale) / (1 + OVERSHOOT);
  return 1 + ((s - 1) * (EMOJI.peakScale - 1)) / OVERSHOOT;
}

/** Pop-away over the last 150 ms of a `dur`-long cue: scale factor 1 → 1.2 → 0 and opacity 1 → 0. */
export function emojiPopAway(local: number, dur: number): { scale: number; alpha: number } {
  const x = local - (dur - EMOJI.popAway);
  if (x <= 0) return { scale: 1, alpha: 1 };
  return {
    scale: keyframes([{ t: 0, v: 1 }, { t: 0.05, v: 1.2, ease: easeOutQuad }, { t: EMOJI.popAway, v: 0, ease: easeInQuad }], x),
    alpha: 1 - easeInQuad(progress(x, 0, EMOJI.popAway)),
  };
}

/**
 * Rest centre of the emoji under the key word: below the whole caption block (so a two-line caption is never covered),
 * horizontally on the word (settled layout, so the caption's entrance never drags it), kept inside the safe area;
 * above the block when there is no room below.
 */
export function emojiRest(anchor: WordAnchor, w: number, h: number, u: number): [number, number] {
  const size = EMOJI.sizePx * u, gap = EMOJI.gap * u;
  const block = anchor.chunk ? chunkBox(anchor.chunk) : anchor.box;
  const wx = anchor.box.x + anchor.box.w / 2;
  const safe = captionSafe(w, h);
  const side = safe.side * w;
  const x = clamp(wx, side + size / 2, w - side - size / 2);
  const below = block.y + block.h + gap + size / 2;
  const y = below + size / 2 <= h - safe.bottom ? below : Math.max(safe.top + size / 2, block.y - gap - size / 2);
  return [x, y];
}

/**
 * Where an emoji (or burst) lands: the key word in the caption on screen at the cue start (the settled layout, so the
 * emoji never jumps when the line under it changes), else at t; when no on-screen caption holds the key word (a user
 * cue in a caption gap, words that never matched) the caption position, so the emoji still shows instead of vanishing.
 */
export function emojiAnchor(p: PreparedScene, cue: AccentCue, t: number): WordAnchor | null {
  return anchorAt(p, cue, cue.start, false) ?? anchorAt(p, cue, t, false) ?? standaloneAnchor(p, cue, cue.start, false);
}

function emojiOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const key = cue.emoji;
  if (!key) return [];
  const anchor = emojiAnchor(dc.scene, cue, dc.t);
  if (!anchor) return [];
  const u = dc.u, size = EMOJI.sizePx * u;
  const [x, y] = emojiRest(anchor, dc.scene.width, dc.scene.height, u);
  const local = dc.local;
  const dy = spring(local, { ...SPRING, from: EMOJI.riseFrom * u, to: 0 });
  const side = dc.hash(1) < 0.5 ? -1 : 1;
  const rot = side * EMOJI.wobbleDeg * DEG * Math.exp(-local / EMOJI.wobbleTau) * Math.cos(2 * Math.PI * EMOJI.wobbleHz * local);
  const pop = emojiPopAway(local, dc.dur);
  const scale = emojiLandScale(local) * pop.scale;
  if (scale <= 0 || pop.alpha <= 0) return [];
  return [spriteOp({ key, x: -size / 2, y: -size / 2, w: size, h: size, transform: placeAt(x, y + dy, rot, scale), alpha: pop.alpha })];
}

/** One burst particle's state `local` s after launch (output px, radians). */
export function burstParticle(i: number, local: number, origin: [number, number], u: number, hash: (n: number) => number) {
  const angle = lerp(BURST.minAngle, BURST.maxAngle, hash(10 + i)) * DEG;
  const speed = lerp(BURST.minSpeed, BURST.maxSpeed, hash(40 + i)) * u;
  const spin = lerp(-BURST.spinDeg, BURST.spinDeg, hash(70 + i)) * DEG;
  const t = Math.max(0, local);
  return {
    x: origin[0] + Math.cos(angle) * speed * t,
    y: origin[1] + Math.sin(angle) * speed * t + 0.5 * BURST.gravity * u * t * t,
    rot: spin * t + (hash(100 + i) - 0.5) * 0.6,
    scale: easeOutBack(progress(t, 0, BURST.popIn)),
    alpha: 1 - easeInQuad(progress(t, 0, BURST.fade)),
  };
}

export const burstCount = (cue: AccentCue): number =>
  clamp(Math.round(cue.value ?? BURST.defaultCount), 1, BURST.maxCount) || BURST.defaultCount;

/** Launch point: the key word's centre (settled layout). */
function burstOrigin(anchor: WordAnchor): [number, number] {
  const b: Rect = anchor.box;
  return [b.x + b.w / 2, b.y + b.h / 2];
}

function burstOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const key = cue.emoji;
  if (!key) return [];
  const anchor = emojiAnchor(dc.scene, cue, dc.t);
  if (!anchor) return [];
  const origin = burstOrigin(anchor);
  const size = BURST.sizePx * dc.u;
  const ops: DrawOp[] = [];
  for (let i = 0; i < burstCount(cue); i++) {
    const s = burstParticle(i, dc.local, origin, dc.u, dc.hash);
    if (s.alpha <= 0 || s.scale <= 0) continue;
    ops.push(spriteOp({ key, x: -size / 2, y: -size / 2, w: size, h: size, transform: placeAt(s.x, s.y, s.rot, s.scale), alpha: clamp01(s.alpha) }));
  }
  return ops;
}

registerCaptionStyle({
  template: "emoji_drop",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.emoji_drop),
});

registerAccent({
  variant: "emoji",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: emojiOps,
});

registerAccent({
  variant: "emoji_burst",
  layer: "overlay",
  // Sprites are gone after the 0.7 s fade; the cue may be longer (1 s), never draw past either.
  window: (cue) => [cue.start, Math.min(cue.end, cue.start + BURST.fade)],
  ops: burstOps,
});
