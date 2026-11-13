// STUB (WP3b) for the `bleep` overlay drawer, owned by WP9 (§11.2): replace the body, keep the registration.
import { registerCue } from "../registry";

registerCue({
  kind: "bleep",
  layer: "cover",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
