import { expect, it } from "vitest";
import { draft } from "./document.test-fixture";
import { hookRange, trimHook } from "./document";
it("keeps generated timing for old drafts", () => {
  expect(hookRange(draft)).toEqual({ start: .12, end: 2.75 });
});
it("allows a title to extend past four seconds over an edited sequence", () => {
  const doc = { ...draft, segments: [...draft.segments, { id: "repeat", start: 0, end: 3 }] };
  expect(hookRange(trimHook(doc, "end", 5.5))).toEqual({ start: .12, end: 5.5 });
});
it("keeps timing edges ordered and within the clip", () => {
  let doc = trimHook(draft, "start", 99);
  expect(hookRange(doc)).toEqual({ start: 2.65, end: 2.75 });
  doc = trimHook(doc, "end", -99);
  expect(hookRange(doc).end - hookRange(doc).start).toBeCloseTo(.1);
  expect(hookRange(trimHook(draft, "end", 99)).end).toBe(3);
});
it("clamps explicit timing after a trim and uses output time after reordering", () => {
  const doc = { ...draft, hookTiming: { start: 1, end: 2 }, segments: [{ id: "cut", start: 20, end: 23 }] };
  expect(hookRange(doc)).toEqual({ start: 1, end: 2 });
  expect(hookRange({ ...doc, segments: [{ id: "short", start: 20, end: 20.5 }] })).toEqual({ start: .46, end: .5 });
});
