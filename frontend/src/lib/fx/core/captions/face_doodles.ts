// STUB (WP3b) for the `face_doodles` caption template, owned by WP8 (§11.1): replace the bodies, keep the
// registrations. Until then base captions use the shared line drawer and the accents draw nothing.
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";

registerCaptionStyle({
  template: "face_doodles",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.face_doodles),
});

registerAccent({
  variant: "doodle",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
