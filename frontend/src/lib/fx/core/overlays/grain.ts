// Film grain (§11.2): a 256² mono noise tile from mulberry32(4242), tiled with per-frame offsets from
// hash01(4242, frame), at alpha = grain · 0.35 in `overlay` (mid grey leaves the picture untouched). The raster `grain`
// op draws the tile; this global decides when and how strong. Sits in the "pixel" layer, above the grade extras.
import { round3 } from "../layout";
import { registerGlobal } from "../registry";

export const GRAIN_SEED = 4242;
export const GRAIN_STRENGTH = 0.35;

registerGlobal({
  id: "grain",
  layer: "pixel",
  active: (p) => p.scene.grain > 0,
  ops: (dc) => {
    const amount = round3(Math.min(1, dc.scene.scene.grain) * GRAIN_STRENGTH);
    return amount > 0 ? [{ op: "grain", amount, seed: GRAIN_SEED, frame: dc.clock.frame }] : [];
  },
});
