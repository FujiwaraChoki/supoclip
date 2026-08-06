import { formatSupportMessage, parseApiError } from "@/lib/api-error";

import type { EditorAsset, EditorProject } from "./types";

interface RawEditorAsset {
  id: string;
  name?: string;
  filename?: string;
  kind: EditorAsset["kind"];
  mime_type?: string;
  mimeType?: string;
  size_bytes?: number;
  sizeBytes?: number;
  duration?: number;
  width?: number | null;
  height?: number | null;
  url: string;
}

export interface EditorBootstrap {
  project: EditorProject | null;
  assets: EditorAsset[];
  version: number;
}

export class EditorVersionConflictError extends Error {
  constructor() {
    super("This project was changed in another tab. Refresh before saving again.");
    this.name = "EditorVersionConflictError";
  }
}

function apiAssetUrl(url: string) {
  if (url.startsWith("/api/")) return url;
  if (url.startsWith("/tasks/")) return `/api${url}`;
  return url;
}

function mapAsset(asset: RawEditorAsset): EditorAsset {
  return {
    id: asset.id,
    name: asset.name || asset.filename || "Untitled asset",
    kind: asset.kind,
    source: "uploaded",
    url: apiAssetUrl(asset.url),
    duration: Number(asset.duration || 0),
    width: asset.width ?? undefined,
    height: asset.height ?? undefined,
    sizeBytes: asset.size_bytes ?? asset.sizeBytes,
    mimeType: asset.mime_type ?? asset.mimeType,
  };
}

async function editorError(response: Response, fallback: string) {
  return formatSupportMessage(await parseApiError(response, fallback));
}

export async function loadEditorProject(taskId: string): Promise<EditorBootstrap> {
  const response = await fetch(`/api/tasks/${taskId}/editor`, { cache: "no-store" });
  if (!response.ok) throw new Error(await editorError(response, "Failed to load the editor project"));
  const data = await response.json() as {
    project?: EditorProject | null;
    assets?: RawEditorAsset[];
    version?: number;
  };
  return {
    project: data.project ?? null,
    assets: (data.assets ?? []).map(mapAsset),
    version: Number(data.version ?? data.project?.version ?? 0),
  };
}

export async function saveEditorProject(
  taskId: string,
  project: EditorProject,
  expectedVersion: number,
) {
  const response = await fetch(`/api/tasks/${taskId}/editor`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, expected_version: expectedVersion }),
  });
  if (response.status === 409) throw new EditorVersionConflictError();
  if (!response.ok) throw new Error(await editorError(response, "Failed to save the project"));
  const data = await response.json() as { version?: number; project?: EditorProject };
  return {
    version: Number(data.version ?? data.project?.version ?? expectedVersion + 1),
    project: data.project,
  };
}

export function uploadEditorAsset(
  taskId: string,
  file: File,
  onProgress?: (progress: number) => void,
): Promise<EditorAsset> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api/tasks/${taskId}/editor/assets`);
    request.responseType = "json";
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress?.(Math.round((event.loaded / event.total) * 100));
    });
    request.addEventListener("load", () => {
      if (request.status >= 200 && request.status < 300) {
        const data = request.response as { asset?: RawEditorAsset } | RawEditorAsset;
        const rawAsset = "asset" in data && data.asset ? data.asset : data as RawEditorAsset;
        resolve(mapAsset(rawAsset));
        return;
      }
      const detail = typeof request.response?.detail === "string"
        ? request.response.detail
        : `Upload failed (${request.status})`;
      reject(new Error(detail));
    });
    request.addEventListener("error", () => reject(new Error("Upload failed. Check your connection and try again.")));
    const formData = new FormData();
    formData.set("file", file);
    request.send(formData);
  });
}

export async function deleteEditorAsset(
  taskId: string,
  assetId: string,
  expectedVersion?: number,
) {
  const query = expectedVersion === undefined
    ? ""
    : `?remove_references=true&expected_version=${encodeURIComponent(expectedVersion)}`;
  const response = await fetch(`/api/tasks/${taskId}/editor/assets/${assetId}${query}`, { method: "DELETE" });
  if (response.status === 409) throw new EditorVersionConflictError();
  if (!response.ok) throw new Error(await editorError(response, "Failed to delete the asset"));
  return await response.json() as {
    deleted: boolean;
    id: string;
    project?: EditorProject | null;
    version?: number;
  };
}
