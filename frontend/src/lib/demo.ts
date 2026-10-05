/**
 * Real SupoClip output used on the landing page and /demo. Both clips were
 * generated from DEMO_SOURCE, then shortened and downscaled to 540×960 web
 * previews (see public/clips/). Keep these facts in sync with the files.
 */
export const DEMO_SOURCE = {
  title: "Sam Altman — How to Start a Startup",
  url: "https://www.youtube.com/watch?v=Vv3CEAS_w34",
};

export interface DemoClip {
  id: string;
  src: string;
  poster: string;
  hook: string;
  range: string;
  /** Start of the moment in the source video, for timestamped links. */
  startSeconds: number;
  /** Length of the full generated clip. */
  duration: string;
  /** Length of the web preview in public/clips. */
  previewSeconds: number;
}

export const DEMO_CLIPS: DemoClip[] = [
  {
    id: "chaos-management",
    src: "/clips/demo-2.mp4",
    poster: "/clips/demo-2.jpg",
    hook: "Why chaos management is not teachable",
    range: "05:04 – 05:54",
    startSeconds: 304,
    duration: "0:50",
    previewSeconds: 9,
  },
  {
    id: "ten-week-startup",
    src: "/clips/demo-1.mp4",
    poster: "/clips/demo-1.jpg",
    hook: "Why your 10 week old startup is failing",
    range: "00:14 – 00:39",
    startSeconds: 14,
    duration: "0:25",
    previewSeconds: 12,
  },
];

/** Date the demo clips were committed; used for structured data. */
export const DEMO_PUBLISHED_AT = "2026-07-27";

export function getDemoSourceMomentUrl(clip: DemoClip) {
  return `${DEMO_SOURCE.url}&t=${clip.startSeconds}s`;
}
