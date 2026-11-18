/**
 * Preview clips and their full-quality render.
 *
 * With preview renders on, a generation's clips are fast, small previews
 * (`render_quality: "preview"`). The first download, export or publish asks the
 * backend to render the full-quality clip and waits for it here, so the page can
 * show "Preparing full quality… 40%" instead of a frozen button. Once a clip is
 * full quality every later download is instant.
 */

export type FullRenderStatus = "ready" | "none" | "queued" | "running" | "failed";

export type FullRenderState = {
  status: FullRenderStatus;
  progress: number;
  error: string | null;
};

export type RenderQualityFields = {
  render_quality?: "preview" | "full";
  full_render?: FullRenderState;
};

export type FullRenderTrigger = "download" | "zip" | "export" | "publish" | "studio";

const POLL_MS = 1500;
// Longer than a full render plus a short queue; past it the user can try again.
const TIMEOUT_MS = 10 * 60 * 1000;

export function isPreviewClip(clip: RenderQualityFields | null | undefined): boolean {
  return clip?.render_quality === "preview";
}

export function hasPreviewClips(clips: ReadonlyArray<RenderQualityFields>): boolean {
  return clips.some(isPreviewClip);
}

type Options = {
  request?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  timeoutMs?: number;
  onProgress?: (progress: number) => void;
  readError: (response: Response) => Promise<string>;
  failedMessage: string;
  timeoutMessage: string;
};

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function poll<T>(
  start: () => Promise<Response>,
  status: () => Promise<Response>,
  read: (payload: T) => { done: boolean; failed: string | null; progress: number },
  {
    sleep = wait, now = Date.now, timeoutMs = TIMEOUT_MS,
    onProgress, readError, failedMessage, timeoutMessage,
  }: Omit<Options, "request">,
): Promise<void> {
  const deadline = now() + timeoutMs;
  let response = await start();
  for (;;) {
    if (!response.ok) throw new Error(await readError(response));
    const payload = (await response.json().catch(() => null)) as T | null;
    if (!payload) throw new Error(failedMessage);
    const { done, failed, progress } = read(payload);
    if (failed !== null) throw new Error(failed || failedMessage);
    onProgress?.(done ? 100 : Math.max(0, Math.min(99, Math.round(progress))));
    if (done) return;
    if (now() >= deadline) throw new Error(timeoutMessage);
    await sleep(POLL_MS);
    response = await status();
  }
}

type ClipStatePayload = { render_quality?: string; full_render?: Partial<FullRenderState> };

/** Render one clip at full quality (no-op when it already is). Resolves once it's ready. */
export async function ensureFullQuality({
  taskApiUrl,
  taskId,
  clipId,
  trigger,
  ...options
}: Options & { taskApiUrl: string; taskId: string; clipId: string; trigger: FullRenderTrigger }): Promise<void> {
  const request = options.request ?? fetch;
  const url = `${taskApiUrl}/${encodeURIComponent(taskId)}/clips/${encodeURIComponent(clipId)}/full-render`;
  await poll<ClipStatePayload>(
    () => request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger }),
      cache: "no-store",
    }),
    () => request(url, { cache: "no-store" }),
    (payload) => {
      const state = payload.full_render ?? {};
      return {
        done: payload.render_quality !== "preview" || state.status === "ready",
        failed: state.status === "failed" ? state.error ?? "" : null,
        progress: Number(state.progress ?? 0),
      };
    },
    options,
  );
}

type TaskStatePayload = { status?: string; progress?: number; clips?: ClipStatePayload[] };

/** Render every preview clip of a task at full quality ("Download all"). */
export async function ensureTaskFullQuality({
  taskApiUrl,
  taskId,
  ...options
}: Options & { taskApiUrl: string; taskId: string }): Promise<void> {
  const request = options.request ?? fetch;
  const url = `${taskApiUrl}/${encodeURIComponent(taskId)}/full-render`;
  await poll<TaskStatePayload>(
    () => request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger: "zip" }),
      cache: "no-store",
    }),
    () => request(url, { cache: "no-store" }),
    (payload) => {
      const failed = payload.clips?.find((clip) => clip.full_render?.status === "failed");
      return {
        done: payload.status === "ready",
        failed: failed ? failed.full_render?.error ?? "" : null,
        progress: Number(payload.progress ?? 0),
      };
    },
    options,
  );
}
