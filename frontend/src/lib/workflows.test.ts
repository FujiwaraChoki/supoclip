import { describe, expect, it } from "vitest";
import {
  defaultSort,
  expectedHighlightCount,
  expectedOutputs,
  INSTRUCTIONS_MAX_CHARS,
  isWorkflow,
  MIN_SOURCE_SECONDS,
  normalizeTopic,
  PART_COUNT_OPTIONS,
  SCORED,
  SINGLE_OUTPUT,
  taskWorkflow,
  TOPIC_MAX_CHARS,
  topicIssue,
  WORKFLOWS,
} from "./workflows";

describe("workflows contract", () => {
  it("recognises only the five workflow ids", () => {
    expect(WORKFLOWS).toEqual(["highlights", "series", "topic", "recap", "trailer"]);
    for (const workflow of WORKFLOWS) expect(isWorkflow(workflow)).toBe(true);
    for (const value of ["Series", "", null, undefined, 1, {}, "bogus"]) expect(isWorkflow(value)).toBe(false);
  });

  it("reads unknown task workflows as highlights", () => {
    expect(taskWorkflow({ workflow: "recap" })).toBe("recap");
    expect(taskWorkflow({ workflow: "bogus" })).toBe("highlights");
    expect(taskWorkflow({})).toBe("highlights");
    expect(taskWorkflow(null)).toBe("highlights");
  });

  it("mirrors the backend limits", () => {
    expect(MIN_SOURCE_SECONDS).toEqual({ highlights: 0, series: 120, topic: 60, recap: 180, trailer: 180 });
    expect([TOPIC_MAX_CHARS, INSTRUCTIONS_MAX_CHARS]).toEqual([120, 500]);
    expect(PART_COUNT_OPTIONS).toEqual(["auto", 2, 3, 4, 5, 6, 8, 10]);
    expect([...SINGLE_OUTPUT]).toEqual(["recap", "trailer"]);
    expect([...SCORED]).toEqual(["highlights", "topic"]);
  });

  it("sorts series by timeline and everything else by score", () => {
    expect(WORKFLOWS.map(defaultSort)).toEqual(["score", "timeline", "score", "score", "score"]);
  });

  it("expects one output for recaps and trailers", () => {
    expect(expectedOutputs("recap", 3600)).toBe(1);
    expect(expectedOutputs("trailer", null, 4)).toBe(1);
  });

  it("expects the chosen part count, else about one part per 90 seconds", () => {
    expect(expectedOutputs("series", 3600, 8)).toBe(8);
    expect(expectedOutputs("series", 120)).toBe(2);
    expect(expectedOutputs("series", 360)).toBe(4);
    expect(expectedOutputs("series", 3600)).toBe(6);
    expect(expectedOutputs("series", null)).toBe(4);
  });

  it("keeps the highlights estimate", () => {
    expect(expectedOutputs("topic", 3600)).toBe(5);
    expect(expectedOutputs("highlights", 1500)).toBe(expectedHighlightCount(1500));
    expect([expectedHighlightCount(null), expectedHighlightCount(60), expectedHighlightCount(1500), expectedHighlightCount(36000)]).toEqual([4, 1, 5, 10]);
  });

  it("normalizes topics like the API", () => {
    expect(normalizeTopic("  building\n\ta\u0000  business\u202e ")).toBe("building a business");
    expect(normalizeTopic("pric\u00ading\u200b stra\ufefftegy\ud800")).toBe("pricing strategy");
    expect(normalizeTopic("\u{1F469}\u200d\u{1F4BB} نم\u200cا")).toBe("\u{1F469}\u200d\u{1F4BB} نم\u200cا");
  });

  it("checks topics like the API", () => {
    for (const topic of ["", "  ", "a", "!!", "?", "\u200b\u200b", "\ud800\udfff"]) expect(topicIssue(topic)).toBe("TOPIC_REQUIRED");
    for (const topic of ["钱", "猫", "お", "돈", "\u{20000}", "AI", "C#", "5G", "धन", "building a business"]) expect(topicIssue(topic)).toBeNull();
    expect(topicIssue("x".repeat(TOPIC_MAX_CHARS))).toBeNull();
    expect(topicIssue("x".repeat(TOPIC_MAX_CHARS + 1))).toBe("TOPIC_TOO_LONG");
    // Characters, not UTF-16 units: 120 supplementary ideographs (240 units) fit.
    expect(topicIssue("\u{20000}".repeat(TOPIC_MAX_CHARS))).toBeNull();
    expect(topicIssue(` ${"word ".repeat(30)} `)).toBe("TOPIC_TOO_LONG");
    expect(topicIssue(` ${"word ".repeat(24)} `)).toBeNull();
  });
});
