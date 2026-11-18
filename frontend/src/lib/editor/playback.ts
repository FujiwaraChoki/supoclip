/**
 * True when play() was cut short by pause(), a new source or a seek. The
 * browser rejects the pending promise with AbortError; the media is fine.
 */
export function isPlayAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

/** Starts playback and reports real failures, ignoring interrupted starts. */
export function playPreview(video: HTMLVideoElement, onFailure: () => void) {
  return video.play().catch((error: unknown) => {
    if (isPlayAbortError(error)) return;
    onFailure();
  });
}
