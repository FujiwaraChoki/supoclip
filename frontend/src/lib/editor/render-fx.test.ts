// @vitest-environment node
// The studio renderer's dynamic-edit branch on real Skia (@napi-rs/canvas, the same engine as Chrome): fx-core frames
// over the framed picture, the document's effects before the grade, censored legacy captions, the suppressed hook.
import { createCanvas, type Canvas, type SKRSContext2D } from "@napi-rs/canvas";
import { beforeAll, describe, expect, it } from "vitest";
import { registerFxFonts } from "@/lib/fx/core/test-utils/napi-host";
import type { FxCue } from "@/lib/fx/core/types";
import { DEFAULT_DOC_FX, type DocFx, type EditDocument } from "./document";
import { draft } from "./document.test-fixture";
import { drawEditorFrame, drawEditorTextLayer, editorFxScene, fxCaptionBounds, fxCaptionRowShift, textLayerBounds } from "./render";

beforeAll(() => {
  registerFxFonts();
  // The browser host probes ctx.filter on an OffscreenCanvas; give it Skia's.
  (globalThis as { OffscreenCanvas?: unknown }).OffscreenCanvas = class {
    constructor(width: number, height: number) {
      return createCanvas(width, height);
    }
  };
});

const base = { seed: 5, beat: "", origin: "auto" as const };
const flash = (start: number, end: number): FxCue => ({ ...base, id: "fl", kind: "flash", start, end, color: "#FFFFFF", peak: 1 });
const bleep = (start: number, end: number): FxCue => ({ ...base, id: "bl", kind: "bleep", start, end, tone: true, cover: "none" });
const withFx = (over: Partial<DocFx>, doc: Partial<EditDocument> = {}): EditDocument => ({
  ...draft,
  segments: [{ id: "s", start: 0, end: 3 }],
  ...doc,
  fx: { ...DEFAULT_DOC_FX, style: { ...DEFAULT_DOC_FX.style, intensity: "bold" }, ...over },
});

function canvas(width = 270, height = 480): { canvas: Canvas; ctx: CanvasRenderingContext2D } {
  const c = createCanvas(width, height);
  return { canvas: c, ctx: c.getContext("2d") as unknown as CanvasRenderingContext2D };
}
const picture = (filters: string[] = []) => (rect: { x: number; y: number; width: number; height: number }, target: CanvasRenderingContext2D) => {
  filters.push(String(target.filter));
  target.fillStyle = "#3060C0";
  target.fillRect(rect.x, rect.y, rect.width, rect.height);
};
const mean = (ctx: CanvasRenderingContext2D) => {
  const { data } = (ctx as unknown as SKRSContext2D).getImageData(0, 0, ctx.canvas.width, ctx.canvas.height);
  let r = 0, g = 0, b = 0;
  for (let i = 0; i < data.length; i += 4) { r += data[i]; g += data[i + 1]; b += data[i + 2]; }
  const n = data.length / 4;
  return [r / n, g / n, b / n];
};

describe("drawEditorFrame with dynamic edits", () => {
  it("draws fx-core over the framed picture: a flash whitens its frames only", () => {
    const doc = withFx({ cues: [flash(1, 1.3)] });
    const { ctx } = canvas();
    drawEditorFrame(ctx, doc, 1080, 1920, 0.5, picture(), undefined, null, "none");
    const before = mean(ctx);
    expect(before[2]).toBeGreaterThan(before[0] + 80);   // the blue picture, untouched
    drawEditorFrame(ctx, doc, 1080, 1920, 1.05, picture(), undefined, null, "none");
    const during = mean(ctx);
    expect(during[0]).toBeGreaterThan(before[0] + 60);
  });

  it("applies the document's effects to the source picture before the grade", () => {
    const doc = withFx({ grade: "noir" }, { effects: { brightness: 50, contrast: 100, saturation: 100, blur: 0, hue: 0 } });
    const { ctx } = canvas();
    const filters: string[] = [];
    drawEditorFrame(ctx, doc, 1080, 1920, 0.5, picture(filters), undefined, null, "none");
    expect(filters.length).toBeGreaterThan(0);
    const filter = filters[0];
    expect(filter.indexOf("brightness(50%)")).toBe(0);
    expect(filter).toContain("grayscale");                      // the noir grade follows the effects
    const [r, g, b] = mean(ctx);
    expect(Math.abs(r - b)).toBeLessThan(6);                     // graded to grey
    expect(r).toBeLessThan(70);                                  // and darkened by the effects first
    expect(g).toBeGreaterThan(0);
  });

  it("draws B-roll inside the picture so the grade covers it too", () => {
    const doc = withFx({ grade: "noir" });
    const { ctx } = canvas();
    const filters: string[] = [];
    drawEditorFrame(ctx, doc, 1080, 1920, 0.5, picture(), undefined, {
      width: 1080, height: 1920, alpha: 1,
      draw: (x, y, w, h, target) => {
        filters.push(String(target!.filter));
        target!.fillStyle = "#E02020";
        target!.fillRect(x, y, w, h);
      },
    }, "none");
    expect(filters[0]).toContain("grayscale");
    const [r, , b] = mean(ctx);
    expect(Math.abs(r - b)).toBeLessThan(6);
  });

  it("leaves a draft without dynamic edits on the legacy path", () => {
    const { ctx } = canvas();
    const filters: string[] = [];
    drawEditorFrame(ctx, draft, 1080, 1920, 0.5, picture(filters), undefined, null, "none");
    expect(filters).toEqual(["brightness(100%) contrast(100%) saturate(100%) blur(0px) hue-rotate(0deg)"]);
  });
});

