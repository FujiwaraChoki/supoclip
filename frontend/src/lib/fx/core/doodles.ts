// Face doodles (§11.1 face_doodles, WP8): hand-drawn vector marks around the speaker's head — sparkles, arrows, a sweat
// drop, devil horns, an "!!" burst, a pulsing heart and an anger mark. Every mark is a seeded polyline (or a closed
// shape) authored relative to the face box, so it follows the face as it moves and scales with it as the picture
// zooms (the face box is already carried through the frame's picture transform, AR-M6).
//
// Look: crisp, sticker-bright fills and strokes with a dark under-stroke so they read on any background; the lines
// "boil" (re-jitter) at 8 fps from floor(local·8) so every frame is deterministic. Line marks draw in over 300 ms
// (dash reveal), filled marks pop in with a small overshoot, and everything fades out over the last 200 ms.
import type { DrawOp, Mat2D } from "./host";
import type { DrawerContext } from "./registry";
import type { DoodleKind } from "./types";
import { clamp, clamp01, easeOutBack, easeOutCubic, easeInQuad, keyframes, easeOutQuad, lerp, progress, stepIndex } from "./easing";
import { fxFamily, IDENTITY, pathOp, placeAt, textOp } from "./layout";

export interface DoodleFace { x: number; y: number; w: number; h: number }

/** Line marks draw in over this long (s). */
export const DOODLE_DRAW_IN = 0.3;
/** Fade out over the last 200 ms of the cue. */
export const DOODLE_FADE = 0.2;
/** Hand-drawn lines re-jitter at this rate (frames stay deterministic: the step is floor(local·8)). */
export const DOODLE_BOIL_FPS = 8;
/** The 7 doodle kinds, in contract order. */
export const DOODLE_KINDS: readonly DoodleKind[] = ["sparkles", "arrows", "sweat", "horns", "bang", "heart", "anger"];

/** Palette (sticker-bright, with dark outlines for contrast on any picture). */
export const DOODLE_COLORS = {
  ink: "#15131A",
  under: "rgba(0,0,0,0.42)",
  white: "#FFFFFF",
  yellow: "#FFE14D",
  burst: "#FFD400",
  red: "#FF2E3B",
  hornDark: "#3A0006",
  pink: "#FF3B6B",
  heartDark: "#4A0016",
  sweat: "#8FD8FF",
  sweatDark: "#0B3A55",
} as const;

type Pt = [number, number];
type PathOp = Extract<DrawOp, { op: "path" }>;

/** Everything a doodle needs: the face, its size scale, the reveal/fade envelope, the boil step and the seeded hash. */
interface Ctx {
  face: DoodleFace;
  cx: number; cy: number;   // face centre
  s: number;                // doodle scale (output px): the face width, kept sane for odd boxes
  u: number;
  W: number; H: number;
  local: number;
  draw: number;             // 0 → 1 reveal (easeOutCubic over DOODLE_DRAW_IN)
  alpha: number;            // fade-out envelope
  boil: number;             // 8 fps step index
  hash: (n: number) => number;
  line: number;             // main stroke width (output px)
}

// ----------------------------------------------------------------------------------------------- geometry helpers

const flat = (pts: readonly Pt[]): number[] => pts.flatMap(([x, y]) => [x, y]);

/** Length of an (open or closed) polyline: the dash reveal needs it in the path's own units. */
export function polylineLength(points: readonly number[], closed = false): number {
  let total = 0;
  const n = Math.floor(points.length / 2);
  for (let i = 1; i < n; i++) total += Math.hypot(points[2 * i] - points[2 * i - 2], points[2 * i + 1] - points[2 * i - 1]);
  if (closed && n > 1) total += Math.hypot(points[0] - points[2 * n - 2], points[1] - points[2 * n - 1]);
  return total;
}

/** Points of a quadratic Bézier a → b with control c (n + 1 samples). */
function quad(a: Pt, c: Pt, b: Pt, n: number): Pt[] {
  const out: Pt[] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, m = 1 - t;
    out.push([m * m * a[0] + 2 * m * t * c[0] + t * t * b[0], m * m * a[1] + 2 * m * t * c[1] + t * t * b[1]]);
  }
  return out;
}

