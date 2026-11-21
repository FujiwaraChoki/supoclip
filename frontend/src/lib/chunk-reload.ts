const STORAGE_KEY = "supoclip.chunkReloadAt";
const RELOAD_COOLDOWN_MS = 60_000;

/** A script or stylesheet chunk of the app failed to load (slow or dropped connection, or a deploy). */
export function isChunkLoadError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const { name, message } = error as { name?: unknown; message?: unknown };
  if (name === "ChunkLoadError") return true;
  return typeof message === "string" && /Loading (CSS )?chunk/i.test(message);
}

/**
 * A chunk that failed to load usually loads on a fresh page, so reload once instead of leaving the
 * visitor on the error screen. The last attempt is stored in sessionStorage so a chunk that keeps
 * failing can't reload in a loop; without sessionStorage (some WebViews) we never reload.
 * Returns whether it reloaded.
 */
export function reloadOnceForChunkError(error: unknown, now: number = Date.now()): boolean {
  if (typeof window === "undefined" || !isChunkLoadError(error)) return false;
  try {
    const last = Number(window.sessionStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(last) && last > 0 && now - last < RELOAD_COOLDOWN_MS) return false;
    window.sessionStorage.setItem(STORAGE_KEY, String(now));
  } catch {
    return false;
  }
  window.location.reload();
  return true;
}
