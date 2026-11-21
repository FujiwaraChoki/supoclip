// @vitest-environment node
// Placing a die-cut sticker (sticker_place.ts): the label, the spot it gets and its motion.
import { describe, expect, it } from "vitest";
import { overlapArea, safeRect } from "../layout";
import { TEST_FACE } from "../test-utils/napi-host";
import { hookBounds } from "../hook";
import { STICKER_TILT_DEG, headBox, placeSticker, placedStickerOps, stickerLabel, stickerMotion, stickerWindow } from "./sticker_place";
import { prep, templateScene, texts } from "./wp8.testkit";

const cue = (id: string, start: number, end: number, text: string) => ({ id, seed: 7, start, end, text });

describe("sticker placement", () => {
  for (const [w, h] of [[1080, 1920], [1080, 1080], [1920, 1080]] as const) {
    it(`sits inside the safe area, off the head, tilted within ±8° (${w}x${h})`, () => {
      const p = prep(templateScene("face_doodles", { width: w, height: h }));
      const spot = placeSticker(p, cue("a", 0.56, 1.9, "No way"), [], null)!;
      expect(Math.abs(spot.rot)).toBeLessThanOrEqual((STICKER_TILT_DEG * Math.PI) / 180 + 1e-12);
      const area = safeRect(w, h, false);
      expect(spot.box.x).toBeGreaterThanOrEqual(area.x - 1e-6);
      expect(spot.box.y).toBeGreaterThanOrEqual(area.y - 1e-6);
      expect(spot.box.x + spot.box.w).toBeLessThanOrEqual(area.x + area.w + 1e-6);
      expect(spot.box.y + spot.box.h).toBeLessThanOrEqual(area.y + area.h + 1e-6);
      const face = { x: TEST_FACE.x * w, y: TEST_FACE.y * h, w: TEST_FACE.w * w, h: TEST_FACE.h * h };
      expect(overlapArea(spot.box, headBox(face, p.u))).toBe(0);
    });
  }

  it("keeps off taken spots and off the hook title wherever that sits", () => {
    const p = prep(templateScene("face_doodles", {
      hook_window: [0, 3],
      hook: { text: "Never trust the first offer", font: "Poppins-ExtraBold", accent: "#FFE45C", look: "bold", template: "face_doodles", start: 0, end: 3, x: 0, y: 0, scale: 1 },
    }));
    const first = placeSticker(p, cue("a", 0.5, 1.9, "No way"), [], null)!;
    expect(overlapArea(first.box, hookBounds(p)!)).toBe(0);
    const second = placeSticker(p, cue("b", 0.6, 1.9, "Wild"), [first.box], null)!;
    expect(overlapArea(second.box, first.box)).toBe(0);
    expect(placeSticker(p, cue("c", 0.5, 1.9, "   "), [], null)).toBeNull();
  });
});

describe("sticker label and motion", () => {
  it("wraps long labels onto two lines and uppercases them", () => {
    expect(stickerLabel("300% growth in one year")).toBe("300% GROWTH\nIN ONE YEAR");
    expect(stickerLabel("no way")).toBe("NO WAY");
    expect(stickerLabel("supercalifragilistic")).toBe("SUPERCALIFRAGILISTIC");
  });

  it("slaps in 1.3 → 1 in 90 ms, peels off at the end, lives at most 1.6 s", () => {
    expect(stickerMotion(0, 1)[0]).toBeCloseTo(1.3, 9);
    expect(stickerMotion(0.09, 1)[0]).toBeCloseTo(1, 2);
    expect(stickerMotion(0.5, 1)[1]).toBe(1);
    expect(stickerMotion(1, 1)[1]).toBe(0);
    expect(stickerMotion(1.01, 1)).toEqual([0, 0]);
    expect(stickerWindow({ start: 1, end: 5 })).toEqual([1, 2.6]);
  });

  it("draws the label at rest and nothing after its life", () => {
    const p = prep(templateScene("face_doodles"));
    const spot = placeSticker(p, cue("a", 0.5, 1.9, "No way"), [], null)!;
    expect(texts(placedStickerOps(spot, 0.5, p.u)).some((o) => o.text === "NO WAY")).toBe(true);
    expect(placedStickerOps(spot, 5, p.u)).toEqual([]);
  });
});
