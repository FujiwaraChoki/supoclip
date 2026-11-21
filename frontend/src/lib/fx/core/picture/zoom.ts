// Zoom picture modifier (§11.2). `punch`: the scale steps to `scale` at start (a hard reframe), holds, and steps back at
// end (the resolver ends it on the next reframe or cut, so the framing never bounces back on a timer). `push`:
// 1 → scale, easeInOutSine over the cue; `pull`: scale → 1. `nudge`: 1 → scale over 11 frames, nearly linear with a
// soft landing (the reference short eased zoom), then held to the end. Zooms compose multiplicatively (a punch
// inside a baseline push). The anchor is the smoothed face centre (anchor "face"), the frame centre (anchor
// "center"), or (0.5, 0.38) when the cue has no face near t; frame.ts raises the scale to coverScale and clamps the
// anchor so no picture edge ever shows.
import { easeInOutSine, progress } from "../easing";
import { registerPicture, type PictureState } from "../registry";
import type { PreparedScene } from "../scene";
import type { CueOf } from "../types";

/** Where a face-anchored zoom aims when no face is tracked at t: where faces sit in vertical framing. */
export const NO_FACE_ANCHOR: readonly [number, number] = [0.5, 0.38];
/** Face smoothing window: triangular weights over t ± 0.3 s (7 samples), so the zoom glides with the face. */
const SMOOTH_STEP = 0.1, SMOOTH_TAPS = 3;

/** Smoothed face centre at edit time t, as fractions of the frame; null when no face is near t. */
export function smoothedFaceCenter(p: Pick<PreparedScene, "faceAt" | "width" | "height">, t: number): [number, number] | null {
  let sx = 0, sy = 0, n = 0;
  for (let k = -SMOOTH_TAPS; k <= SMOOTH_TAPS; k++) {
    const f = p.faceAt(t + k * SMOOTH_STEP);
    if (!f) continue;
    const w = SMOOTH_TAPS + 1 - Math.abs(k);
    sx += (f.x + f.w / 2) * w;
    sy += (f.y + f.h / 2) * w;
    n += w;
  }
  return n > 0 ? [sx / n / p.width, sy / n / p.height] : null;
}

/** Length of a nudge ramp: 11 frames (0.37 s). */
export const NUDGE_SECONDS = 11 / 30;
/** Nudge curve (spec §3.5, measured 0.24 / 0.55 / 0.86): linear at 1.1x to 75 %, then an easeOutCubic landing. */
export function nudgeCurve(x: number): number {
  const k = Math.max(0, Math.min(1, x));
  if (k < 0.75) return 1.1 * k;
  const y = (k - 0.75) / 0.25;
  return 0.825 + 0.175 * (1 - (1 - y) ** 3);
}

/** The zoom factor a cue contributes at `local` seconds into it (1 outside [0, dur)). */
export function zoomFactor(cue: Pick<CueOf<"zoom">, "mode" | "scale">, local: number, dur: number): number {
  if (local < 0 || local >= dur) return 1;
  const s = Math.max(0.01, cue.scale);
  if (cue.mode === "punch") return s;
  if (cue.mode === "nudge") return 1 + (s - 1) * nudgeCurve(local / Math.min(NUDGE_SECONDS, dur));
  const k = easeInOutSine(progress(local, 0, dur));
  return cue.mode === "push" ? 1 + (s - 1) * k : s + (1 - s) * k;
}

/** Longest look-around (s) for a face when none is near t: past it the cue aims at NO_FACE_ANCHOR. */
const FALLBACK_REACH = 3;

/**
 * The face anchor of a zoom cue at t: the smoothed face centre at t; when the face is lost at t (a detection gap, a
 * head turn), the smoothed centre at the nearest time inside the cue that has one, so a held punch or a long push
 * never jumps to NO_FACE_ANCHOR mid-zoom; NO_FACE_ANCHOR only when the cue has no face near t at all.
 */
export function zoomFaceAnchor(p: Pick<PreparedScene, "faceAt" | "width" | "height">, t: number, start: number, end: number): readonly [number, number] {
  const here = smoothedFaceCenter(p, t);
  if (here) return here;
  const lo = Math.max(start, t - FALLBACK_REACH), hi = Math.min(end, t + FALLBACK_REACH);
  for (let k = 1; t - k * SMOOTH_STEP >= lo - 1e-9 || t + k * SMOOTH_STEP <= hi + 1e-9; k++) {
    for (const at of [t - k * SMOOTH_STEP, t + k * SMOOTH_STEP]) {
      if (at < lo - 1e-9 || at > hi + 1e-9 || !p.faceAt(at)) continue;
      const c = smoothedFaceCenter(p, at);
      if (c) return c;
    }
  }
  return NO_FACE_ANCHOR;
}

registerPicture({
  kind: "zoom",
  apply: (state, cue, dc): PictureState => {
    if (!cue || cue.kind !== "zoom") return state;
    const factor = zoomFactor(cue, dc.local, dc.dur);
    if (factor === 1) return state;
    const anchor: readonly [number, number] = cue.anchor === "center"
      ? [0.5, 0.5]
      : zoomFaceAnchor(dc.scene, dc.t, cue.start, cue.end);
    return { ...state, scale: state.scale * factor, ax: anchor[0], ay: anchor[1] };
  },
});
