// STUB (WP3b) for the `flash` overlay drawer, owned by WP9 (§11.2): replace the body, keep the registration.
import { registerCue } from "../registry";

registerCue({
  kind: "flash",
  layer: "flash",
  window: (cue) => [cue.start, cue.end],
  ops: () => [],
});