/** Hand-drawn boil: every point moves by a seeded offset that changes 8 times a second. `salt` separates marks. */
function boilPts(pts: readonly Pt[], g: Ctx, salt: number, amp?: number): Pt[] {
  const a = amp ?? Math.max(1.5 * g.u, 0.007 * g.s);
  return pts.map(([x, y], i) => {
    const n = (g.boil * 7919 + salt * 131 + i * 2) >>> 0;
    return [x + (g.hash(n) - 0.5) * 2 * a, y + (g.hash(n + 1) - 0.5) * 2 * a];
  });
}

/** A stroked line mark: dark under-stroke, then the colour stroke, both revealed by a dash as `reveal` goes 0 → 1. */
function strokeMark(pts: readonly Pt[], color: string, width: number, g: Ctx, transform: Mat2D, reveal: number, closed = false): PathOp[] {
  if (reveal <= 0 || pts.length < 2) return [];
  const points = flat(pts);
  const length = polylineLength(points, closed);
  const dash: [number, number] | null = reveal >= 1 ? null : [length + 1, length + 1];
  const dashOffset = reveal >= 1 ? 0 : (length + 1) * (1 - reveal);
  const common = { points, closed, smooth: true, fill: null, cap: "round" as const, dash, dashOffset, transform, alpha: g.alpha };
  return [
    pathOp({ ...common, stroke: DOODLE_COLORS.under, strokePx: width + 4 * g.u }),
    pathOp({ ...common, stroke: color, strokePx: width }),
  ];
}

/** A filled mark with a dark outline (outline first, at double width, so the fill keeps its full shape). */
function fillMark(pts: readonly Pt[], fill: string, outline: string, g: Ctx, transform: Mat2D, alpha = 1, smooth = false): PathOp[] {
  if (pts.length < 3) return [];
  const points = flat(pts);
  const width = Math.max(6 * g.u, 0.03 * g.s);
  const common = { points, closed: true, smooth, cap: "round" as const, transform, alpha: g.alpha * alpha };
  return [
    pathOp({ ...common, fill: null, stroke: outline, strokePx: width * 2 }),
    pathOp({ ...common, fill, stroke: null, strokePx: 0 }),
  ];
}

/** Side (+1 right, −1 left) with more room for a mark of width `need`; the seeded preference wins when both fit. */
function sideFor(g: Ctx, need: number, salt: number): 1 | -1 {
  const prefer: 1 | -1 = g.hash(900 + salt) < 0.5 ? 1 : -1;
  const room = (side: 1 | -1) => (side > 0 ? g.W - (g.face.x + g.face.w) : g.face.x);
  if (room(prefer) >= need || room(prefer) >= room((-prefer) as 1 | -1)) return prefer;
  return (-prefer) as 1 | -1;
}

/** Pop-in scale (0 → 1.18 → 1 over the draw-in) for filled marks, delayed by `delay` seconds. */
const popScale = (local: number, delay = 0): number =>
  keyframes([{ t: 0, v: 0 }, { t: 0.16, v: 1.18, ease: easeOutQuad }, { t: DOODLE_DRAW_IN, v: 1, ease: easeOutQuad }], local - delay);

// ----------------------------------------------------------------------------------------------- the doodles

/** Four-point stars twinkling around the top of the head (3–5, scale pulse 0.8–1.2 at 3 Hz). */
function sparkles(g: Ctx): DrawOp[] {
  const { face, cx } = g;
  const count = 3 + Math.min(2, Math.floor(g.hash(11) * 3));
  const ops: DrawOp[] = [];
  const ox = cx, oy = face.y + face.h * 0.42;
  const rx = face.w * 0.5 + g.s * 0.28, ry = face.h * 0.5 + g.s * 0.22;
  for (let i = 0; i < count; i++) {
    const spread = (i + 0.5 + (g.hash(20 + i) - 0.5) * 0.5) / count;
    const angle = (lerp(-168, -12, spread) * Math.PI) / 180;
    const x = ox + Math.cos(angle) * rx, y = oy + Math.sin(angle) * ry;
    const r = g.s * (0.1 + 0.08 * g.hash(30 + i));
    const delay = i * 0.07;
    const pop = popScale(g.local, delay);
    if (pop <= 0) continue;
    const twinkle = 1 + 0.2 * Math.sin(2 * Math.PI * 3 * g.local + g.hash(40 + i) * 2 * Math.PI);
    const spin = (g.hash(50 + i) < 0.5 ? -1 : 1) * 0.35 * g.local + (g.hash(60 + i) - 0.5) * 0.5;
    const star: Pt[] = [];
    for (let k = 0; k < 8; k++) {
      const a = (k * Math.PI) / 4 - Math.PI / 2;
      const rr = k % 2 === 0 ? r : r * 0.3;
      star.push([Math.cos(a) * rr, Math.sin(a) * rr]);
    }
    const color = i % 2 === 0 ? DOODLE_COLORS.yellow : DOODLE_COLORS.white;
    ops.push(...fillMark(boilPts(star, g, 70 + i, Math.max(1, 0.05 * r)), color, DOODLE_COLORS.ink, g, placeAt(x, y, spin, pop * twinkle)));
  }
  return ops;
}