describe("text layers with dynamic edits", () => {
  const recordText = (ctx: CanvasRenderingContext2D) => {
    const texts: string[] = [];
    const fill = ctx.fillText.bind(ctx);
    ctx.fillText = ((text: string, x: number, y: number) => {
      texts.push(text);
      fill(text, x, y);
    }) as typeof ctx.fillText;
    return texts;
  };

  it("censors legacy captions under a bleep, like the server's ASS", () => {
    const doc = withFx({ cues: [bleep(1, 1.5)] });
    const { ctx } = canvas(1080, 1920);
    const texts = recordText(ctx);
    drawEditorTextLayer(ctx, doc, 1080, 1920, 1.2, undefined, "captions");
    expect(texts).toContain("w****");
    expect(texts).not.toContain("world");
    const plain = canvas(1080, 1920);
    const plainTexts = recordText(plain.ctx);
    drawEditorTextLayer(plain.ctx, draft, 1080, 1920, 1.2, undefined, "captions");
    expect(plainTexts).toContain("world");
  });

  it("leaves FX-template captions to fx-core and gives their box for dragging", () => {
    const doc = withFx({}, { captions: { ...draft.captions, template: "slam" } });
    const { ctx } = canvas(1080, 1920);
    const texts = recordText(ctx);
    drawEditorTextLayer(ctx, doc, 1080, 1920, 1.2, undefined, "captions");
    expect(texts).toEqual([]);
    const box = fxCaptionBounds(doc, 1080, 1920, 1.2);
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(textLayerBounds(ctx, doc, 1080, 1920, 1.2, "captions")).toEqual(box);
  });

  it("gives a caption on an interview row its shift from the style's y, so its own transform starts there", () => {
    const rows = { y: 0.5, rows: [{ start: 0, end: 0.9, y: 0.5 }, { start: 0.9, end: 3, y: 0.65 }] };
    const doc = withFx({}, { captions: { ...draft.captions, template: "highlighter_swipe", y: 0.5 }, captionRows: rows });
    expect(fxCaptionRowShift(doc, 1080, 1920, 0.2)).toBeCloseTo(0, 9);
    expect(fxCaptionRowShift(doc, 1080, 1920, 1.2)).toBeCloseTo(0.15, 9);
    const box = fxCaptionBounds(doc, 1080, 1920, 1.2)!;
    expect(box.y + box.height / 2).toBeGreaterThan(0.6 * 1920);                // the drag box is where it is drawn
    const placed = { ...doc, captionTransforms: { '["s","b"]': { x: 0, y: 0.15, scale: 1, size: null } } };
    expect(fxCaptionRowShift(placed, 1080, 1920, 1.2)).toBe(0);                 // pinned: no row
    expect(fxCaptionBounds(placed, 1080, 1920, 1.2)!.y).toBeCloseTo(box.y, 6);  // and exactly where it was
    expect(fxCaptionRowShift({ ...doc, captionRows: undefined }, 1080, 1920, 1.2)).toBe(0);
  });

  it("hides a hook title the plan suppressed", () => {
    const titled = withFx({}, { hookTitle: "The big secret" });
    const { ctx } = canvas(1080, 1920);
    const texts = recordText(ctx);
    drawEditorTextLayer(ctx, titled, 1080, 1920, 1, undefined, "hook");
    expect(texts.length).toBeGreaterThan(0);
    const hidden = withFx({ hookTitleSuppressed: true }, { hookTitle: "The big secret", captions: { ...draft.captions, template: "slam" } });
    const second = canvas(1080, 1920);
    const none = recordText(second.ctx);
    drawEditorTextLayer(second.ctx, hidden, 1080, 1920, 1, undefined, "hook");
    expect(none).toEqual([]);
    expect(textLayerBounds(second.ctx, hidden, 1080, 1920, 1, "hook")).toBeNull();
  });
});

it("blends the loop crossfade only into frame 0 of the scene being drawn", () => {
  const solid = (color: string) => (rect: { x: number; y: number; width: number; height: number }, target: CanvasRenderingContext2D) => {
    target.fillStyle = color;
    target.fillRect(rect.x, rect.y, rect.width, rect.height);
  };
  const doc = withFx({ loopEnding: "crossfade" });
  const { ctx } = canvas();
  drawEditorFrame(ctx, doc, 1080, 1920, 0, solid("#FF0000"), undefined, null, "none");   // frame 0 is cached
  drawEditorFrame(ctx, doc, 1080, 1920, 2.95, solid("#0000FF"), undefined, null, "none");
  expect(mean(ctx)[0]).toBeGreaterThan(100);                    // the red first frame fades in over the blue end
  // Another draft on the same canvas (another clip, an edit) never blends in a stale first frame.
  drawEditorFrame(ctx, { ...doc }, 1080, 1920, 2.95, solid("#0000FF"), undefined, null, "none");
  expect(mean(ctx)[0]).toBeLessThan(5);
});

it("memoises the prepared scene per document and session", () => {
  const doc = withFx({ cues: [flash(1, 1.3)] });
  const a = editorFxScene(doc, undefined, 1080, 1920, 1080, 1920);
  expect(editorFxScene(doc, undefined, 1080, 1920, 1080, 1920)).toBe(a);
  expect(editorFxScene(doc, undefined, 1080, 1920, 1080, 1920, { base: "layout", matte: true })).not.toBe(a);
  expect(editorFxScene({ ...doc }, undefined, 1080, 1920, 1080, 1920)).not.toBe(a);
});
