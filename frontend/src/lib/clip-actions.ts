import { formatSupportMessage, parseApiError } from "@/lib/api-error";

export const EXPORT_PRESETS = [
  { id: "tiktok", label: "TikTok", width: 1080, height: 1920, bitrate: 10_000_000 },
  { id: "reels", label: "Reels", width: 1080, height: 1920, bitrate: 12_000_000 },
  { id: "shorts", label: "Shorts", width: 1080, height: 1920, bitrate: 10_000_000 },
] as const;

export function getClipUrl(videoUrl: string, version?: string) {
  const url = videoUrl.startsWith("/api/") ? videoUrl : `/api${videoUrl}`;
  return version ? `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(version)}` : url;
}

export async function requestAction(url: string, method: string, body?: unknown) {
  const response = await fetch(url, {
    method,
    ...(body === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  });
  if (!response.ok) {
    throw new Error(formatSupportMessage(await parseApiError(response, "Could not save changes. Please try again.")));
  }
  return response;
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser time to begin the download before releasing the URL.
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