/** Two curved arrows swinging in from both sides and poking at the face. */
function arrows(g: Ctx): DrawOp[] {
  const { face, s } = g;
  const ops: DrawOp[] = [];
  ([-1, 1] as const).forEach((side, i) => {
    const local = g.local - i * 0.08;
    const reveal = easeOutCubic(progress(local, 0, DOODLE_DRAW_IN));
    if (reveal <= 0) return;
    const edge = side > 0 ? face.x + face.w : face.x;
    const end: Pt = [edge + side * s * 0.1, face.y + face.h * 0.46];
    const start: Pt = [edge + side * s * 0.62, face.y - face.h * 0.12];
    const control: Pt = [start[0] + side * s * 0.05, end[1] + face.h * 0.12];
    const shaft = boilPts(quad(start, control, end, 9), g, 100 + i);
    // Poke toward the face along the arrow's final direction (2.5 Hz) once drawn.
    const [ax, ay] = shaft[shaft.length - 2], [bx, by] = shaft[shaft.length - 1];
    const len = Math.hypot(bx - ax, by - ay) || 1, dx = (bx - ax) / len, dy = (by - ay) / len;
    const poke = reveal >= 1 ? s * 0.035 * Math.sin(2 * Math.PI * 2.5 * local) : 0;
    const transform: Mat2D = [1, 0, 0, 1, dx * poke, dy * poke];
    ops.push(...strokeMark(shaft, DOODLE_COLORS.white, g.line, g, transform, reveal));
    const head = progress(reveal, 0.7, 1);
    if (head > 0) {
      const hl = s * 0.15 * easeOutBack(head);
      const spread = (28 * Math.PI) / 180;
      const back = (a: number): Pt => [bx - hl * (dx * Math.cos(a) - dy * Math.sin(a)), by - hl * (dx * Math.sin(a) + dy * Math.cos(a))];
      ops.push(...strokeMark([back(spread), [bx, by], back(-spread)], DOODLE_COLORS.white, g.line, g, transform, 1));
    }
  });
  return ops;
}

/** A sweat drop sliding 40u down from the temple. */
function sweat(g: Ctx, dur: number): DrawOp[] {
  const { face, s } = g;
  const side = sideFor(g, s * 0.2, 3);
  const dw = s * 0.2, dh = s * 0.3;
  const slide = 40 * g.u * easeInQuad(dur > 0 ? clamp01(g.local / dur) : 0);
  const x = (side > 0 ? face.x + face.w * 0.97 : face.x + face.w * 0.03) + side * dw * 0.4;
  const y = face.y + face.h * 0.2 + slide;
  const pop = popScale(g.local);
  if (pop <= 0) return [];
  // Teardrop: x = sin t · sin(t/2), y = −cos t (tip up), 24 samples.
  const drop: Pt[] = [];
  for (let k = 0; k < 24; k++) {
    const t = (k / 24) * Math.PI * 2;
    drop.push([Math.sin(t) * Math.sin(t / 2) * dw * 0.65, -Math.cos(t) * dh * 0.5]);
  }
  const shine: Pt[] = [];
  for (let k = 0; k < 10; k++) {
    const a = (k / 10) * Math.PI * 2;
    shine.push([-dw * 0.13 + Math.cos(a) * dw * 0.08, dh * 0.1 + Math.sin(a) * dh * 0.11]);
  }
  const transform = placeAt(x, y, side * 0.12, pop);
  return [
    ...fillMark(boilPts(drop, g, 200, Math.max(1, 0.02 * s)), DOODLE_COLORS.sweat, DOODLE_COLORS.sweatDark, g, transform),
    pathOp({ points: flat(shine), closed: true, smooth: true, fill: DOODLE_COLORS.white, stroke: null, strokePx: 0, transform, alpha: g.alpha * 0.9 }),
  ];
}

