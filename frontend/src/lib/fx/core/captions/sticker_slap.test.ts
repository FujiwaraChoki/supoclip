// @vitest-environment node
import { describe, expect, it } from "vitest";
import { drawerErrors } from "../frame";
import { accentDrawerFor } from "../registry";
import { safeRect, overlapArea, transformRect } from "../layout";
import { STICKER_EDGE_EM, STICKER_PX } from "../looks/sticker";
import { expectGolden, renderSceneToPng } from "../test-utils/golden";
import { TEST_FACE } from "../test-utils/napi-host";
import {
  STICKER_MAX_LIFE, STICKER_MAX_STACK, STICKER_TILT_DEG, stickerLabel, stickerMotion, stickerPlacements,
} from "./sticker_slap";
import { accent, cueOps, expectInsideFrame, opsAt, prep, snap, templateScene, texts } from "./wp8.testkit";

// "Our revenue grew | 300% in one | year and honestly | nobody saw that | coming", words 0.36 s apart from 0.2 s.
const CUES = [
  accent("b1.accent", "sticker", 0.56, 1.9, { text: "No way" }),
  accent("b2.accent", "sticker", 1.0, 2.4, { text: "300% growth in one year" }),
  accent("b3.accent", "sticker", 1.3, 2.6, { text: "plot twist" }),
  accent("b4.accent", "sticker", 1.45, 2.8, { text: "wild" }),
];
const scene = (w = 1080, h = 1920, extra = {}) => templateScene("sticker_slap", { width: w, height: h, cues: CUES, ...extra });
const TIMES = [0.58, 0.62, 0.7, 1.05, 1.5, 2.1];

describe("sticker_slap frameOps", () => {
  it("snapshots at 1080x1920, inside the frame", () => {
    for (const t of TIMES) {
      const ops = cueOps(scene(), t);
      expect(snap(ops)).toMatchSnapshot(`t=${t}`);
      expectInsideFrame(ops, 1080, 1920);
    }
  });

  it("1920x1080 stays inside the frame", () => {
    const ops = cueOps(scene(1920, 1080), 1.5);
    expect(ops.length).toBeGreaterThan(0);
    expectInsideFrame(ops, 1920, 1080);
  });

  it("uses the shared die-cut look (edge, keyline, fill) and the base caption look", () => {
    const ops = texts(cueOps(scene(), 0.7));
    expect(ops).toHaveLength(3);
    expect(ops[0].fill).toBe("#FFFFFF");
    expect(ops[0].strokePx).toBeCloseTo(STICKER_EDGE_EM * STICKER_PX, 6);
    expect(ops.every((o) => o.text === "NO WAY" && o.font === "FX-Poppins-ExtraBold")).toBe(true);
  });
});

describe("sticker placement", () => {
  for (const [w, h] of [[1080, 1920], [1080, 1080], [1920, 1080]] as const) {
    it(`stickers sit inside the safe area, off the face box and caption band, tilted within ±8° (${w}x${h})`, () => {
      const p = prep(scene(w, h));
      const spots = [...stickerPlacements(p).values()].filter((s) => s !== null);
      expect(spots).toHaveLength(CUES.length);
      const face = { x: TEST_FACE.x * w, y: TEST_FACE.y * h, w: TEST_FACE.w * w, h: TEST_FACE.h * h };
      for (const s of spots) {
        expect(Math.abs(s.rot)).toBeLessThanOrEqual((STICKER_TILT_DEG * Math.PI) / 180 + 1e-12);
        const area = safeRect(w, h, false);
        expect(s.box.x).toBeGreaterThanOrEqual(area.x - 1e-6);
        expect(s.box.y).toBeGreaterThanOrEqual(area.y - 1e-6);
        expect(s.box.x + s.box.w).toBeLessThanOrEqual(area.x + area.w + 1e-6);
        expect(s.box.y + s.box.h).toBeLessThanOrEqual(area.y + area.h + 1e-6);
        expect(overlapArea(s.box, face)).toBe(0);
        // The drawn text anchors at full size stay inside the placement box.
        for (const t of [s.start + 0.3]) {
          const ops = texts(cueOps(scene(w, h), t));
          for (const o of ops) {
            const r = transformRect(o.transform, { x: -1, y: -1, w: 2, h: 2 });
            expect(r.x).toBeGreaterThanOrEqual(0);
            expect(r.x + r.w).toBeLessThanOrEqual(w);
          }
        }
      }
      // Stickers on screen together never overlap.
      // (the 4th sticker only avoids the 2 newest: the oldest has peeled off by then)
      for (let i = 0; i < 3; i++) for (let j = i + 1; j < 3; j++) expect(overlapArea(spots[i].box, spots[j].box)).toBe(0);
    });
  }

  it("avoids the hook band while the hook title burns", () => {
    const p = prep(scene(1080, 1920, { hook_window: [0, 3] }));
    for (const s of stickerPlacements(p).values()) expect(s!.box.y).toBeGreaterThanOrEqual(0.2 * 1920 - 1e-6);
  });

  it("a sticker never moves when another appears (placement is per scene, in start order)", () => {
    const p = prep(scene());
    const first = stickerPlacements(p).get("b1.accent")!;
    const alone = stickerPlacements(prep(scene(1080, 1920, { cues: [CUES[0]] }))).get("b1.accent")!;
    expect(first.box).toEqual(alone.box);
  });
});

describe("sticker motion", () => {
  it("slaps in 1.3 → 1 in 90 ms, peels off at the end", () => {
    expect(stickerMotion(0, 1)[0]).toBeCloseTo(1.3, 9);
    expect(stickerMotion(0.09, 1)[0]).toBeCloseTo(1, 2);
    expect(stickerMotion(0.5, 1)[1]).toBe(1);
    expect(stickerMotion(1, 1)[1]).toBe(0);
    expect(stickerMotion(1.01, 1)).toEqual([0, 0]);
  });

  it("lives at most 1.6 s and at most 3 stack at once", () => {
    const d = accentDrawerFor("sticker")!;
    expect(d.window(accent("x", "sticker", 1, 5))).toEqual([1, 1 + STICKER_MAX_LIFE]);
    const many = Array.from({ length: 5 }, (_, i) => accent(`m${i}.accent`, "sticker", 0.5 + i * 0.1, 3, { text: `S${i}` }));
    const ops = texts(cueOps(templateScene("sticker_slap", { cues: many }), 1.2));
    expect(new Set(ops.map((o) => o.text)).size).toBe(STICKER_MAX_STACK);
    expect(ops.some((o) => o.text === "S0")).toBe(false);
  });

  it("wraps long labels onto two lines and uppercases them", () => {
    expect(stickerLabel("300% growth in one year")).toBe("300% GROWTH\nIN ONE YEAR");
    expect(stickerLabel("no way")).toBe("NO WAY");
    expect(stickerLabel("supercalifragilistic")).toBe("SUPERCALIFRAGILISTIC");
  });

  it("is deterministic and never throws", () => {
    const a = prep(scene()), b = prep(scene());
    for (const t of TIMES) expect(snap(opsAt(a, t))).toEqual(snap(opsAt(b, t)));
    expect(drawerErrors(a)).toEqual([]);
  });
});

describe("sticker_slap raster goldens", () => {
  it("renders at 3 times (540x960)", () => {
    for (const t of [0.6, 1.05, 1.5]) expectGolden(`sticker_slap__sticker_t${t}`, renderSceneToPng(scene(540, 960), t));
  });
  it("renders landscape (960x540)", () => {
    expectGolden("sticker_slap__sticker_landscape_t1.5", renderSceneToPng(scene(960, 540), 1.5));
  });
});
