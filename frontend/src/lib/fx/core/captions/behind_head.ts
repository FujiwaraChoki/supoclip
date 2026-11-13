// STUB (WP3b) for the `behind_head` caption template, owned by WP7 (§11.1): replace the bodies, keep the
// registrations. Until then base captions use the shared line drawer and the accents draw nothing.
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";

registerCaptionStyle({
  template: "behind_head",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.behind_head),
});

registerAccent({
  variant: "behind",
  layer: "behind",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
