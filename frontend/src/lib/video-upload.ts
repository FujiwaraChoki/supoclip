import { parseApiError, formatSupportMessage } from "@/lib/api-error";

export const MAX_VIDEO_UPLOAD_BYTES = 12_000_000_000;

// Only surface the font search box once the list is long enough to need it.
export const FONT_SEARCH_THRESHOLD = 8;

export type SupportedVideoSource = "youtube" | "twitch" | "kick";
const TWITCH_RESERVED_PATHS = new Set(["/videos", "/directory", "/search", "/settings"]);
const KICK_RESERVED_PATHS = new Set(["/video", "/videos", "/categories", "/browse", "/search"]);
export function validateVideoSourceUrl(value: string): { source?: SupportedVideoSource; error?: string } {
  let parsed: URL; try { parsed = new URL(value.trim()); } catch { return { error: "Enter a valid YouTube, Twitch VOD/clip, or Kick VOD/clip URL." }; }
  if (!["http:", "https:"].includes(parsed.protocol)) return { error: "Enter a valid video URL." };
  const host = parsed.hostname.toLowerCase().replace(/^www\./, ""), path = parsed.pathname.replace(/\/$/, "");
  if (["youtu.be", "youtube.com", "m.youtube.com", "music.youtube.com"].includes(host)) return extractYouTubeVideoId(value) ? { source: "youtube" } : { error: "Enter a valid YouTube video URL." };
  if (host === "clips.twitch.tv" && /^\/[A-Za-z0-9_-]+$/.test(path)) return { source: "twitch" };
  if (["twitch.tv", "m.twitch.tv", "go.twitch.tv"].includes(host)) {
    if (/^\/videos\/\d+$/.test(path) || /^\/[A-Za-z0-9_]+\/clip\/[A-Za-z0-9_-]+$/.test(path)) return { source: "twitch" };
    // Mirrors the backend: only a bare channel page is a live stream; listing
    // and directory pages get the unsupported-URL message.
    if (/^\/[A-Za-z0-9_]+$/.test(path) && !TWITCH_RESERVED_PATHS.has(path.toLowerCase())) return { error: "Live streams aren't supported. Use a Twitch VOD or clip URL instead." };
    return { error: "Enter a valid Twitch VOD or clip URL." };
  }
  if (host === "kick.com") {
    const vod = /^(?:\/[A-Za-z0-9_-]+\/videos|\/video)\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(path);
    const clip = /^\/[A-Za-z0-9_-]+\/clips\/clip_[A-Za-z0-9_-]+$/.test(path) || (/^\/[A-Za-z0-9_-]+$/.test(path) && /^clip_[A-Za-z0-9_-]+$/.test(parsed.searchParams.get("clip") || ""));
    if (vod || clip) return { source: "kick" };
    if (/^\/[A-Za-z0-9_-]+$/.test(path) && !KICK_RESERVED_PATHS.has(path.toLowerCase())) return { error: "Live streams aren't supported. Use a Kick VOD or clip URL instead." };
    return { error: "Enter a valid Kick VOD or clip URL." };
  }
  return { error: "Enter a YouTube, Twitch VOD/clip, or Kick VOD/clip URL." };
}

type DirectUploadAuthorization = {
  directUpload: true;
  uploadUrl: string;
  headers: Record<string, string>;
};

type ProxyUploadAuthorization = {
  directUpload: false;
  reason: "signed_backend_auth_required";
};

type S3UploadAuthorization = {
  storage: "s3";
  video_path: string;
  uploadId: string;
  partSize: number;
  parts: { partNumber: number; url: string }[];
};

type UploadAuthorization = DirectUploadAuthorization | ProxyUploadAuthorization | S3UploadAuthorization;

const extractYouTubeVideoId = (value: string): string | null => {
  const input = value.trim();
  if (!input) return null;

  try {
    const parsed = new URL(input);
    const host = parsed.hostname.replace(/^www\./, "");

    if (host === "youtu.be") {
      const id = parsed.pathname.split("/").filter(Boolean)[0];
      return id && id.length === 11 ? id : null;
    }

    if (host === "youtube.com" || host === "m.youtube.com" || host === "music.youtube.com") {
      const fromSearch = parsed.searchParams.get("v");
      if (fromSearch && fromSearch.length === 11) {
        return fromSearch;
      }

      const pathParts = parsed.pathname.split("/").filter(Boolean);
      const embedId = pathParts[0] === "embed" ? pathParts[1] : null;
      if (embedId && embedId.length === 11) {
        return embedId;
      }
    }
  } catch {
    return null;
  }

  return null;
};

export const getYouTubeThumbnailUrl = (value: string): string | null => {
  const videoId = extractYouTubeVideoId(value);
  return videoId ? `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg` : null;
};

async function requestUploadAuthorization(file: File): Promise<UploadAuthorization> {
  const response = await fetch("/api/upload/authorization", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, size: file.size, contentType: file.type }),
    cache: "no-store",
  });

  if (!response.ok) {
    const uploadError = await parseApiError(
      response,
      `Upload authorization error: ${response.status}`,
    );
    throw new Error(formatSupportMessage(uploadError));
  }

  return response.json() as Promise<UploadAuthorization>;
}

