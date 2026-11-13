// STUB (WP3b) for the `emoji_drop` caption template, owned by WP7 (§11.1): replace the bodies, keep the
// registrations. Until then base captions use the shared line drawer and the accents draw nothing.
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";

registerCaptionStyle({
  template: "emoji_drop",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.emoji_drop),
});

registerAccent({
  variant: "emoji",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});

registerAccent({
  variant: "emoji_burst",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
