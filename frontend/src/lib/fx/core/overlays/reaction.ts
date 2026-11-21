// Reaction frame (§11.2): the GIF PiP itself is composited in the picture (P1 on the server, the host's drawBase in
// the browser) at reactionRect(W, H); this drawer frames it: a white rounded border 10u px, a soft drop shadow and
// the "via GIPHY" attribution 22u px under it. Everything is drawn with the frame's picture transform so it moves
// with the PiP through zooms and shakes (AR-M6). A cue with no attached asset has no PiP, so nothing is framed.
import type { DrawOp } from "../host";
import { reactionRect } from "../constants";
import { fxFamily, pictureMatrix, rectOp, round3, shadow, textOp } from "../layout";
import { registerCue, type CueDrawer } from "../registry";
import type { CueOf } from "../types";

export const REACTION_BORDER = 10;    // u px
export const REACTION_CREDIT_PX = 22; // u px

export const hasReactionAsset = (cue: Pick<CueOf<"reaction">, "url" | "asset_id">): boolean => !!(cue.url || cue.asset_id);

registerCue({
  kind: "reaction",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: (cue, dc): DrawOp[] => {
    if (!hasReactionAsset(cue)) return [];
    const W = dc.scene.width, H = dc.scene.height, u = dc.u;
    const r = reactionRect(W, H);
    const transform = pictureMatrix(dc.picture, W, H);
    const b = REACTION_BORDER * u;
    const label = cue.provider === "giphy" ? "via GIPHY" : "";
    const ops: DrawOp[] = [
      // Drop shadow: two dark bands hugging the border's outside, shifted down 0.5u·10, so it never darkens the PiP.
      ...[[1.2, 1.2, 0.28], [2.2, 0.9, 0.12]].map(([at, width, alpha]) => rectOp({
        x: round3(r.x - at * b), y: round3(r.y - at * b + 0.5 * b), w: round3(r.w + 2 * at * b), h: round3(r.h + 2 * at * b),
        radius: round3((1.2 + at) * b), fill: null, stroke: `rgba(0,0,0,${alpha})`, strokePx: round3(width * b), transform,
      })),
      // Border: a 10u band covering [rect − 10u, rect]; its inner corners round off the PiP's square corners.
      rectOp({
        x: round3(r.x - b / 2), y: round3(r.y - b / 2), w: round3(r.w + b), h: round3(r.h + b), radius: round3(1.2 * b),
        fill: null, stroke: "#FFFFFF", strokePx: round3(b), transform,
      }),
    ];
    if (label) {
      const px = REACTION_CREDIT_PX * u;
      ops.push(textOp({
        text: label, font: fxFamily("TikTokSans-Bold"), px: round3(px), x: round3(r.x + r.w), y: round3(r.y + r.h + b + px * 0.85),
        align: "right", fill: "#FFFFFF", alpha: 0.92, letterSpacing: round3(0.04 * px), transform,
        shadow: shadow("rgba(0,0,0,0.6)", round3(6 * u), 0, round3(2 * u)),
      }));
    }
    return ops;
  },
} satisfies CueDrawer<"reaction"> as unknown as CueDrawer);
