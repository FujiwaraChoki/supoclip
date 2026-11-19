// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  DEFAULT_EDIT_STYLE, EDIT_FX_VERSION, FPS, FRAME, FX_CAPTION_TEMPLATES, INSTANT_KINDS, TRANSITION_HALF,
  editStyleToJson, isFxTemplate, type EditStyle,
} from "./types";

describe("fx-core types", () => {
  it("exposes the contract constants", () => {
    expect(EDIT_FX_VERSION).toBe("fx-v2");
    expect(FPS).toBe(30);
    expect(FRAME).toBeCloseTo(1 / 30, 15);
    expect([...INSTANT_KINDS].sort()).toEqual(["freeze", "sfx", "transition"]);
    expect(FX_CAPTION_TEMPLATES).toHaveLength(9);
    expect(TRANSITION_HALF).toEqual({ whip: 0.12, flash: 0.05, glitch: 0.1, zoom_through: 0.18, spin: 0.2, light_leak: 0.1 });
  });

  it("isFxTemplate only knows the 10 FX templates", () => {
    expect(isFxTemplate("marker_circle")).toBe(true);
    expect(isFxTemplate("face_doodles")).toBe(true);
    for (const legacy of ["default", "hormozi", "mrbeast", "minimal", "tiktok", "neon", "podcast"]) {
      expect(isFxTemplate(legacy)).toBe(false);
    }
    expect(isFxTemplate(null)).toBe(false);
    expect(isFxTemplate(undefined)).toBe(false);
    expect(isFxTemplate("")).toBe(false);
  });

  describe("editStyleToJson (mirror of EditStyle.to_json)", () => {
    it("is {} when off, whatever else is set", () => {
      expect(editStyleToJson(DEFAULT_EDIT_STYLE)).toEqual({});
      expect(editStyleToJson({ ...DEFAULT_EDIT_STYLE, grain: true, music: false })).toEqual({});
    });

    it("keeps intensity and only the non-default keys", () => {
      expect(editStyleToJson({ ...DEFAULT_EDIT_STYLE, intensity: "bold" })).toEqual({ intensity: "bold" });
      const style: EditStyle = {
        ...DEFAULT_EDIT_STYLE, intensity: "max", music_mood: "hype", progress_bar: true, sfx: false,
        censor: "bleep_bar",   // default: dropped
      };
      expect(editStyleToJson(style)).toEqual({ intensity: "max", music_mood: "hype", progress_bar: true, sfx: false });
    });

    it("emits keys in sorted order like Python's sorted()", () => {
      const json = editStyleToJson({
        ...DEFAULT_EDIT_STYLE, intensity: "subtle", zooms: false, censor: "off", wait_for_it: false, grade: "noir",
        loop_ending: true,
      });
      expect(Object.keys(json)).toEqual(["censor", "grade", "intensity", "loop_ending", "wait_for_it", "zooms"]);
      expect(JSON.stringify(json)).toBe(
        '{"censor":"off","grade":"noir","intensity":"subtle","loop_ending":true,"wait_for_it":false,"zooms":false}',
      );
    });

    it("ignores keys the contract does not declare", () => {
      const style = { ...DEFAULT_EDIT_STYLE, intensity: "bold", extra: 1 } as unknown as EditStyle;
      expect(editStyleToJson(style)).toEqual({ intensity: "bold" });
    });
  });
});
