import type { Intensity } from "@/lib/fx/core/types";

/**
 * Media for the landing page's "Dynamic edits" section. The four intensity
 * renders are the same clip, cut from one source moment, so the player can
 * swap between them at the same playback position. Files live in
 * public/clips/dynamic-edits/; swapping media is a change to this file only.
 *
 * Real SupoClip renders (production render path, built-in sounds and music) of DEMO_SOURCE (lib/demo.ts):
 * the intensity clip is 04:20.6–04:46.9 (Highlighter captions; Off uses the default template), the b-roll clip
 * 28:57.8–29:26.3 at Max. Web previews: 540x960 H.264 CRF 26, AAC 96k.
 */
export type DynamicEditsIntensity = Intensity;

export interface DynamicEditsVideo {
  src: string;
  poster: string;
}

/** Order of the switcher, left to right. */
export const DYNAMIC_EDITS_INTENSITIES: readonly DynamicEditsIntensity[] = ["off", "subtle", "bold", "max"];

/** What the player shows first. */
export const DYNAMIC_EDITS_DEFAULT_INTENSITY: DynamicEditsIntensity = "max";

const BASE = "/clips/dynamic-edits";
const INTERVIEW_POSTER = `${BASE}/interview.jpg`;

export const DYNAMIC_EDITS_INTERVIEW: Record<DynamicEditsIntensity, DynamicEditsVideo> = {
  off: { src: `${BASE}/interview-off.mp4`, poster: INTERVIEW_POSTER },
  subtle: { src: `${BASE}/interview-subtle.mp4`, poster: INTERVIEW_POSTER },
  bold: { src: `${BASE}/interview-bold.mp4`, poster: INTERVIEW_POSTER },
  max: { src: `${BASE}/interview-max.mp4`, poster: INTERVIEW_POSTER },
};

/** A second clip at Max, with B-roll cut in. */
export const DYNAMIC_EDITS_BROLL: DynamicEditsVideo = {
  src: `${BASE}/broll-max.mp4`,
  poster: `${BASE}/broll-max.jpg`,
};