/** Two curved devil horns on top of the head (red, dark outline), popping up from the hairline with a small wiggle. */
function horns(g: Ctx): DrawOp[] {
  const { face, cx } = g;
  const ops: DrawOp[] = [];
  ([-1, 1] as const).forEach((side, i) => {
    const pop = popScale(g.local, i * 0.05);
    if (pop <= 0) return;
    const bx = cx + side * face.w * 0.27, by = face.y + face.h * 0.1;
    const bw = face.w * 0.2;
    // Local coordinates around the base centre: outer edge up to the tip, inner edge back down.
    const outerBase: Pt = [side * bw * 0.5, 0], innerBase: Pt = [-side * bw * 0.5, 0];
    const tip: Pt = [side * face.w * 0.17, -face.h * 0.44];
    const outer = quad(outerBase, [side * face.w * 0.3, -face.h * 0.12], tip, 8);
    const inner = quad(tip, [side * face.w * 0.02, -face.h * 0.16], innerBase, 8).slice(1);
    const shape = boilPts([...outer, ...inner], g, 300 + i, Math.max(1, 0.012 * g.s));
    const wiggle = side * (4 * Math.PI / 180) * Math.sin(2 * Math.PI * 3 * g.local + i);
    ops.push(...fillMark(shape, DOODLE_COLORS.red, DOODLE_COLORS.hornDark, g, placeAt(bx, by, wiggle, pop)));
  });
  return ops;
}

/** "!!" in a jagged, hand-drawn burst near the top corner of the head. */
function bang(g: Ctx): DrawOp[] {
  const { face, s } = g;
  const R = s * 0.3;
  const side = sideFor(g, R * 1.6, 5);
  const x = (side > 0 ? face.x + face.w : face.x) + side * R * 0.75, y = face.y - face.h * 0.02;
  const scale = keyframes([
    { t: 0, v: 0 }, { t: 0.12, v: 1.25, ease: easeOutQuad }, { t: 0.2, v: 0.94, ease: easeOutQuad }, { t: DOODLE_DRAW_IN, v: 1, ease: easeOutQuad },
  ], g.local);
  if (scale <= 0) return [];
  const rot = side * (0.12 + 0.1 * g.hash(500)) + (g.hash(510 + g.boil) - 0.5) * 0.08;
  const burst: Pt[] = [];
  for (let k = 0; k < 24; k++) {
    const a = (k / 24) * Math.PI * 2;
    const r = (k % 2 === 0 ? R : R * 0.64) * (1 + (g.hash(520 + k) - 0.5) * 0.22);
    burst.push([Math.cos(a) * r, Math.sin(a) * r]);
  }
  const transform = placeAt(x, y, rot, scale);
  const px = R * 0.95;
  return [
    ...fillMark(boilPts(burst, g, 540, Math.max(1, 0.015 * s)), DOODLE_COLORS.burst, DOODLE_COLORS.ink, g, transform),
    textOp({ text: "!!", font: fxFamily("Poppins-ExtraBold"), px, x: 0, y: px * 0.04, fill: DOODLE_COLORS.red, stroke: DOODLE_COLORS.ink,
      strokePx: Math.max(3 * g.u, px * 0.07), transform, alpha: g.alpha }),
  ];
}

/** A heart beside the face, beating (two quick pulses per second). */
function heart(g: Ctx): DrawOp[] {
  const { face, s } = g;
  const R = s * 0.21;
  const side = sideFor(g, R * 2.4, 6);
  const x = (side > 0 ? face.x + face.w : face.x) + side * R * 1.3, y = face.y + face.h * 0.32;
  const pop = popScale(g.local);
  if (pop <= 0) return [];
  const beat = Math.max(0, Math.sin(2 * Math.PI * 1.6 * g.local)) ** 4;
  const float = -6 * g.u * Math.sin(2 * Math.PI * 0.8 * g.local);
  const pts: Pt[] = [];
  for (let k = 0; k < 32; k++) {
    const t = (k / 32) * Math.PI * 2;
    const hx = 16 * Math.sin(t) ** 3;
    const hy = -(13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t));
    pts.push([(hx / 16) * R, (hy / 16) * R]);
  }
  const transform = placeAt(x, y + float, side * 0.14, pop * (1 + 0.14 * beat));
  const shine = quad([-R * 0.62, -R * 0.2], [-R * 0.6, -R * 0.62], [-R * 0.25, -R * 0.66], 5);
  return [
    ...fillMark(boilPts(pts, g, 600, Math.max(1, 0.012 * s)), DOODLE_COLORS.pink, DOODLE_COLORS.heartDark, g, transform),
    pathOp({ points: flat(shine), smooth: true, stroke: DOODLE_COLORS.white, strokePx: Math.max(3 * g.u, R * 0.12), transform, alpha: g.alpha * 0.85 }),
  ];
}

