import { describe, expect, it } from "vitest";

import { buildFontOptionsPayload, savedCaptionDefaults } from "./font-options";

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

describe("savedCaptionDefaults", () => {
  it("turns customized Settings defaults into create-form overrides", () => {
    expect(savedCaptionDefaults({ fontFamily: "Inter", fontSize: 32, fontColor: "#FFD700" })).toEqual({
      fontFamily: "Inter",
      fontSize: 32,
      fontColor: "#FFD700",
    });
  });

  it("leaves untouched stock values to the caption template", () => {
    expect(savedCaptionDefaults({ fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#ffffff" })).toEqual({
      fontFamily: null,
      fontSize: null,
      fontColor: null,
    });
  });

  it("applies only the fields that were customized", () => {
    expect(savedCaptionDefaults({ fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#FF6B6B" })).toEqual({
      fontFamily: null,
      fontSize: null,
      fontColor: "#FF6B6B",
    });
  });

  it("ignores missing or invalid preferences", () => {
    expect(savedCaptionDefaults(null)).toEqual({ fontFamily: null, fontSize: null, fontColor: null });
    expect(savedCaptionDefaults({ fontFamily: " ", fontSize: 99, fontColor: "red" })).toEqual({
      fontFamily: null,
      fontSize: null,
      fontColor: null,
    });
  });
});
