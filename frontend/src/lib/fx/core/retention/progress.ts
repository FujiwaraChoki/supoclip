// STUB (WP3b) for the progress bar (F clock), owned by WP9 (§11.2): replace the body, keep the registration.
import { registerGlobal } from "../registry";

registerGlobal({
  id: "progress",
  layer: "retention",
  active: (p) => p.scene.progress_bar,
  ops: () => [],
});