export async function uploadVideoFile(file: File): Promise<string> {
  if (file.size > MAX_VIDEO_UPLOAD_BYTES) {
    throw new Error("Uploaded file is too large. Please upload a video under 12 GB.");
  }

  const uploadAuthorization = await requestUploadAuthorization(file);
  if ("storage" in uploadAuthorization) {
    return uploadVideoFileToS3(file, uploadAuthorization);
  }
  if (!uploadAuthorization.directUpload) {
    return uploadVideoFileViaProxy(file);
  }

  const formData = new FormData();
  formData.append("video", file);

  const uploadResponse = await fetch(uploadAuthorization.uploadUrl, {
    method: "POST",
    headers: uploadAuthorization.headers,
    body: formData,
  });

  if (!uploadResponse.ok) {
    const fallbackMessage =
      uploadResponse.status === 413
        ? "Uploaded file is too large. Please upload a video under 12 GB."
        : `Upload error: ${uploadResponse.status}`;
    const uploadError = await parseApiError(uploadResponse, fallbackMessage);
    throw new Error(formatSupportMessage(uploadError));
  }

  const uploadResult = await uploadResponse.json();
  if (typeof uploadResult.video_path !== "string" || !uploadResult.video_path) {
    throw new Error("Upload finished without a video path. Please try again.");
  }

  return uploadResult.video_path;
}

async function uploadVideoFileToS3(file: File, authorization: S3UploadAuthorization): Promise<string> {
  const completedParts: { partNumber: number; etag: string }[] = [];
  const controller = new AbortController();
  const payload = { video_path: authorization.video_path, uploadId: authorization.uploadId };
  let nextPart = 0;
  let failure: unknown;
  const workers = Array.from({ length: Math.min(3, authorization.parts.length) }, async () => {
    while (nextPart < authorization.parts.length && !controller.signal.aborted) {
      const part = authorization.parts[nextPart++];
      try {
        const start = (part.partNumber - 1) * authorization.partSize;
        let partUrl = part.url;
        let response: Response | undefined;
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            response = await fetch(partUrl, {
              method: "PUT",
              body: file.slice(start, Math.min(file.size, start + authorization.partSize)),
              signal: controller.signal,
            });
            if (response.ok) break;
            if (response.status === 403 && attempt < 2) {
              // Refresh with current credentials without discarding completed
              // parts when an upload lasts longer than the signed URL/session.
              const refreshed = await fetch("/api/upload/parts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ...payload, partNumbers: [part.partNumber] }),
                signal: controller.signal,
              });
              if (!refreshed.ok) throw new Error("Video upload authorization could not be refreshed.");
              const result = await refreshed.json();
              const freshPart = result.parts?.find((item: { partNumber: number; url: string }) => item.partNumber === part.partNumber);
              if (!freshPart?.url) throw new Error("Video upload authorization could not be refreshed.");
              partUrl = freshPart.url;
              continue;
            }
            if (response.status < 500) throw new Error("Video upload failed. Please try again.");
          } catch (error) {
            if (controller.signal.aborted || attempt === 2) throw error;
          }
        }
        const etag = response?.headers.get("ETag");
        if (!response?.ok || !etag) {
          throw new Error("Video upload failed. Please try again.");
        }
        completedParts.push({ partNumber: part.partNumber, etag });
      } catch (error) {
        failure ??= error;
        controller.abort();
      }
    }
  });
  await Promise.all(workers);
  try {
    if (failure) throw failure;
    let response: Response | undefined;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        response = await fetch("/api/upload/complete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...payload, parts: completedParts.sort((a, b) => a.partNumber - b.partNumber) }),
        });
        if (response.ok || response.status < 500) break;
      } catch (error) {
        if (attempt === 2) throw error;
      }
    }
    if (!response?.ok) {
      if (!response) throw new Error("Video upload could not be completed.");
      throw new Error(formatSupportMessage(await parseApiError(response, "Video upload could not be completed.")));
    }
    const result = await response.json();
    if (typeof result.video_path !== "string" || !result.video_path) {
      throw new Error("Upload finished without a video path. Please try again.");
    }
    return result.video_path;
  } catch (error) {
    await fetch("/api/upload/abort", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => undefined);
    throw error;
  }
}

export async function uploadVideoFileViaProxy(file: File): Promise<string> {
  const formData = new FormData();
  formData.append("video", file);

  const uploadResponse = await fetch("/api/upload", {
    method: "POST",
    body: formData,
  });

  if (!uploadResponse.ok) {
    const fallbackMessage =
      uploadResponse.status === 413
        ? "Uploaded file is too large. Please upload a video under 12 GB."
        : `Upload error: ${uploadResponse.status}`;
    const uploadError = await parseApiError(uploadResponse, fallbackMessage);
    throw new Error(formatSupportMessage(uploadError));
  }

  const uploadResult = await uploadResponse.json();
  if (typeof uploadResult.video_path !== "string" || !uploadResult.video_path) {
    throw new Error("Upload finished without a video path. Please try again.");
  }

  return uploadResult.video_path;
}
