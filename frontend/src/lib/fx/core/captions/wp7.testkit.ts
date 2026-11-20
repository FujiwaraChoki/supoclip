// Test kit for the WP7 caption-accent tests (marker_circle, emoji_drop, slam, behind_head, stat_counter). Test-only:
// imported by *.test.ts files, never by fx-core itself (so it is not in the bundle).
import { expect } from "vitest";
import type { DrawOp } from "../host";
import type { PreparedScene } from "../scene";
import { prepareScene } from "../scene";
import { clockAt, frameOps } from "../frame";
import { applyMat } from "../layout";
import type { AccentCue, AccentVariant, FaceTrack, FxCaptionStyle, FxScene } from "../types";
import { fakeMeasureHost, makeScene } from "../test-utils/fake-host";
import { expectGolden, renderSceneToPng } from "../test-utils/golden";
import { ATLAS_DIR, TEST_FACE, readAtlasMap } from "../test-utils/napi-host";
import type { EmojiAtlas } from "../host";
import { loadImage } from "@napi-rs/canvas";

/** JSON snapshot form: numbers to 3 dp so float noise never churns a snapshot. */
export const snap = (ops: DrawOp[]): unknown =>
  JSON.parse(JSON.stringify(ops, (_k, v) => (typeof v === "number" ? Math.round(v * 1000) / 1000 : v)));

export function accent(id: string, variant: AccentVariant, start: number, end: number, extra: Partial<AccentCue> = {}): AccentCue {
  return {
    id, kind: "accent", variant, start, end, seed: 4711 + id.length * 31, beat: id.split(".")[0], origin: "auto",
    text: "", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "", ...extra,
  };
}

/** The test picture's face (napi-host TEST_FACE) as a 6 fps output-space track over `duration` seconds. */
export function testFaceTrack(duration: number, drift = 0): FaceTrack {
  const n = Math.ceil(duration * 6) + 1;
  return {
    version: 1, space: "output", fps: 6,
    samples: Array.from({ length: n }, (_, i) => [i / 6, [[TEST_FACE.x + drift * (i / n), TEST_FACE.y, TEST_FACE.w, TEST_FACE.h, 0.95]]] as [number, number[][]]),
  };
}

export const prep = (scene: FxScene): PreparedScene => prepareScene(scene, fakeMeasureHost);

/** frameOps at edit time t (identity warp, so final time == edit time). */
export const opsAt = (p: PreparedScene, t: number): DrawOp[] => frameOps(p, clockAt(p, t));

/** The ops `cue` adds to a frame: frameOps with the cue minus frameOps without it (first-match multiset difference),
 *  so snapshots hold only the accent (and never the caption ops it does not change). */
export function accentOps(scene: FxScene, t: number, without: FxScene = { ...scene, cues: [] }): DrawOp[] {
  const rest = opsAt(prep(without), t).map((o) => JSON.stringify(o));
  return opsAt(prep(scene), t).filter((o) => {
    const i = rest.indexOf(JSON.stringify(o));
    if (i < 0) return true;
    rest.splice(i, 1);
    return false;
  });
}

/** Every drawable point of the ops (text anchors, path points, sprite and rect corners) lies inside the frame. */
export function expectInsideFrame(ops: DrawOp[], w: number, h: number, margin = 0): void {
  for (const op of ops) {
    const pts: [number, number][] = [];
    if (op.op === "text") pts.push(applyMat(op.transform, op.x, op.y));
    else if (op.op === "path") for (let i = 0; i + 1 < op.points.length; i += 2) pts.push(applyMat(op.transform, op.points[i], op.points[i + 1]));
    else if (op.op === "sprite" || op.op === "rect") {
      for (const [x, y] of [[op.x, op.y], [op.x + op.w, op.y], [op.x, op.y + op.h], [op.x + op.w, op.y + op.h]]) pts.push(applyMat(op.transform, x, y));
    }
    for (const [x, y] of pts) {
      expect(x).toBeGreaterThanOrEqual(-margin);
      expect(x).toBeLessThanOrEqual(w + margin);
      expect(y).toBeGreaterThanOrEqual(-margin);
      expect(y).toBeLessThanOrEqual(h + margin);
    }
  }
}

let atlas: Promise<EmojiAtlas> | null = null;
/** The committed emoji atlas, fully decoded. (napi-host's loadTestAtlas assigns `Image.src`, which @napi-rs/canvas
 *  0.1.80 decodes asynchronously, so its sprites draw nothing; loadImage waits for the decode.) */
export function decodedAtlas(): Promise<EmojiAtlas> {
  atlas ??= (async () => {
    const image = await loadImage(`${ATLAS_DIR}/atlas.png`);
    const atlasMap = readAtlasMap();
    return { image, cell: atlasMap.cell, glyphs: atlasMap.glyphs };
  })();
  return atlas;
}

/** Raster goldens: the scene rendered with real fonts and the decoded emoji atlas at each time, against
 *  shared/fx/golden/<name>_t<t>.png. */
export async function expectGoldens(name: string, scene: FxScene, times: readonly number[]): Promise<void> {
  const decoded = await decodedAtlas();
  for (const t of times) expectGolden(`${name}_t${t}`, renderSceneToPng(scene, t, { atlas: decoded }));
}

/** A scene for one template with the default sample words; `caption` overrides merge into the scaled caption style
 *  (pass `null` for a scene without captions). */
export function templateScene(template: string, overrides: Partial<FxScene> = {}, caption: Partial<FxCaptionStyle> | null = {}): FxScene {
  const base = makeScene({ template, ...overrides });
  return { ...base, caption: caption === null || !base.caption ? null : { ...base.caption, template, ...caption } };
}
