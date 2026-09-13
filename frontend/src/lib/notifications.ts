"use client";

/** Browser Notification API wrapper for batch-complete alerts — this is a
 * web app, not a native desktop app, so "desktop notification" means the
 * browser's own Notification API. Never requests permission unconditionally;
 * only call requestNotificationPermission() from an explicit user action
 * (an "Enable notifications" toggle), matching normal browser UX norms. */

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function notificationsEnabled(): boolean {
  return notificationsSupported() && Notification.permission === "granted";
}

export async function requestNotificationPermission(): Promise<boolean> {
  if (!notificationsSupported()) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  try {
    const result = await Notification.requestPermission();
    return result === "granted";
  } catch {
    return false;
  }
}

export function notifyBatchComplete(summary: { succeeded: number; failed: number; total: number }): void {
  if (!notificationsEnabled()) return;
  try {
    const body =
      summary.failed > 0
        ? `${summary.succeeded}/${summary.total} succeeded, ${summary.failed} failed`
        : `All ${summary.total} videos processed successfully`;
    new Notification("Batch processing complete", { body });
  } catch {
    // Notification construction can throw in some contexts (e.g. no
    // ServiceWorker on some mobile browsers) — never let this break the app.
  }
}
