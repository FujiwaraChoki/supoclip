// STUB (WP3b) for the `decode_glitch` caption template, owned by WP8 (§11.1): replace the bodies, keep the
// registrations. Until then base captions use the shared line drawer and the accents draw nothing.
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";

registerCaptionStyle({
  template: "decode_glitch",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.decode_glitch),
});

registerAccent({
  variant: "decode",
  layer: "caption",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
