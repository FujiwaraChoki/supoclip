// Transitions at junction times (§11.2). Visual only: the cut itself is in the picture (stage 1), so a transition
// never changes duration. A cue is an instant at the cut; it draws within ±TRANSITION_HALF[style] around it, with
// p = local / half running −1 → 0 (the cut) → 1.
//
// Geometry (picture modifier, folded into the picture op and covered by coverScale):
//   whip         the outgoing shot accelerates off one side, the incoming one decelerates in from the other
//                (±140u px, easeIn/easeOut, direction seeded) with horizontal motion blur up to 36u px at the cut;
//   zoom_through scale 1 → 1.35 into the cut and 1.35 → 1 out of it (plus 3 radial ghost draws, "pixel" layer);
//   spin         one full turn (0 → 360°, easeInOutCubic over ±0.2 s, direction seeded); coverScale(θ) per frame;
//   glitch       the glitch modifier's slices and RGB split at amount 0.8.
// Overlays ("flash" layer), spec §3.6: flash = 3 frames of white (0.6 the frame before the cut, 0.95 on the cut,
// 0.3 after); light_leak = 6 frames of two warm radial leaks (#FFB347, #FF6A3D) sweeping across in `screen`
// (2 frames up, 3 at about 0.85, 1 down).
import type { DrawOp } from "../host";
import { easeInCubic, easeInOutCubic, easeOutCubic, easeInOutSine, lerp } from "../easing";
import { gradientOp, pictureMatrix, rectOp, round3 } from "../layout";
import { registerCue, type CueDrawer, registerPicture, type DrawerContext, type PictureState } from "../registry";
import { TRANSITION_HALF, type CueOf, type TransitionStyle } from "../types";
import { glitchState } from "./glitch";

type TransitionCue = CueOf<"transition">;

export const WHIP_TRAVEL = 140;       // u px
export const WHIP_BLUR = 36;          // u px
export const ZOOM_THROUGH_PEAK = 1.35;
export const TRANSITION_GLITCH = 0.8;
export const LIGHT_LEAK_PEAK = 0.85;
export const FLASH_PEAK = 0.95;
/** Flash alpha per frame from the cut (-1, 0, +1). */
export const FLASH_FRAMES: Readonly<Record<string, number>> = { "-1": 0.6, "0": 0.95, "1": 0.3 };
/** Light leak envelope per frame from the cut (-3 .. +2), as a fraction of LIGHT_LEAK_PEAK. */
export const LEAK_FRAMES: Readonly<Record<string, number>> = { "-3": 0.4, "-2": 0.8, "-1": 1, "0": 1, "1": 1, "2": 0.45 };

const halfOf = (style: TransitionStyle): number => TRANSITION_HALF[style] ?? 0.1;
export const transitionWindow = (cue: TransitionCue): [number, number] => {
  const half = halfOf(cue.style);
  return [cue.start - half, cue.start + half];
};
/** Position through the transition: −1 at the window start, 0 at the cut, 1 at the end (clamped). */
export const transitionPhase = (cue: TransitionCue, local: number): number => {
  const half = halfOf(cue.style);
  return Math.max(-1, Math.min(1, half > 0 ? local / half : 0));
};
/** Seeded direction, +1 or −1 (purpose n keeps the draws of one cue independent). */
const direction = (hash: DrawerContext["hash"], n: number): 1 | -1 => (hash(n) < 0.5 ? -1 : 1);

/** Whip: [travel 0..1, signed offset in u px]. The outgoing shot leaves toward −dir, the incoming one enters from +dir. */
export function whipAt(p: number, dir: 1 | -1): { k: number; dx: number } {
  if (p < 0) {
    const k = easeInCubic(1 + p);
    return { k, dx: -dir * WHIP_TRAVEL * k };
  }
  const k = 1 - easeOutCubic(p);
  return { k, dx: dir * WHIP_TRAVEL * k };
}

/** zoom_through scale at phase p: 1 → 1.35 into the cut, 1.35 → 1 out. */
export function zoomThroughScale(p: number): number {
  const k = p < 0 ? easeInCubic(1 + p) : 1 - easeOutCubic(p);
  return 1 + (ZOOM_THROUGH_PEAK - 1) * k;
}

