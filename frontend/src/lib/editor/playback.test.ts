import { describe, expect, it, vi } from "vitest";
import { isPlayAbortError, playPreview } from "./playback";

function videoRejecting(error: unknown) {
  return { play: vi.fn(() => Promise.reject(error)) } as unknown as HTMLVideoElement;
}

describe("isPlayAbortError", () => {
  it("matches only an AbortError DOMException", () => {
    expect(
      isPlayAbortError(
        new DOMException("The play() request was interrupted by a call to pause().", "AbortError"),
      ),
    ).toBe(true);
    expect(isPlayAbortError(new DOMException("No supported source", "NotSupportedError"))).toBe(false);
    expect(isPlayAbortError(new Error("AbortError"))).toBe(false);
  });
});

describe("playPreview", () => {
  it("handles a play() interrupted by pause() without reporting a failure", async () => {
    const onFailure = vi.fn();
    const video = videoRejecting(
      new DOMException("The play() request was interrupted by a call to pause().", "AbortError"),
    );
    await expect(playPreview(video, onFailure)).resolves.toBeUndefined();
    expect(onFailure).not.toHaveBeenCalled();
  });

  it("still reports a play() that fails for another reason", async () => {
    const onFailure = vi.fn();
    const video = videoRejecting(new DOMException("No supported source", "NotSupportedError"));
    await expect(playPreview(video, onFailure)).resolves.toBeUndefined();
    expect(onFailure).toHaveBeenCalledOnce();
  });

  it("does not report a failure when playback starts", async () => {
    const onFailure = vi.fn();
    const video = { play: vi.fn(() => Promise.resolve()) } as unknown as HTMLVideoElement;
    await playPreview(video, onFailure);
    expect(onFailure).not.toHaveBeenCalled();
  });
});
