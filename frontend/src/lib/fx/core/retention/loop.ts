// STUB (WP3b) for the loop crossfade (frame0 over the last 0.4 s of F), owned by WP9: replace the body, keep the
// registration.
import { registerGlobal } from "../registry";

registerGlobal({
  id: "loop",
  layer: "loop",
  active: (p) => p.scene.loop_ending === "crossfade",
  ops: () => [],
});
