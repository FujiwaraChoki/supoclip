// Desaturate picture modifier (§11.2): `saturate(1 − amount) brightness(1 − dim)` on the picture for the cue's span
// (the drama drop under kinetic text and serious beats). Hard in, hard out, like a cut.
import { clamp01 } from "../easing";
import { round3 } from "../layout";
import { registerPicture } from "../registry";
import type { CueOf } from "../types";

export const desaturateFilters = (cue: Pick<CueOf<"desaturate">, "amount" | "dim">): string[] => {
  const out: string[] = [];
  const amount = clamp01(cue.amount), dim = clamp01(cue.dim);
  if (amount > 0) out.push(`saturate(${round3(1 - amount)})`);
  if (dim > 0) out.push(`brightness(${round3(1 - dim)})`);
  return out;
};

registerPicture({
  kind: "desaturate",
  apply: (state, cue) => {
    if (!cue || cue.kind !== "desaturate") return state;
    const filters = desaturateFilters(cue);
    return filters.length ? { ...state, filter: [...state.filter, ...filters] } : state;
  },
});
