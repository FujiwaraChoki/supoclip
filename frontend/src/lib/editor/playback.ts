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

/**
 * Resumes the preview's audio and reports whether it worked. Some in-app
 * browsers reject with InvalidStateError when the audio device won't start;
 * the video should still play, and the next tap tries again.
 */
export async function resumePreviewAudio(context: AudioContext) {
  try {
    await context.resume();
    return true;
  } catch {
    return false;
  }
}
