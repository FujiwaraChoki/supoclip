// STUB (WP3b) for the `kinetic_stack` caption template, owned by WP8 (§11.1): replace the bodies, keep the
// registrations. Until then base captions use the shared line drawer and the accents draw nothing.
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";

registerCaptionStyle({
  template: "kinetic_stack",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.kinetic_stack),
});

registerAccent({
  variant: "kinetic",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
