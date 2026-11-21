/**
 * Clip only part of a long source: the range a user picks when a video is over
 * the plan's per-video limit. Times are whole seconds into the source. The
 * server validates the same rules (`INVALID_SOURCE_RANGE`); this is only so the
 * form can say what's wrong before sending.
 */

import type { ApiErrorInfo } from "@/lib/api-error";

export type SourceRange = { start: number; end: number };

/** Shortest part worth clipping. Mirrors the backend's minimum; the server's `min_range_seconds` wins when it sends one. */
export const DEFAULT_MIN_RANGE_SECONDS = 60;

export type SourceRangeProblem = "invalid" | "order" | "outOfBounds" | "tooShort" | "tooLong";

export type SourceRangeLimits = {
  durationSeconds: number;
  maxSeconds: number;
  minSeconds?: number;
};

/** "1:02:03" for an hour or more, else "2:03". */
export function formatClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/**
 * Reads "1:02:03", "62:03" or "1h 2m 3s". A bare number is minutes ("90" = 1:30:00),
 * since nobody types a start time in seconds into a five-hour stream. Returns null
 * when the text isn't a time.
 */
export function parseClock(text: string): number | null {
  const value = text.trim().toLowerCase().replace(/\s+/g, "");
  if (!value) return null;
  if (/^\d+(\.\d+)?$/.test(value)) return Math.round(Number(value) * 60);
  if (/^\d+(:\d{1,2}){1,2}$/.test(value)) {
    const parts = value.split(":").map(Number);
    if (parts.slice(1).some((part) => part >= 60)) return null;
    return parts.reduce((total, part) => total * 60 + part, 0);
  }
  const units = /^(?:(\d+)h)?(?:(\d+)m(?:in)?)?(?:(\d+)s)?$/.exec(value);
  if (units && (units[1] || units[2] || units[3])) {
    return Number(units[1] ?? 0) * 3600 + Number(units[2] ?? 0) * 60 + Number(units[3] ?? 0);
  }
  return null;
}

/** The first part of the video, as long as the plan allows. */
export function defaultSourceRange({ durationSeconds, maxSeconds }: SourceRangeLimits): SourceRange {
  return { start: 0, end: Math.floor(Math.min(durationSeconds, maxSeconds)) };
}

export function sourceRangeProblem(range: SourceRange, limits: SourceRangeLimits): SourceRangeProblem | null {
  const { start, end } = range;
  const minSeconds = limits.minSeconds ?? DEFAULT_MIN_RANGE_SECONDS;
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "invalid";
  if (start < 0 || end > Math.ceil(limits.durationSeconds)) return "outOfBounds";
  if (end <= start) return "order";
  if (end - start < Math.min(minSeconds, limits.durationSeconds)) return "tooShort";
  if (end - start > limits.maxSeconds) return "tooLong";
  return null;
}

/**
 * Applies a slider move. Dragging one end past the limit pulls the other end
 * along, so the window keeps its maximum length instead of refusing to move.
 */
export function moveSourceRange(
  previous: SourceRange,
  next: SourceRange,
  { durationSeconds, maxSeconds }: SourceRangeLimits,
): SourceRange {
  const duration = Math.floor(durationSeconds);
  let start = Math.max(0, Math.min(next.start, duration));
  let end = Math.max(0, Math.min(next.end, duration));
  if (end - start > maxSeconds) {
    if (start !== previous.start) end = Math.min(duration, start + maxSeconds);
    else start = Math.max(0, end - maxSeconds);
  }
  return { start, end };
}

/** A typed start moves the whole part, keeping its length (up to the limit) where the video allows. */
export function shiftStart(previous: SourceRange, start: number, { durationSeconds, maxSeconds }: SourceRangeLimits): SourceRange {
  const duration = Math.floor(durationSeconds);
  const length = Math.min(Math.max(previous.end - previous.start, 0), maxSeconds);
  return { start, end: Math.min(duration, start + length) };
}

/** Request fields for `source`: nothing for the whole video, so that request stays exactly as before. */
export function sourceRangeFields(range: SourceRange | null): { start_seconds?: number; end_seconds?: number } {
  if (!range) return {};
  return { start_seconds: Math.round(range.start), end_seconds: Math.round(range.end) };
}

/** A `VIDEO_TOO_LONG` the user can answer by picking a part of the video. Livestreams and unknown lengths can't. */
export function tooLongRangeOffer(info: Pick<ApiErrorInfo, "code" | "detail">): { durationSeconds: number; maxSeconds: number; minSeconds: number } | null {
  if (info.code !== "VIDEO_TOO_LONG") return null;
  const detail = (info.detail ?? {}) as Record<string, unknown>;
  if (detail.range_allowed === false) return null;
  const durationSeconds = Number(detail.duration_seconds);
  const maxSeconds = Number(detail.max_duration_seconds);
  if (!Number.isFinite(durationSeconds) || !Number.isFinite(maxSeconds) || durationSeconds <= maxSeconds || maxSeconds <= 0) return null;
  const minSeconds = Number(detail.min_range_seconds);
  return { durationSeconds, maxSeconds, minSeconds: Number.isFinite(minSeconds) && minSeconds > 0 ? minSeconds : DEFAULT_MIN_RANGE_SECONDS };
}
