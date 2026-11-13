// STUB (WP3b) for the `marker_circle` caption template, owned by WP7 (§11.1): replace the bodies, keep the
// registrations. Until then base captions use the shared line drawer and the accents draw nothing.
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";

registerCaptionStyle({
  template: "marker_circle",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.marker_circle),
});

registerAccent({
  variant: "circle",
  layer: "caption",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});

registerAccent({
  variant: "underline",
  layer: "caption",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});

registerAccent({
  variant: "strike",
  layer: "caption",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});

registerAccent({
  variant: "arrow",
  layer: "caption",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
