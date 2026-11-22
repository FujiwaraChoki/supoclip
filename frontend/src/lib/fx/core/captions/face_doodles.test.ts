// @vitest-environment node
import { describe, expect, it } from "vitest";
import { clockAt, drawerErrors, pictureStateAt } from "../frame";
import { doodleOps, DOODLE_KINDS, opsBounds } from "../doodles";
import { pictureMatrix, transformRect } from "../layout";
import { IDENTITY_PICTURE, registerPicture } from "../registry";
import { hash01, mulberry32 } from "../rng";
import { expectGolden, renderSceneToPng } from "../test-utils/golden";
import { TEST_FACE } from "../test-utils/napi-host";
import type { FxCue } from "../types";
import { DOODLE_FALLBACK_TEXT } from "./face_doodles";
import { accent, cueOps, expectInsideFrame, faceTrack, paths, prep, snap, templateScene, texts } from "./wp8.testkit";

const doodle = (kind: (typeof DOODLE_KINDS)[number], extra = {}) =>
  accent("b1.accent", "doodle", 0.5, 2.3, { doodle: kind, text: "so true", ...extra });
const scene = (cues: FxCue[], w = 1080, h = 1920, extra = {}) => templateScene("face_doodles", { width: w, height: h, cues, ...extra });

describe("face_doodles frameOps", () => {
  for (const kind of DOODLE_KINDS) {
    it(`${kind}: snapshots at 5 times (1080x1920), inside the frame; landscape too`, () => {
      for (const t of [0.52, 0.6, 0.8, 1.4, 2.25]) {
        const ops = cueOps(scene([doodle(kind)]), t);
        expect(snap(ops)).toMatchSnapshot(`t=${t}`);
        expectInsideFrame(ops, 1080, 1920);
      }
      const wide = cueOps(scene([doodle(kind)], 1920, 1080), 1.4);
      expect(wide.length).toBeGreaterThan(0);
      expectInsideFrame(wide, 1920, 1080);
    });
  }

  it("with no face at t the beat becomes a die-cut sticker with the cue text", () => {
    const ops = texts(cueOps(scene([doodle("bang")], 1080, 1920, { face_track: null }), 1.0));
    expect(ops.map((o) => o.text)).toEqual(["SO TRUE", "SO TRUE", "SO TRUE"]);
    expect(ops[0].font).toBe("FX-Poppins-ExtraBold");
    const blank = texts(cueOps(scene([doodle("heart", { text: "" })], 1080, 1920, { face_track: null }), 1.0));
    expect(blank[0].text).toBe(DOODLE_FALLBACK_TEXT.heart);
  });

  it("is hidden during B-roll windows", () => {
    expect(cueOps(scene([doodle("horns")], 1080, 1920, { broll_windows: [[1, 1.5]] }), 1.2)).toEqual([]);
    expect(cueOps(scene([doodle("horns")], 1080, 1920, { broll_windows: [[1, 1.5]] }), 1.6).length).toBeGreaterThan(0);
  });

  it("follows a moving, zooming face (the picture-transformed dc.face)", () => {
    // Probe zoom: 1.25× about the face, from 1.0 s (this file only; WP9 owns the real modifier).
    registerPicture({ kind: "zoom", apply: (s, cue) => (cue && cue.kind === "zoom" ? { ...s, scale: s.scale * cue.scale, ax: 0.5, ay: 0.34 } : s) });
    const moving = faceTrack(3, (t) => ({ x: TEST_FACE.x + 0.04 * t, y: TEST_FACE.y + 0.01 * t, w: TEST_FACE.w * (1 + 0.1 * t), h: TEST_FACE.h * (1 + 0.1 * t) }));
    const zoom: FxCue = { id: "z", kind: "zoom", start: 1.0, end: 2.0, seed: 1, beat: "", origin: "auto", mode: "punch", scale: 1.25, anchor: "face" };
    const cue = doodle("horns");
    const s = scene([cue, zoom], 1080, 1920, { face_track: moving });
    const p = prep(s);
    const centres: number[] = [];
    for (const t of [0.9, 1.3, 1.7]) {
      const clock = clockAt(p, t);
      const raw = p.faceAt(t)!;
      const face = { ...transformRect(pictureMatrix(pictureStateAt(p, clock, raw), 1080, 1920), raw), landmarks: null };
      const dc = { scene: p, clock, t, local: t - cue.start, dur: cue.end - cue.start, face, picture: { ...IDENTITY_PICTURE, filter: [], slices: [] },
        u: p.u, rng: mulberry32(cue.seed), hash: (n: number) => hash01(cue.seed, n) };
      const expected = doodleOps("horns", face, dc);
      const drawn = paths(cueOps({ ...s, cues: [cue, zoom] }, t)).filter((o) => o.fill === "#FF2E3B" || o.stroke === "#3A0006");
      expect(snap(drawn)).toEqual(snap(expected));
      const b = opsBounds(drawn)!;
      centres.push(b.x + b.w / 2);
      if (t > 1) expect(face.w).toBeGreaterThan(raw.w * 1.2);   // the zoom grew the face the doodle hugs
    }
    expect(centres[1]).not.toBeCloseTo(centres[0], 0);
  });

  it("is deterministic and never throws", () => {
    const cues = DOODLE_KINDS.map((k, i) => accent(`b${i}.accent`, "doodle", 0.3 + i * 0.3, 1.8 + i * 0.3, { doodle: k }));
    const a = prep(scene(cues)), b = prep(scene(cues));
    for (const t of [0.5, 1.2, 2.4]) expect(snap(cueOps(a.scene, t))).toEqual(snap(cueOps(b.scene, t)));
    for (let f = 0; f < 120; f++) cueOps(a.scene, f / 30);
    expect(drawerErrors(a)).toEqual([]);
  });
});

describe("face_doodles raster goldens", () => {
  it("sparkles at 3 times (540x960)", () => {
    for (const t of [0.6, 1.0, 1.6]) expectGolden(`face_doodles__sparkles_t${t}`, renderSceneToPng(scene([doodle("sparkles")], 540, 960), t));
  });
  it("every kind once, and the no-face sticker (540x960)", () => {
    for (const kind of DOODLE_KINDS) {
      if (kind === "sparkles") continue;
      expectGolden(`face_doodles__${kind}_t1.2`, renderSceneToPng(scene([doodle(kind)], 540, 960), 1.2));
    }
    expectGolden("face_doodles__noface_t1.0", renderSceneToPng(scene([doodle("bang")], 540, 960, { face_track: null }), 1.0));
  }, 30_000); // one frame per doodle kind; a shared CI runner needs more than the default 5 s
});
