import { expect, it } from "vitest";
import { draft } from "./document.test-fixture";
import { formatHookEditorText, prepareTextDraft, textEditorLayout } from "./render";

function context() {
  return {
    font: "10px sans-serif",
    measureText(this: { font: string }, text: string) {
      const px = Number.parseFloat(this.font);
      return { width: text.length * px * .5, fontBoundingBoxAscent: px * .9, fontBoundingBoxDescent: px * .3 };
    },
  } as unknown as CanvasRenderingContext2D;
}

it.each(["hook", "captions"] as const)("uses the canvas font-height correction for the %s native caret", layer => {
  const layout = textEditorLayout(context(), { ...draft, hookTitle: "Editable title" }, 1080, 1920, .2, layer);
  const assPixels = layer === "hook" ? 44 : 54;
  expect(layout.fontSize).toBeCloseTo(assPixels / 1.2);
  expect(layout.lineHeight).toBeCloseTo(assPixels * 1.2);
});

it("keeps original word timings and highlights in an unchanged editing draft", () => {
  const before = structuredClone(draft);
  const scratch = prepareTextDraft(draft, .2, "captions", "Hello world today");
  expect(scratch.captionWords).toEqual(draft.words);
  expect(prepareTextDraft(draft, .2, "captions", "A revised phrase").captionWords?.map(word => word.text)).toEqual(["A", "revised", "phrase"]);
  expect(draft).toEqual(before);
});

it("balances hook editor lines without changing stored casing or eating a typed space", () => {
  const text = "Mixed Case Äpfel remain editable while typing ";
  const formatted = formatHookEditorText({ ...draft, captions: { ...draft.captions, uppercase: true } }, 1080, 1920, text);
  expect(formatted.replace(/\s+/g, " ")).toBe(text);
  expect(prepareTextDraft(draft, .2, "hook", formatted).hookText).toBe(text.trim());
});
