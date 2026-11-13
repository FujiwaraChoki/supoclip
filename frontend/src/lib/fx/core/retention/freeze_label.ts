// STUB (WP3b) for the freeze label (F clock, sticker look), owned by WP9 (§11.2): replace the body, keep the
// registration. A freeze is an E instant: its window [start, start] is active for the whole hold.
import { registerCue } from "../registry";

registerCue({
  kind: "freeze",
  layer: "retention",
  window: (cue) => [cue.start, cue.start],
  ops: () => [],
});
