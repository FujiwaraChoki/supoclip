export * from "./types";
export { fnv1a32, mulberry32, hash01 } from "./rng";
export { buildWarp, toOutput, toEdit, warpFromJson, type Warp } from "./warp";          // mirror of edit_fx/warp.py
export { speechSpans, duckEnvelope, envelopeDbAt } from "./duck";                      // mirror of edit_fx/duck.py
export { mappedCues } from "./mapping";                                                 // mirror of edit_fx/mapping.py
export { censorText, censoredWords } from "./censor";                                   // mirror of edit_fx/censor.py
export { gradeOps, gradeMatrix, type GradeSpec } from "./grades";
export { prepareScene, type PreparedScene } from "./scene";
export { frameOps, isPassthrough, captionHitBoxes, type FxClock } from "./frame";
export { hookBounds, hookLayout, hookOps, type HookLayout } from "./hook";
export { rasterize } from "./raster";
export { renderFrame } from "./render";
export type { FxHost, FxCanvas, FxContext2D, FxImage, EmojiAtlas, DrawOp, Mat2D, Blend } from "./host";
export const FX_CORE_VERSION = "fx-core-1";
