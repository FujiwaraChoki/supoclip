// STUB (WP3b) for the `highlighter_swipe` caption template, owned by WP8 (§11.1): replace the bodies, keep the
// registrations. Until then base captions use the shared line drawer and the accents draw nothing.
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";

registerCaptionStyle({
  template: "highlighter_swipe",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.highlighter_swipe),
});

registerAccent({
  variant: "highlight",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
