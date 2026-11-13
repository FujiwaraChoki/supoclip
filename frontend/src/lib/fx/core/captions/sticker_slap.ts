// STUB (WP3b) for the `sticker_slap` caption template, owned by WP8 (§11.1): replace the bodies, keep the
// registrations. Until then base captions use the shared line drawer and the accents draw nothing.
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";

registerCaptionStyle({
  template: "sticker_slap",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.sticker_slap),
});

registerAccent({
  variant: "sticker",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
