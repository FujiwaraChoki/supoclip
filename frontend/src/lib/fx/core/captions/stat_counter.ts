// STUB (WP3b) for the `stat_counter` caption template, owned by WP7 (§11.1): replace the bodies, keep the
// registrations. Until then base captions use the shared line drawer and the accents draw nothing.
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";

registerCaptionStyle({
  template: "stat_counter",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.stat_counter),
});

registerAccent({
  variant: "counter",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
