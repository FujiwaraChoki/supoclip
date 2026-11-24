/**
 * Files someone gives the agent: videos are uploaded (they can become projects); images, PDFs and
 * text files travel with the message so Claude can see or read them. Limits match
 * `backend/src/api/routes/agent.py`.
 */
import type { Attachment } from "@/lib/agent";
import { uploadVideoFile } from "@/lib/video-upload";

export const MAX_ATTACHMENTS = 6;
const IMAGE_MAX_SIDE = 1600;
const PREVIEW_SIDE = 480;
const MAX_IMAGE_BYTES = 20 * 1024 * 1024; // before shrinking
const MAX_PDF_BYTES = 10 * 1024 * 1024;
const MAX_TEXT_BYTES = 500 * 1024;
/** Kept under the server's limit for one message's inline files (base64 grows by a third). */
export const MAX_INLINE_BYTES = 16 * 1024 * 1024;

const TEXT_EXTENSIONS = /\.(txt|md|markdown|csv|tsv|json|srt|vtt|html?|xml|ya?ml|log)$/i;
const IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);

export type AttachmentKind = "video" | "image" | "pdf" | "text";

export class AttachmentError extends Error {
  constructor(public readonly reason: "type" | "size" | "read", message?: string) {
    super(message ?? reason);
  }
}

export function attachmentKind(file: File): AttachmentKind | null {
  if (file.type.startsWith("video/")) return "video";
  if (file.type.startsWith("image/")) return "image";
  if (file.type === "application/pdf" || /\.pdf$/i.test(file.name)) return "pdf";
  if (file.type.startsWith("text/") || file.type === "application/json" || TEXT_EXTENSIONS.test(file.name)) return "text";
  return null;
}

function toBase64(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  return btoa(binary);
}

async function blobToDataUrl(blob: Blob) {
  return `data:${blob.type};base64,${toBase64(await blob.arrayBuffer())}`;
}

async function draw(bitmap: ImageBitmap, maxSide: number, type: string, quality: number): Promise<Blob> {
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new AttachmentError("read");
  if (type === "image/jpeg") {
    context.fillStyle = "#fff";
    context.fillRect(0, 0, width, height);
  }
  context.drawImage(bitmap, 0, 0, width, height);
  return new Promise((resolve, reject) => canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new AttachmentError("read"))), type, quality));
}

/** An image shrunk to a size Claude reads well, plus a small preview for the chat. */
async function prepareImage(file: File): Promise<Attachment> {
  if (file.size > MAX_IMAGE_BYTES) throw new AttachmentError("size");
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new AttachmentError("read");
  }
  try {
    // Screenshots (PNG) stay sharp as PNG while small; photos become JPEG.
    const big = Math.max(bitmap.width, bitmap.height) > IMAGE_MAX_SIDE;
    const keepPng = file.type === "image/png" && !big && file.size < 1.5 * 1024 * 1024;
    const keepAsIs = IMAGE_TYPES.has(file.type) && !big && file.size < 1.5 * 1024 * 1024 && file.type !== "image/gif";
    const blob = keepPng || keepAsIs ? file : await draw(bitmap, IMAGE_MAX_SIDE, "image/jpeg", 0.86);
    const preview = await draw(bitmap, PREVIEW_SIDE, "image/jpeg", 0.78);
    return {
      kind: "image",
      name: file.name || "image",
      media_type: blob.type || "image/jpeg",
      data: toBase64(await blob.arrayBuffer()),
      preview: await blobToDataUrl(preview),
      size: blob.size,
    };
  } finally {
    bitmap.close();
  }
}

/** Turns a dropped, pasted or picked file into something the agent can take. */
export async function prepareAttachment(file: File, onVideoUpload?: (url: string) => void): Promise<Attachment> {
  const kind = attachmentKind(file);
  if (!kind) throw new AttachmentError("type");
  if (kind === "video") {
    const url = await uploadVideoFile(file);
    onVideoUpload?.(url);
    return { kind: "video", url, name: file.name || "video" };
  }
  if (kind === "image") return prepareImage(file);
  if (kind === "pdf") {
    if (file.size > MAX_PDF_BYTES) throw new AttachmentError("size");
    return { kind: "pdf", name: file.name || "document.pdf", data: toBase64(await file.arrayBuffer()), size: file.size };
  }
  if (file.size > MAX_TEXT_BYTES) throw new AttachmentError("size");
  return { kind: "text", name: file.name || "notes.txt", text: await file.text(), size: file.size };
}

/** Bytes a ready attachment adds to the message itself (videos are uploaded separately). */
export function inlineBytes(attachment: Attachment) {
  if (attachment.kind === "image" || attachment.kind === "pdf") return attachment.size;
  if (attachment.kind === "text") return attachment.size;
  return 0;
}

export function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
