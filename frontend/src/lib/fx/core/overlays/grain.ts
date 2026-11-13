// STUB (WP3b) for film grain, owned by WP9 (§11.2): replace the body, keep the registration.
// The raster `grain` op draws the tile; this drawer decides when and how strong (alpha = scene.grain · 0.35).
import { registerGlobal } from "../registry";

registerGlobal({
  id: "grain",
  layer: "pixel",
  active: (p) => p.scene.grain > 0,
  ops: () => [],
});
