// STUB (WP3b) for the `reaction` overlay drawer, owned by WP9 (§11.2): replace the body, keep the registration.
import { registerCue } from "../registry";

registerCue({
  kind: "reaction",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
