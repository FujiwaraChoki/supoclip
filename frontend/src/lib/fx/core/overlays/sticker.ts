// STUB (WP3b) for the `sticker` overlay drawer, owned by WP9 (§11.2): replace the body, keep the registration.
import { registerCue } from "../registry";

registerCue({
  kind: "sticker",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
