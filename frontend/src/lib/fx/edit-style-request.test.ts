import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { DEFAULT_EDIT_STYLE, FX_CAPTION_TEMPLATES, type EditStyle } from "./core/types";
import {
  EDIT_STYLES_UNAVAILABLE, editStylePreference, fetchEditStylesInfo, fxCreateFields, offeredFxTemplates,
  MOODS, parseEditStylePreference, parseEditStylesInfo, readEditStyle, sendableCaptionTemplate,
} from "./edit-style-request";

const ENABLED = {
  enabled: true,
  intensities: ["off", "subtle", "bold", "max"],
  defaults: { ...DEFAULT_EDIT_STYLE, intensity: undefined },
  settings: [],
  music_moods: ["hype", "chill", "dramatic", "funny", "inspiring", "dark", "aggressive"],
  grades: ["none", "punchy", "warm", "teal_orange", "noir", "vhs", "dream"],
  templates: [...FX_CAPTION_TEMPLATES, "from_the_future"],
  reactions_available: false,
};

const bold: EditStyle = { ...DEFAULT_EDIT_STYLE, intensity: "bold" };

describe("edit styles info", () => {
  it("is enabled only when the server says exactly true", () => {
    expect(parseEditStylesInfo(ENABLED).enabled).toBe(true);
    for (const body of [{ ...ENABLED, enabled: "true" }, { ...ENABLED, enabled: 1 }, {}, null, "x", []]) {
      expect(parseEditStylesInfo(body)).toBe(EDIT_STYLES_UNAVAILABLE);
    }
  });

  it("keeps only what this client knows", () => {
    const info = parseEditStylesInfo({ ...ENABLED, intensities: ["off", "bold", "nuclear"], reactions_available: "yes" });
    expect(info.intensities).toEqual(["off", "bold"]);
    expect(info.templates).toEqual(FX_CAPTION_TEMPLATES);
    expect(info.reactions_available).toBe(false);
    expect(offeredFxTemplates(info)).toEqual(FX_CAPTION_TEMPLATES);
    expect(offeredFxTemplates(EDIT_STYLES_UNAVAILABLE)).toEqual([]);
    expect(offeredFxTemplates(null)).toEqual([]);
  });

  it("knows every music mood the backend lists, aggressive included, and drops unknown ones", () => {
    expect(MOODS).toEqual(["hype", "chill", "dramatic", "funny", "inspiring", "dark", "aggressive"]);
    expect(parseEditStylesInfo({ ...ENABLED, music_moods: [...ENABLED.music_moods, "polka"] }).music_moods).toEqual(MOODS);
    expect(parseEditStylePreference({ intensity: "bold", music_mood: "aggressive" })?.music_mood).toBe("aggressive");
  });

  it("names every music mood in every language, on the create screen and in the studio", () => {
    for (const locale of ["en", "es", "pt", "de", "zh", "id"]) {
      const read = (file: string) => JSON.parse(readFileSync(join(process.cwd(), "messages", locale, file), "utf8"));
      const create = read("home.json").edits.moods;
      const studio = read("editor.json").fx.moods;
      for (const mood of ["auto", ...MOODS]) {
        expect(create[mood], `${locale} home edits.moods.${mood}`).toBeTruthy();
        expect(studio[mood], `${locale} editor fx.moods.${mood}`).toBeTruthy();
      }
    }
  });

  it("reads a failed fetch, an error status and an old backend's 404 as not enabled", async () => {
    const fetcher = vi.fn();
    fetcher.mockResolvedValueOnce(Response.json(ENABLED));
    expect((await fetchEditStylesInfo(fetcher)).enabled).toBe(true);
    expect(fetcher).toHaveBeenLastCalledWith("/api/tasks/edit-styles", { cache: "no-store" });
    fetcher.mockResolvedValueOnce(new Response("Not found", { status: 404 }));
    expect(await fetchEditStylesInfo(fetcher)).toBe(EDIT_STYLES_UNAVAILABLE);
    fetcher.mockResolvedValueOnce(new Response("oops", { status: 500 }));
    expect(await fetchEditStylesInfo(fetcher)).toBe(EDIT_STYLES_UNAVAILABLE);
    fetcher.mockRejectedValueOnce(new TypeError("offline"));
    expect(await fetchEditStylesInfo(fetcher)).toBe(EDIT_STYLES_UNAVAILABLE);
    fetcher.mockResolvedValueOnce(new Response("<html>", { status: 200 }));
    expect(await fetchEditStylesInfo(fetcher)).toBe(EDIT_STYLES_UNAVAILABLE);
  });
});

