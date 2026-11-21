import { describe, expect, it } from "vitest";

import { parseCreateSettings } from "./create-settings";

describe("parseCreateSettings", () => {
  it("keeps every well-formed setting", () => {
    const settings = {
      workflow: "trailer",
      captionTemplate: "minimal",
      addSubtitles: false,
      outputFormat: "original",
      includeBroll: true,
      cutLongPauses: false,
      pauseThresholdMs: 800,
      removeFillerWords: false,
      filteredWords: "um",
      lengths: { series: "long" },
      advanced: { trailer: { spoilers: "strict", target_seconds: 40, hook_title: false } },
      editStyle: { intensity: "bold", music: false, music_mood: "dark", censor: "bleep" },
    };
    expect(parseCreateSettings(settings)).toEqual(settings);
  });

  it("drops anything else, including keys that aren't settings", () => {
    expect(parseCreateSettings({
      workflow: "montage",
      topic: "secret",
      instructions: "secret",
      outputFormat: "square",
      pauseThresholdMs: 10,
      filteredWords: "x".repeat(2001),
      lengths: { highlights: "long", recap: "huge" },
      advanced: { montage: { a: true }, series: { "bad key": true, cold_open: { nested: true }, part_labels: false } },
    })).toEqual({ advanced: { series: { part_labels: false } } });
    for (const value of [null, "x", 3, []]) expect(parseCreateSettings(value)).toEqual({});
  });

  it("keeps a well-formed edit style, value by value", () => {
    expect(parseCreateSettings({ editStyle: { intensity: "nuclear", sfx: false, grade: "sepia", reactions: true, extra: 1 } }))
      .toEqual({ editStyle: { sfx: false, reactions: true } });
    // Defaults and junk leave nothing to remember.
    for (const editStyle of [{}, { intensity: "off" }, "bold", null, []]) expect(parseCreateSettings({ editStyle })).toEqual({});
  });
});
