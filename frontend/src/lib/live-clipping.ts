/**
 * Clipping a stream that is still live (Creator plan). The backend contract is in
 * backend/src/live_source.py and docs/live-clipping.md:
 *
 * - `POST /tasks/` with a live link and no `source.live` answers 422 `LIVE_STREAM_DETECTED`
 *   with the stream (channel, elapsed time) and whether this account may clip it.
 * - Sending `source.live = { window_seconds, follow }` clips the newest part. `follow` keeps
 *   clipping while the stream lasts: a new part every `follow_interval_seconds`.
 */
import type { ApiErrorInfo } from "@/lib/api-error";

export type LivePlatform = "youtube" | "twitch" | "kick";

export type LiveOffer = {
  platform: LivePlatform;
  channel: string;
  title: string | null;
  elapsedSeconds: number;
  canClipLive: boolean;
  maxWindowSeconds: number;
  defaultWindowSeconds: number;
  followIntervalSeconds: number;
};

export type LiveChoice = { windowSeconds: number; follow: boolean };

export const LIVE_STREAM_DETECTED = "LIVE_STREAM_DETECTED";
export const LIVE_REQUIRES_CREATOR = "LIVE_REQUIRES_CREATOR";
/** Quick picks for "the newest N minutes". */
export const LIVE_WINDOW_OPTIONS = [15 * 60, 30 * 60, 60 * 60, 120 * 60] as const;
const MIN_LIVE_SECONDS = 60;

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function positive(value: unknown, fallback: number): number {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

/** The offer in a `LIVE_STREAM_DETECTED` error, or null for any other error. */
export function liveOfferFromError(info: Pick<ApiErrorInfo, "code" | "detail">): LiveOffer | null {
  if (info.code !== LIVE_STREAM_DETECTED) return null;
  const detail = record(info.detail);
  const live = record(detail?.live);
  if (!detail || !live) return null;
  const platform = live.platform;
  if (platform !== "youtube" && platform !== "twitch" && platform !== "kick") return null;
  return {
    platform,
    channel: typeof live.channel === "string" ? live.channel : "",
    title: typeof live.title === "string" && live.title.trim() ? live.title : null,
    elapsedSeconds: positive(live.elapsed_seconds, 0),
    canClipLive: detail.can_clip_live === true,
    maxWindowSeconds: positive(detail.max_window_seconds, 6 * 3600),
    defaultWindowSeconds: positive(detail.default_window_seconds, 30 * 60),
    followIntervalSeconds: positive(detail.follow_interval_seconds, 30 * 60),
  };
}

/** The quick picks that make sense for this stream: never longer than the plan allows, and
 *  at most one that reaches back to the start (shown as "everything so far"). */
export function liveWindowOptions(offer: LiveOffer): { seconds: number; wholeStream: boolean }[] {
  const options: { seconds: number; wholeStream: boolean }[] = [];
  for (const seconds of LIVE_WINDOW_OPTIONS) {
    if (seconds > offer.maxWindowSeconds) break;
    if (seconds >= offer.elapsedSeconds) {
      if (offer.elapsedSeconds >= MIN_LIVE_SECONDS && offer.elapsedSeconds <= offer.maxWindowSeconds) {
        options.push({ seconds, wholeStream: true });
      }
      break;
    }
    options.push({ seconds, wholeStream: false });
  }
  return options;
}

export function defaultLiveChoice(offer: LiveOffer): LiveChoice {
  const options = liveWindowOptions(offer);
  const preferred = options.find((option) => option.seconds === offer.defaultWindowSeconds) ?? options[options.length - 1];
  return { windowSeconds: preferred?.seconds ?? offer.defaultWindowSeconds, follow: false };
}

/** Minutes this request uses now: the part that exists, not the whole window asked for. */
export function liveMinutesNow(offer: LiveOffer, choice: LiveChoice): number {
  const seconds = Math.min(choice.windowSeconds, offer.elapsedSeconds || choice.windowSeconds, offer.maxWindowSeconds);
  return Math.max(1, Math.ceil(seconds / 60));
}

/** What goes in `source.live`. */
export function liveSourceFields(choice: LiveChoice | null): { live?: { window_seconds: number; follow: boolean } } {
  return choice ? { live: { window_seconds: choice.windowSeconds, follow: choice.follow } } : {};
}