describe("send-time sanitiser", () => {
  it("adds edit_style only for an active style with the flag on", () => {
    expect(fxCreateFields({ editStyle: bold, captionTemplate: "hormozi" }, { enabled: true }))
      .toEqual({ caption_template: "hormozi", edit_style: { intensity: "bold" } });
    expect(fxCreateFields({ editStyle: { ...bold, music: false, censor: "bleep" }, captionTemplate: "slam" }, { enabled: true }))
      .toEqual({ caption_template: "slam", edit_style: { censor: "bleep", intensity: "bold", music: false } });
    // Off is today's request, even with the flag on.
    expect(fxCreateFields({ editStyle: { ...DEFAULT_EDIT_STYLE, music: false }, captionTemplate: "default" }, { enabled: true }))
      .toEqual({ caption_template: "default" });
    // An animated template with intensity off is "style only": the template alone carries it.
    expect(fxCreateFields({ editStyle: DEFAULT_EDIT_STYLE, captionTemplate: "slam" }, { enabled: true }))
      .toEqual({ caption_template: "slam" });
  });

  it("drops a remembered GIF reactions choice the server can't serve", () => {
    const withReactions = { ...bold, reactions: true };
    expect(fxCreateFields({ editStyle: withReactions, captionTemplate: "default" }, { enabled: true, reactions_available: false }))
      .toEqual({ caption_template: "default", edit_style: { intensity: "bold" } });
    expect(fxCreateFields({ editStyle: withReactions, captionTemplate: "default" }, { enabled: true, reactions_available: true }))
      .toEqual({ caption_template: "default", edit_style: { intensity: "bold", reactions: true } });
  });

  it.each([
    ["the flag off", { enabled: false }],
    ["a failed or 404 fetch", EDIT_STYLES_UNAVAILABLE],
    ["no answer yet", null],
  ])("drops a stored bold style and animated template with %s", (_label, info) => {
    for (const template of FX_CAPTION_TEMPLATES) {
      const fields = fxCreateFields({ editStyle: bold, captionTemplate: template }, info);
      expect(fields).toEqual({ caption_template: sendableCaptionTemplate(template, false) });
      expect(["default", "tiktok", "mrbeast", "minimal", "hormozi", "neon"]).toContain(fields.caption_template);
    }
    expect(fxCreateFields({ editStyle: bold, captionTemplate: "podcast" }, info)).toEqual({ caption_template: "podcast" });
    expect(fxCreateFields({ editStyle: null, captionTemplate: null }, info)).toEqual({ caption_template: "default" });
  });

  it("maps each animated template to its fallback", () => {
    expect(sendableCaptionTemplate("behind_head", false)).toBe("minimal");
    expect(sendableCaptionTemplate("stat_counter", false)).toBe("hormozi");
    expect(sendableCaptionTemplate("behind_head", true)).toBe("behind_head");
    expect(sendableCaptionTemplate("", false)).toBe("default");
  });
});

describe("stored styles", () => {
  it("reads a row like the backend: a dict or a JSON string, unknown keys dropped, bad values defaulted", () => {
    expect(readEditStyle({ intensity: "bold", from_the_future: 1, music: "loud", censor: "bleep" }))
      .toEqual({ ...DEFAULT_EDIT_STYLE, intensity: "bold", censor: "bleep" });
    expect(readEditStyle('{"intensity":"max","grade":"noir"}')).toEqual({ ...DEFAULT_EDIT_STYLE, intensity: "max", grade: "noir" });
    for (const value of [null, undefined, "", "{nope", 3, [], { intensity: "nuclear" }]) expect(readEditStyle(value)).toEqual(DEFAULT_EDIT_STYLE);
  });

  it("remembers every changed choice, intensity included when off", () => {
    expect(editStylePreference(DEFAULT_EDIT_STYLE)).toEqual({});
    expect(editStylePreference({ ...DEFAULT_EDIT_STYLE, music: false })).toEqual({ music: false });
    expect(editStylePreference({ ...bold, music_mood: "dark" })).toEqual({ intensity: "bold", music_mood: "dark" });
    expect(parseEditStylePreference({ intensity: "subtle", sfx: false, zooms: "yes", nope: true })).toEqual({ intensity: "subtle", sfx: false });
    expect(parseEditStylePreference({})).toBeUndefined();
    expect(parseEditStylePreference({ intensity: "off" })).toBeUndefined();
    expect(parseEditStylePreference("bold")).toBeUndefined();
  });
});
