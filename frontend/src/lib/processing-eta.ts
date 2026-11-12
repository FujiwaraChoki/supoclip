import type { Workflow } from "@/lib/workflows";

/**
 * Time-left estimate for a generation, phase by phase.
 *
 * The progress percentage is not a clock: transcription starts at 30% within
 * seconds, analysis and the segment download sit at 50-65% for most of a run,
 * and rendering covers 70-95%. Extrapolating elapsed time over that percentage
 * promised "less than a minute" on runs with several minutes to go. Instead
 * each phase has an expected duration, fitted on production runs (render
 * worker logs, 2026-11), from the source length and the number of outputs.
 */
export type EtaPhase = "fetch" | "transcribe" | "find" | "sections" | "render";

const PHASES: readonly EtaPhase[] = ["fetch", "transcribe", "find", "sections", "render"];

/** Used when the source length is unknown: a typical 15-minute video. */
const DEFAULT_SOURCE_SECONDS = 900;

export interface EtaInput {
  phase: EtaPhase;
  /** Seconds since the current phase (or, while rendering, the current clip) started. */
  phaseElapsed: number;
  sourceSeconds?: number | null;
  /** Uploads skip the selected-segment download; YouTube/Twitch/Kick transcribe audio first. */
  upload: boolean;
  workflow: Workflow;
  /** Outputs the run should make (the "clip x/y" total once rendering). */
  outputs: number;
  /** Clip being rendered, 1-based. */
  currentClip?: number;
  /** Measured seconds per clip in this run, once one has finished. */
  observedSecondsPerClip?: number | null;
}

/** Median render time of one output: parts and single cuts run longer than highlights. */
export function secondsPerClip(workflow: Workflow) {
  if (workflow === "series") return 35;
  if (workflow === "recap" || workflow === "trailer") return 40;
  return 20;
}

function phaseSeconds(phase: Exclude<EtaPhase, "render">, input: EtaInput) {
  const minutes = (input.sourceSeconds && input.sourceSeconds > 0 ? input.sourceSeconds : DEFAULT_SOURCE_SECONDS) / 60;
  switch (phase) {
    case "fetch": return input.upload ? 6 : 22;
    case "transcribe": return 11 + 0.62 * minutes;
    // Workflows plan with several model calls before any cut.
    case "find": return (input.workflow === "highlights" ? 7 : 30) + 0.43 * minutes;
    case "sections": return input.upload ? 0 : 85 + 5.9 * minutes;
  }
}

/**
 * What is left of a phase expected to take `expected` seconds after `elapsed`.
 * Past (or near) its expected length a phase is assumed to need another fifth
 * of what it has taken, so a slow phase raises the estimate instead of
 * pinning it at zero.
 */
function leftOf(expected: number, elapsed: number) {
  return Math.max(expected - elapsed, 0.2 * Math.max(expected, elapsed));
}

export function estimateRemainingSeconds(input: EtaInput) {
  const perClip = input.observedSecondsPerClip || secondsPerClip(input.workflow);
  const outputs = Math.max(1, input.outputs);
  if (input.phase === "render") {
    const current = Math.min(Math.max(1, input.currentClip ?? 1), outputs);
    return (outputs - current) * perClip + leftOf(perClip, input.phaseElapsed);
  }
  const index = PHASES.indexOf(input.phase);
  let remaining = leftOf(phaseSeconds(input.phase, input), input.phaseElapsed);
  for (const later of PHASES.slice(index + 1)) {
    remaining += later === "render" ? outputs * perClip : phaseSeconds(later, input);
  }
  return remaining;
}
