import { describe, expect, it, vi } from "vitest";

import { DEFAULT_EDIT_STYLE, FX_CAPTION_TEMPLATES, type EditStyle } from "./core/types";
import {
  EDIT_STYLES_UNAVAILABLE, editStylePreference, fetchEditStylesInfo, fxCreateFields, offeredFxTemplates,
  parseEditStylePreference, parseEditStylesInfo, readEditStyle, sendableCaptionTemplate,
} from "./edit-style-request";

const ENABLED = {
  enabled: true,
  intensities: ["off", "chill", "spicy", "unhinged"],
  defaults: { ...DEFAULT_EDIT_STYLE, intensity: undefined },
  settings: [],
  music_moods: ["hype", "chill", "dramatic", "funny", "inspiring", "dark"],
  grades: ["none", "punchy", "warm", "teal_orange", "noir", "vhs", "dream"],
  templates: [...FX_CAPTION_TEMPLATES, "from_the_future"],
  reactions_available: false,
};

const spicy: EditStyle = { ...DEFAULT_EDIT_STYLE, intensity: "spicy" };

describe("edit styles info", () => {
  it("is enabled only when the server says exactly true", () => {
    expect(parseEditStylesInfo(ENABLED).enabled).toBe(true);
    for (const body of [{ ...ENABLED, enabled: "true" }, { ...ENABLED, enabled: 1 }, {}, null, "x", []]) {
      expect(parseEditStylesInfo(body)).toBe(EDIT_STYLES_UNAVAILABLE);
    }
  });

  it("keeps only what this client knows", () => {
    const info = parseEditStylesInfo({ ...ENABLED, intensities: ["off", "spicy", "nuclear"], reactions_available: "yes" });
    expect(info.intensities).toEqual(["off", "spicy"]);
    expect(info.templates).toEqual(FX_CAPTION_TEMPLATES);
    expect(info.reactions_available).toBe(false);
    expect(offeredFxTemplates(info)).toEqual(FX_CAPTION_TEMPLATES);
    expect(offeredFxTemplates(EDIT_STYLES_UNAVAILABLE)).toEqual([]);
    expect(offeredFxTemplates(null)).toEqual([]);
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
    expect(fxCreateFields({ editStyle: spicy, captionTemplate: "hormozi" }, { enabled: true }))
      .toEqual({ caption_template: "hormozi", edit_style: { intensity: "spicy" } });
    expect(fxCreateFields({ editStyle: { ...spicy, music: false, censor: "bleep" }, captionTemplate: "slam" }, { enabled: true }))
      .toEqual({ caption_template: "slam", edit_style: { censor: "bleep", intensity: "spicy", music: false } });
    // Off is today's request, even with the flag on.
    expect(fxCreateFields({ editStyle: { ...DEFAULT_EDIT_STYLE, music: false }, captionTemplate: "default" }, { enabled: true }))
      .toEqual({ caption_template: "default" });
    // An animated template with intensity off is "style only": the template alone carries it.
    expect(fxCreateFields({ editStyle: DEFAULT_EDIT_STYLE, captionTemplate: "slam" }, { enabled: true }))
      .toEqual({ caption_template: "slam" });
  });

  it("drops a remembered GIF reactions choice the server can't serve", () => {
    const withReactions = { ...spicy, reactions: true };
    expect(fxCreateFields({ editStyle: withReactions, captionTemplate: "default" }, { enabled: true, reactions_available: false }))
      .toEqual({ caption_template: "default", edit_style: { intensity: "spicy" } });
    expect(fxCreateFields({ editStyle: withReactions, captionTemplate: "default" }, { enabled: true, reactions_available: true }))
      .toEqual({ caption_template: "default", edit_style: { intensity: "spicy", reactions: true } });
  });

  it.each([
    ["the flag off", { enabled: false }],
    ["a failed or 404 fetch", EDIT_STYLES_UNAVAILABLE],
    ["no answer yet", null],
  ])("drops a stored spicy style and animated template with %s", (_label, info) => {
    for (const template of FX_CAPTION_TEMPLATES) {
      const fields = fxCreateFields({ editStyle: spicy, captionTemplate: template }, info);
      expect(fields).toEqual({ caption_template: sendableCaptionTemplate(template, false) });
      expect(["default", "tiktok", "mrbeast", "minimal", "hormozi", "neon"]).toContain(fields.caption_template);
    }
    expect(fxCreateFields({ editStyle: spicy, captionTemplate: "podcast" }, info)).toEqual({ caption_template: "podcast" });
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
    expect(readEditStyle({ intensity: "spicy", from_the_future: 1, music: "loud", censor: "bleep" }))
      .toEqual({ ...DEFAULT_EDIT_STYLE, intensity: "spicy", censor: "bleep" });
    expect(readEditStyle('{"intensity":"unhinged","grade":"noir"}')).toEqual({ ...DEFAULT_EDIT_STYLE, intensity: "unhinged", grade: "noir" });
    for (const value of [null, undefined, "", "{nope", 3, [], { intensity: "nuclear" }]) expect(readEditStyle(value)).toEqual(DEFAULT_EDIT_STYLE);
  });

  it("remembers every changed choice, intensity included when off", () => {
    expect(editStylePreference(DEFAULT_EDIT_STYLE)).toEqual({});
    expect(editStylePreference({ ...DEFAULT_EDIT_STYLE, music: false })).toEqual({ music: false });
    expect(editStylePreference({ ...spicy, music_mood: "dark" })).toEqual({ intensity: "spicy", music_mood: "dark" });
    expect(parseEditStylePreference({ intensity: "chill", sfx: false, zooms: "yes", nope: true })).toEqual({ intensity: "chill", sfx: false });
    expect(parseEditStylePreference({})).toBeUndefined();
    expect(parseEditStylePreference({ intensity: "off" })).toBeUndefined();
    expect(parseEditStylePreference("spicy")).toBeUndefined();
  });
});
