// Following a clip edit that runs on the video worker (`GET /tasks/{id}/clip-jobs/{job}`).

export const CLIP_JOB_POLL_MS = 2000;
/** Polls in a row that may fail (network error, 5xx) before following stops. */
export const CLIP_JOB_MAX_FAILED_POLLS = 5;

/**
 * How following a job ended. `gone`: the record expired (the job finished long ago). `denied`: 401/403.
 * `unreachable`: its status could not be read; the job itself may still be running.
 */
export type ClipJobEnd =
  | { status: "done" | "gone" | "denied" | "unreachable" }
  | { status: "failed"; code?: string; message?: string };

/**
 * Polls a clip job until it ends and returns a function that stops. A job that stays `queued` or `running` is
 * polled for as long as it takes.
 */
export function followClipJob(url: string, { onProgress, onEnd, intervalMs = CLIP_JOB_POLL_MS }: {
  onProgress: (progress: number) => void;
  onEnd: (end: ClipJobEnd) => void;
  intervalMs?: number;
}): () => void {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;
  const poll = async () => {
    let end: ClipJobEnd | null = null;
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (cancelled) return;
      if (response.status === 404) end = { status: "gone" };
      else if (response.status === 401 || response.status === 403) end = { status: "denied" };
      else if (response.ok) {
        const state = (await response.json()) as { status?: string; progress?: number; error?: { code?: string; message?: string } };
        if (cancelled) return;
        failures = 0;
        if (state.status === "done") end = { status: "done" };
        else if (state.status === "failed") end = { status: "failed", code: state.error?.code, message: state.error?.message };
        else onProgress(Number(state.progress) || 0);
      } else {
        failures += 1;
      }
    } catch {
      if (cancelled) return;
      failures += 1;
    }
    if (!end && failures >= CLIP_JOB_MAX_FAILED_POLLS) end = { status: "unreachable" };
    if (end) onEnd(end);
    else timer = setTimeout(() => void poll(), intervalMs);
  };
  timer = setTimeout(() => void poll(), intervalMs);
  return () => { cancelled = true; clearTimeout(timer); };
}