/** A 4-stroke anger mark (four curved brackets bowing to a centre) at the temple, throbbing at 4 Hz. */
function anger(g: Ctx): DrawOp[] {
  const { face, s } = g;
  const R = s * 0.25;
  const side = sideFor(g, R * 1.4, 7);
  const x = (side > 0 ? face.x + face.w * 0.95 : face.x + face.w * 0.05) + side * R * 0.5, y = face.y + face.h * 0.1;
  const reveal = easeOutCubic(progress(g.local, 0, DOODLE_DRAW_IN));
  if (reveal <= 0) return [];
  const throb = reveal >= 1 ? 1 + 0.12 * Math.sin(2 * Math.PI * 4 * g.local) : 1;
  const transform = placeAt(x, y, side * 0.1, throb);
  const ops: DrawOp[] = [];
  for (let q = 0; q < 4; q++) {
    // A curved chevron per quadrant, its vertex pointing at the centre and its arms flaring out (the 💢 shape).
    const theta = Math.PI / 4 + (q * Math.PI) / 2;
    const at = (a: number, r: number): Pt => [Math.cos(a) * r, Math.sin(a) * r];
    const arm = (30 * Math.PI) / 180;
    const pts = quad(at(theta - arm, R), at(theta, R * 0.12), at(theta + arm, R), 8);
    ops.push(...strokeMark(boilPts(pts, g, 700 + q, Math.max(1, 0.01 * s)), DOODLE_COLORS.red, Math.max(g.line, R * 0.2), g, transform, reveal));
  }
  return ops;
}

/**
 * Draw ops for one doodle around `face` (OUTPUT px, already picture-transformed). `dc.local` / `dc.dur` drive the
 * draw-in (300 ms), boil (8 fps) and fade (last 200 ms); `dc.hash` (seeded by the cue) picks every random choice.
 */
export function doodleOps(kind: DoodleKind, face: { x: number; y: number; w: number; h: number }, dc: DrawerContext): DrawOp[] {
  const local = dc.local, dur = dc.dur;
  if (!(face.w > 0 && face.h > 0) || local < 0 || local > dur + 1e-9) return [];
  const s = clamp(Math.max(face.w, face.h * 0.75), 40 * dc.u, Math.min(dc.scene.width, dc.scene.height) * 0.6);
  const fadeLeft = dur - local;
  const g: Ctx = {
    face, cx: face.x + face.w / 2, cy: face.y + face.h / 2, s, u: dc.u, W: dc.scene.width, H: dc.scene.height,
    local, draw: easeOutCubic(progress(local, 0, DOODLE_DRAW_IN)),
    alpha: fadeLeft < DOODLE_FADE ? clamp01(fadeLeft / DOODLE_FADE) : 1,
    boil: stepIndex(local, DOODLE_BOIL_FPS), hash: dc.hash,
    line: clamp(0.045 * s, 6 * dc.u, 16 * dc.u),
  };
  if (g.alpha <= 0) return [];
  switch (kind) {
    case "sparkles": return sparkles(g);
    case "arrows": return arrows(g);
    case "sweat": return sweat(g, dur);
    case "horns": return horns(g);
    case "bang": return bang(g);
    case "heart": return heart(g);
    case "anger": return anger(g);
    default: return arrows(g);
  }
}

/** The output-px bounds of a doodle's drawable points (tests: doodles follow the face). */
export function opsBounds(ops: readonly DrawOp[]): { x: number; y: number; w: number; h: number } | null {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const op of ops) {
    if (op.op !== "path") continue;
    const m = op.transform ?? IDENTITY;
    for (let i = 0; i + 1 < op.points.length; i += 2) {
      const x = m[0] * op.points[i] + m[2] * op.points[i + 1] + m[4], y = m[1] * op.points[i] + m[3] * op.points[i + 1] + m[5];
      x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
    }
  }
  return x0 === Infinity ? null : { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}
