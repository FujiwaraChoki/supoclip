import { renderHook } from "@testing-library/react";
import { useTranslations } from "next-intl";
import { expect, it } from "vitest";

import { clipPickerLabel } from "./editor-workspace";

const base = { id: "c", filename: "c.mp4", clip_order: 3, duration: 60, text: "", video_url: "/x" };

it("labels series parts by number and title, other clips by hook title", () => {
  const { result } = renderHook(() => useTranslations("editor"));
  const t = result.current;
  expect(clipPickerLabel(t, { ...base, workflow_role: "part", part_number: 2, hook_title: "Part 2: The bank calls back" }))
    .toBe("Part 2 · The bank calls back");
  expect(clipPickerLabel(t, { ...base, workflow_role: "part", part_number: 2, hook_title: null, workflow_meta: { title: "Planned title" } }))
    .toBe("Part 2 · Planned title");
  expect(clipPickerLabel(t, { ...base, workflow_role: "part", part_number: 4 })).toBe("Part 4");
  expect(clipPickerLabel(t, { ...base, hook_title: "Why prices lie" })).toBe("Why prices lie");
  expect(clipPickerLabel(t, base)).toBe("Clip 3");
});