/** spin rotation (radians) at phase p: one full turn over the window. */
export const spinAngle = (p: number, dir: 1 | -1): number => dir * 2 * Math.PI * easeInOutCubic((p + 1) / 2);

registerPicture({
  kind: "transition",
  apply: (state, cue, dc): PictureState => {
    if (!cue || cue.kind !== "transition") return state;
    const p = transitionPhase(cue, dc.local);
    switch (cue.style) {
      case "whip": {
        const { k, dx } = whipAt(p, direction(dc.hash, 1));
        return { ...state, dx: state.dx + round3(dx * dc.u), blurX: state.blurX + round3(WHIP_BLUR * k * dc.u) };
      }
      case "zoom_through":
        return { ...state, scale: state.scale * zoomThroughScale(p) };
      case "spin":
        return { ...state, rot: state.rot + spinAngle(p, direction(dc.hash, 2)) };
      case "glitch":
        return glitchState(state, TRANSITION_GLITCH, dc.hash, dc.clock.frame, dc.u, dc.scene.height);
      default:
        return state;
    }
  },
});

/** zoom_through radial blur: three ghosts of the picture, each a little bigger, at 0.25 alpha ("pixel" layer). */
registerCue({
  kind: "transition",
  layer: "pixel",
  window: transitionWindow,
  ops: (cue, dc) => {
    if (cue.style !== "zoom_through") return [];
    const env = (zoomThroughScale(transitionPhase(cue, dc.local)) - 1) / (ZOOM_THROUGH_PEAK - 1);
    if (env < 0.02) return [];
    const W = dc.scene.width, H = dc.scene.height;
    return [1, 2, 3].map((i): DrawOp => ({
      op: "picture",
      transform: pictureMatrix({ ...dc.picture, scale: dc.picture.scale * (1 + 0.045 * i * env) }, W, H),
      filter: dc.picture.filter.join(" "), alpha: 0.25, blend: "source-over", channel: "rgb", dx: 0, dy: 0,
      cutout: false, clip: null,
    }));
  },
} satisfies CueDrawer<"transition"> as unknown as CueDrawer);

/** flash and light_leak overlays ("flash" layer). */
registerCue({
  kind: "transition",
  layer: "flash",
  window: transitionWindow,
  ops: (cue, dc) => {
    const W = dc.scene.width, H = dc.scene.height;
    const p = transitionPhase(cue, dc.local);
    const frame = String(Math.round(dc.local * 30));
    if (cue.style === "flash") {
      const alpha = FLASH_FRAMES[frame] ?? 0;
      return alpha > 0.004 ? [rectOp({ x: 0, y: 0, w: W, h: H, fill: "#FFFFFF", alpha })] : [];
    }
    if (cue.style !== "light_leak") return [];
    const alpha = round3(LIGHT_LEAK_PEAK * (LEAK_FRAMES[frame] ?? 0));
    if (alpha <= 0.004) return [];
    const s = easeInOutSine((p + 1) / 2), dir = direction(dc.hash, 3);
    const big = Math.max(W, H);
    const sweep = (from: number, to: number) => (dir > 0 ? lerp(from, to, s) : lerp(to, from, s));
    const warmX = sweep(-0.35 * W, 1.35 * W), pinkX = sweep(1.25 * W, -0.25 * W);
    const warmY = (0.22 + 0.12 * dc.hash(4)) * H, pinkY = (0.62 + 0.16 * dc.hash(5)) * H;
    return [
      gradientOp({
        kind: "radial", x0: warmX, y0: warmY, r0: 0, x1: warmX, y1: warmY, r1: 0.7 * big, rect: [0, 0, W, H], alpha, blend: "screen",
        stops: [[0, "rgba(255,179,71,1)"], [0.35, "rgba(255,179,71,0.7)"], [1, "rgba(255,179,71,0)"]],
      }),
      gradientOp({
        kind: "radial", x0: pinkX, y0: pinkY, r0: 0, x1: pinkX, y1: pinkY, r1: 0.55 * big, rect: [0, 0, W, H], alpha: round3(alpha * 0.85), blend: "screen",
        stops: [[0, "rgba(255,106,61,1)"], [0.4, "rgba(255,106,61,0.6)"], [1, "rgba(255,106,61,0)"]],
      }),
    ];
  },
} satisfies CueDrawer<"transition"> as unknown as CueDrawer);
