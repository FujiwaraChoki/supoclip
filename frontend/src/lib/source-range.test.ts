import { describe, expect, it } from "vitest";

import {
  defaultSourceRange,
  formatClock,
  moveSourceRange,
  parseClock,
  shiftStart,
  sourceRangeFields,
  sourceRangeProblem,
  tooLongRangeOffer,
} from "./source-range";

const EIGHT_HOURS = { durationSeconds: 8 * 3600, maxSeconds: 7200, minSeconds: 60 };

describe("formatClock", () => {
  it("shows hours only when there are some", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(125)).toBe("2:05");
    expect(formatClock(3723)).toBe("1:02:03");
    expect(formatClock(8 * 3600)).toBe("8:00:00");
  });
});

describe("parseClock", () => {
  it.each([
    ["1:02:03", 3723],
    ["62:03", 3723],
    ["0:00", 0],
    [" 2:00:00 ", 7200],
    ["90", 5400],
    ["1.5", 90],
    ["1h 30m", 5400],
    ["2h", 7200],
    ["45m", 2700],
    ["1h2m3s", 3723],
  ])("reads %s", (text, seconds) => {
    expect(parseClock(text)).toBe(seconds);
  });

  it.each(["", "abc", "1:75", "1:02:60", "-5", "1::2"])("rejects %j", (text) => {
    expect(parseClock(text)).toBeNull();
  });
});

describe("defaultSourceRange", () => {
  it("starts at the beginning and takes as much as the plan allows", () => {
    expect(defaultSourceRange(EIGHT_HOURS)).toEqual({ start: 0, end: 7200 });
    expect(defaultSourceRange({ durationSeconds: 5000.6, maxSeconds: 7200 })).toEqual({ start: 0, end: 5000 });
  });
});

describe("sourceRangeProblem", () => {
  it("accepts a part within the limit", () => {
    expect(sourceRangeProblem({ start: 3600, end: 3600 + 7200 }, EIGHT_HOURS)).toBeNull();
  });

  it.each([
    [{ start: 100, end: 100 }, "order"],
    [{ start: 200, end: 100 }, "order"],
    [{ start: -1, end: 100 }, "outOfBounds"],
    [{ start: 0, end: 8 * 3600 + 5 }, "outOfBounds"],
    [{ start: 0, end: 30 }, "tooShort"],
    [{ start: 0, end: 7201 }, "tooLong"],
    [{ start: Number.NaN, end: 100 }, "invalid"],
  ] as const)("flags %j as %s", (range, problem) => {
    expect(sourceRangeProblem(range, EIGHT_HOURS)).toBe(problem);
  });
});

describe("moveSourceRange", () => {
  it("lets the window shrink freely", () => {
    expect(moveSourceRange({ start: 0, end: 7200 }, { start: 600, end: 7200 }, EIGHT_HOURS)).toEqual({ start: 600, end: 7200 });
  });

  it("drags the end along when the start moves past the limit", () => {
    expect(moveSourceRange({ start: 3600, end: 10800 }, { start: 1800, end: 10800 }, EIGHT_HOURS)).toEqual({ start: 1800, end: 9000 });
  });

  it("drags the start along when the end moves past the limit", () => {
    expect(moveSourceRange({ start: 0, end: 7200 }, { start: 0, end: 9000 }, EIGHT_HOURS)).toEqual({ start: 1800, end: 9000 });
  });

  it("stays inside the video", () => {
    expect(moveSourceRange({ start: 0, end: 7200 }, { start: 0, end: 30000 }, EIGHT_HOURS)).toEqual({ start: 21600, end: 28800 });
  });
});

describe("sourceRangeFields", () => {
  it("adds nothing for the whole video", () => {
    expect(sourceRangeFields(null)).toEqual({});
  });

  it("sends whole seconds", () => {
    expect(sourceRangeFields({ start: 60.4, end: 7260.6 })).toEqual({ start_seconds: 60, end_seconds: 7261 });
  });
});

describe("tooLongRangeOffer", () => {
  const detail = { duration_seconds: 28800, max_duration_seconds: 7200, min_range_seconds: 60, range_allowed: true };

  it("offers a part of a video that is over the limit", () => {
    expect(tooLongRangeOffer({ code: "VIDEO_TOO_LONG", detail })).toEqual({ durationSeconds: 28800, maxSeconds: 7200, minSeconds: 60 });
  });

  it("falls back to the default minimum", () => {
    expect(tooLongRangeOffer({ code: "VIDEO_TOO_LONG", detail: { duration_seconds: 9000, max_duration_seconds: 7200 } })?.minSeconds).toBe(60);
  });

  it.each([
    ["another error", { code: "VIDEO_DURATION_UNAVAILABLE", detail }],
    ["a livestream", { code: "VIDEO_TOO_LONG", detail: { ...detail, range_allowed: false } }],
    ["an unknown length", { code: "VIDEO_TOO_LONG", detail: { max_duration_seconds: 7200 } }],
    ["no detail", { code: "VIDEO_TOO_LONG", detail: undefined }],
  ])("offers nothing for %s", (_name, info) => {
    expect(tooLongRangeOffer(info)).toBeNull();
  });
});

describe("shiftStart", () => {
  it("moves the part and keeps its length", () => {
    expect(shiftStart({ start: 0, end: 7200 }, 5400, EIGHT_HOURS)).toEqual({ start: 5400, end: 12600 });
  });

  it("stops at the end of the video", () => {
    expect(shiftStart({ start: 0, end: 7200 }, 27000, EIGHT_HOURS)).toEqual({ start: 27000, end: 28800 });
  });

  it("never keeps a length over the limit", () => {
    expect(shiftStart({ start: 0, end: 10800 }, 3600, EIGHT_HOURS)).toEqual({ start: 3600, end: 10800 });
  });
});
