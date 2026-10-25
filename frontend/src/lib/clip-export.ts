export type ClipExportStatus = "queued" | "running" | "succeeded" | "failed";

export type ClipExportJob = {
  id: string;
  status: ClipExportStatus;
  progress: number;
  preset: string;
  error: string | null;
  download_url: string | null;
};

const ACTIVE_STATUSES = new Set<ClipExportStatus>(["queued", "running"]);

/** 1s, 1.5s, 2.3s, 3.4s, then 5s. */
export function clipExportPollDelayMs(attempt: number): number {
  return Math.min(5000, Math.round(1000 * 1.5 ** attempt));
}

export function clipExportFilePath(
  taskApiUrl: string,
  taskId: string,
  clipId: string,
  exportId: string,
): string {
  return `${taskApiUrl}/${taskId}/clips/${clipId}/exports/${exportId}/file`;
}

export function triggerFileDownload(href: string, filename: string) {
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function isClipExportJob(value: unknown): value is ClipExportJob {
  if (!value || typeof value !== "object") return false;
  const job = value as Partial<ClipExportJob>;
  return typeof job.id === "string" && typeof job.status === "string";
}

async function readJob(
  response: Response,
  readError: (response: Response) => Promise<string>,
  fallbackMessage: string,
): Promise<ClipExportJob> {
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(fallbackMessage);
  }
  if (!isClipExportJob(payload)) {
    throw new Error(fallbackMessage);
  }
  return {
    id: payload.id,
    status: payload.status,
    progress: typeof payload.progress === "number" ? payload.progress : 0,
    preset: typeof payload.preset === "string" ? payload.preset : "",
    error: typeof payload.error === "string" ? payload.error : null,
    download_url: typeof payload.download_url === "string" ? payload.download_url : null,
  };
}

/**
 * Start a preset export and poll until it succeeds or fails.
 * The download itself is left to the caller so a browser can follow the file
 * route (including a redirect to object storage) without reading the bytes.
 */
export async function pollClipExport({
  start,
  status,
  readError,
  fallbackMessage,
  timeoutMessage,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  now = () => Date.now(),
  maxWaitMs = 20 * 60 * 1000,
}: {
  start: () => Promise<Response>;
  status: (exportId: string) => Promise<Response>;
  readError: (response: Response) => Promise<string>;
  fallbackMessage: string;
  timeoutMessage: string;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  maxWaitMs?: number;
}): Promise<ClipExportJob> {
  const startedAt = now();
  let job = await readJob(await start(), readError, fallbackMessage);
  let attempt = 0;
  while (ACTIVE_STATUSES.has(job.status)) {
    if (now() - startedAt > maxWaitMs) {
      throw new Error(timeoutMessage);
    }
    await sleep(clipExportPollDelayMs(attempt));
    attempt += 1;
    job = await readJob(await status(job.id), readError, fallbackMessage);
  }
  if (job.status === "failed") {
    throw new Error(job.error || fallbackMessage);
  }
  if (job.status !== "succeeded") {
    throw new Error(fallbackMessage);
  }
  return job;
}
