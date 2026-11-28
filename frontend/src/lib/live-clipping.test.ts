import { describe, expect, it } from "vitest";

import {
  defaultLiveChoice,
  liveMinutesNow,
  liveOfferFromError,
  liveSourceFields,
  liveWindowOptions,
  type LiveOffer,
} from "./live-clipping";

const detail = {
  code: "LIVE_STREAM_DETECTED",
  message: "SomeStreamer is live right now.",
  live: { platform: "twitch", channel: "SomeStreamer", title: "Sunday stream", elapsed_seconds: 5400 },
  can_clip_live: true,
  max_window_seconds: 10800,
  default_window_seconds: 1800,
  follow_interval_seconds: 1800,
};

function offer(overrides: Partial<LiveOffer> = {}): LiveOffer {
  return { ...(liveOfferFromError({ code: "LIVE_STREAM_DETECTED", detail }) as LiveOffer), ...overrides };
}

describe("liveOfferFromError", () => {
  it("reads the stream and what this account may do", () => {
    expect(offer()).toEqual({
      platform: "twitch",
      channel: "SomeStreamer",
      title: "Sunday stream",
      elapsedSeconds: 5400,
      canClipLive: true,
      maxWindowSeconds: 10800,
      defaultWindowSeconds: 1800,
      followIntervalSeconds: 1800,
    });
  });

  it("ignores other errors and malformed details", () => {
    expect(liveOfferFromError({ code: "VIDEO_TOO_LONG", detail })).toBeNull();
    expect(liveOfferFromError({ code: "LIVE_STREAM_DETECTED", detail: { live: { platform: "vimeo" } } })).toBeNull();
    expect(liveOfferFromError({ code: "LIVE_STREAM_DETECTED", detail: undefined })).toBeNull();
  });
});

describe("liveWindowOptions", () => {
  it("offers every quick pick shorter than the stream", () => {
    expect(liveWindowOptions(offer({ elapsedSeconds: 3 * 3600 }))).toEqual([
      { seconds: 900, wholeStream: false },
      { seconds: 1800, wholeStream: false },
      { seconds: 3600, wholeStream: false },
      { seconds: 7200, wholeStream: false },
    ]);
  });

  it("ends with 'everything so far' once a pick reaches the start of the stream", () => {
    expect(liveWindowOptions(offer({ elapsedSeconds: 40 * 60 }))).toEqual([
      { seconds: 900, wholeStream: false },
      { seconds: 1800, wholeStream: false },
      { seconds: 3600, wholeStream: true },
    ]);
  });

  it("never offers more than the plan allows per video", () => {
    expect(liveWindowOptions(offer({ elapsedSeconds: 5 * 3600, maxWindowSeconds: 3600 })).map((o) => o.seconds)).toEqual([900, 1800, 3600]);
  });

  it("has nothing to offer for a stream younger than a minute", () => {
    expect(liveWindowOptions(offer({ elapsedSeconds: 30 }))).toEqual([]);
  });
});

describe("choices and request fields", () => {
  it("defaults to the server's window, or the longest pick that fits", () => {
    expect(defaultLiveChoice(offer())).toEqual({ windowSeconds: 1800, follow: false });
    expect(defaultLiveChoice(offer({ elapsedSeconds: 20 * 60 }))).toEqual({ windowSeconds: 1800, follow: false });
    expect(defaultLiveChoice(offer({ elapsedSeconds: 10 * 60 }))).toEqual({ windowSeconds: 900, follow: false });
  });

  it("counts only the minutes that exist now", () => {
    expect(liveMinutesNow(offer(), { windowSeconds: 1800, follow: false })).toBe(30);
    expect(liveMinutesNow(offer({ elapsedSeconds: 600 }), { windowSeconds: 1800, follow: true })).toBe(10);
  });

  it("puts the choice in source.live, and nothing without one", () => {
    expect(liveSourceFields({ windowSeconds: 3600, follow: true })).toEqual({ live: { window_seconds: 3600, follow: true } });
    expect(liveSourceFields(null)).toEqual({});
  });
});
