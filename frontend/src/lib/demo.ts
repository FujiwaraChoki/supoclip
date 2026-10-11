/**
 * Real SupoClip output used on the landing page and /demo. Both clips were
 * generated from DEMO_SOURCE, then shortened and downscaled to 540×960 web
 * previews (see public/clips/). Keep these facts in sync with the files.
 */
export const DEMO_SOURCE = {
  title: "Steven Bartlett — How I Make $1.2 Million A Year From This Podcast",
  url: "https://www.youtube.com/watch?v=OpANpfEjNw8",
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
    id: "publish-every-monday",
    src: "/clips/podcast-1.mp4",
    poster: "/clips/podcast-1.jpg",
    hook: "Publishing every Monday grew listeners 10x",
    range: "04:11 – 05:03",
    startSeconds: 251,
    duration: "0:55",
    previewSeconds: 9,
  },
  {
    id: "views-nobody-remembered",
    src: "/clips/podcast-2.mp4",
    poster: "/clips/podcast-2.jpg",
    hook: "10 million views each, yet nobody remembered",
    range: "22:38 – 23:28",
    startSeconds: 1358,
    duration: "0:53",
    previewSeconds: 7,
  },
];

/** Date the demo clips were committed; used for structured data. */
export const DEMO_PUBLISHED_AT = "2026-10-11";

export function getDemoSourceMomentUrl(clip: DemoClip) {
  return `${DEMO_SOURCE.url}&t=${clip.startSeconds}s`;
}
