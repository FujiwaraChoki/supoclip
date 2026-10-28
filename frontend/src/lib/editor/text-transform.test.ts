import { expect, it } from "vitest";
import { keepTextInFrame, resizeTextScale, TEXT_CORNERS } from "./text-transform";
import { setCaptionVisualSize, textTransform } from "./document";
import { draft } from "./document.test-fixture";

it("sets the visible caption size without clamping extreme legacy base sizes incorrectly", () => {
  for (const [base, requested] of [[20, 132], [132, 20], [54, 5], [54, 528]]) {
    const doc = setCaptionVisualSize({ ...draft, captions: { ...draft.captions, size: base } }, requested);
    const transform = textTransform(doc, "captions");
    expect(doc.captions.size * transform.scale).toBeCloseTo(requested);
    expect(transform.scale).toBeGreaterThanOrEqual(0.25);
    expect(transform.scale).toBeLessThanOrEqual(4);
    expect(doc.captions.size).toBeGreaterThanOrEqual(20);
    expect(doc.captions.size).toBeLessThanOrEqual(132);
  }
});

it("keeps moved text inside every edge of the output frame", () => {
  expect(keepTextInFrame({ x: -0.8, y: 0.9, scale: 1 }, { x: -80, y: 190, width: 100, height: 40 }, 300, 200))
    .toEqual({ x: -0.8 + 88 / 300, y: 0.9 - 38 / 200, scale: 1 });
});

it("uses both pointer axes and the same scale range at all four corners", () => {
  for (const corner of TEXT_CORNERS) {
    const dx = corner.endsWith("e") ? 50 : -50;
    const dy = corner.startsWith("s") ? 20 : -20;
    const box = { x: 20, y: 30, width: 100, height: 40 };
    expect(resizeTextScale(box, corner, dx, dy, 1)).toBeCloseTo(1.5);
    expect(resizeTextScale(box, corner, -dx * 100, -dy * 100, 1)).toBe(0.25);
    expect(resizeTextScale(box, corner, dx * 100, dy * 100, 1)).toBe(4);
  }
});
