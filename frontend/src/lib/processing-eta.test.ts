import { describe, expect, it } from "vitest";

import { estimateRemainingSeconds, type EtaInput } from "./processing-eta";

const youtube = (overrides: Partial<EtaInput>): EtaInput => ({
  phase: "fetch",
  phaseElapsed: 0,
  sourceSeconds: 1566,
  upload: false,
  workflow: "highlights",
  outputs: 6,
  ...overrides,
});

describe("estimateRemainingSeconds", () => {
  // A 26-minute YouTube video made 6 clips in ~425s in production:
  // fetch 21s, transcribe 24s, analysis 20s, segment download 237s, render 122s.
  it("counts the segment download and rendering still ahead while transcribing", () => {
    const left = estimateRemainingSeconds(youtube({ phase: "transcribe", phaseElapsed: 2 }));
    expect(left).toBeGreaterThan(300);
    expect(left).toBeLessThan(500);
  });

  it("keeps several minutes for the segment download once analysis is done", () => {
    const left = estimateRemainingSeconds(youtube({ phase: "sections", phaseElapsed: 5 }));
    expect(left).toBeGreaterThan(240);
  });

  it("skips the segment download for uploads", () => {
    const upload = estimateRemainingSeconds(youtube({ phase: "find", upload: true }));
    const stream = estimateRemainingSeconds(youtube({ phase: "find" }));
    expect(stream - upload).toBeGreaterThan(200);
  });

  it("scales with the source length", () => {
    const short = estimateRemainingSeconds(youtube({ phase: "transcribe", sourceSeconds: 300, outputs: 1 }));
    const long = estimateRemainingSeconds(youtube({ phase: "transcribe", sourceSeconds: 5400, outputs: 10 }));
    expect(long).toBeGreaterThan(short * 3);
  });

  it("counts the clips left to render, at the measured pace once known", () => {
    expect(estimateRemainingSeconds(youtube({ phase: "render", currentClip: 1, outputs: 6 }))).toBe(120);
    expect(estimateRemainingSeconds(youtube({ phase: "render", currentClip: 3, outputs: 6, observedSecondsPerClip: 40 }))).toBe(160);
    expect(estimateRemainingSeconds(youtube({ phase: "render", currentClip: 6, outputs: 6, phaseElapsed: 10 }))).toBe(10);
  });

  it("renders longer outputs for series", () => {
    const series = estimateRemainingSeconds(youtube({ phase: "render", currentClip: 1, outputs: 4, workflow: "series" }));
    const highlights = estimateRemainingSeconds(youtube({ phase: "render", currentClip: 1, outputs: 4 }));
    expect(series).toBeGreaterThan(highlights);
  });

  it("never reaches zero while a phase runs over", () => {
    const expected = estimateRemainingSeconds(youtube({ phase: "render", currentClip: 6, outputs: 6, phaseElapsed: 20 }));
    const late = estimateRemainingSeconds(youtube({ phase: "render", currentClip: 6, outputs: 6, phaseElapsed: 200 }));
    expect(expected).toBeGreaterThan(0);
    expect(late).toBeGreaterThan(expected);
  });

  it("assumes a typical source when the length is unknown", () => {
    expect(estimateRemainingSeconds(youtube({ phase: "fetch", sourceSeconds: null }))).toBeGreaterThan(60);
  });
});
