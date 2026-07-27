import { describe, expect, it } from "vitest";

import { buildFontOptionsPayload } from "./font-options";

describe("buildFontOptionsPayload", () => {
  it("sends null for every field left at the template default", () => {
    expect(buildFontOptionsPayload(null, null, null)).toEqual({
      font_family: null,
      font_size: null,
      font_color: null,
    });
  });

  it("passes through only the fields the user explicitly customized", () => {
    expect(buildFontOptionsPayload("Inter", 28, "#FF0000")).toEqual({
      font_family: "Inter",
      font_size: 28,
      font_color: "#FF0000",
    });
  });

  it("supports customizing a single field while leaving the rest at template default", () => {
    expect(buildFontOptionsPayload(null, 40, null)).toEqual({
      font_family: null,
      font_size: 40,
      font_color: null,
    });
  });

  it("falls back to null when the color is not a valid hex string", () => {
    expect(buildFontOptionsPayload(null, null, "not-a-color")).toEqual({
      font_family: null,
      font_size: null,
      font_color: null,
    });
  });
});
