"use client";
import { useEffect, useRef, useState } from "react";
import type { EditorState } from "@/lib/editor/document";
import { cn } from "@/lib/utils";
import { editorRequest } from "./use-editor";
import type { ClipSummary } from "./export-panel";

/** What the picker knows about a clip's editor preview; `loading` until the server says. */
export type ThumbnailStatus = EditorState["status"] | "loading" | "unknown";

/**
 * The editor filmstrip only exists once a clip's preview is prepared, and
 * preparation runs in the background. Asking for it earlier is a 404.
 */
export function thumbnailUrl(endpoint: string, status: ThumbnailStatus) {
  return status === "ready" ? `${endpoint}/media/thumbnails.jpg` : null;
}

export function ClipThumbnail({ endpoint, status, className }: {
  endpoint: string;
  status: ThumbnailStatus;
  className?: string;
}) {
  const url = thumbnailUrl(endpoint, status);
  return (
    <span
      aria-hidden
      data-thumbnail={status}
      className={cn(
        "shrink-0 bg-muted",
        (status === "loading" || status === "preparing") && "animate-pulse",
        className,
      )}
      style={url ? { backgroundImage: `url(${url})`, backgroundSize: "auto 100%" } : undefined}
    />
  );
}

const POLL_MS = 3000;
const key = (clip: Pick<ClipSummary, "id" | "filename">) => `${clip.id}:${clip.filename}`;

/**
 * Preview status of the other clips, read only while the picker is open.
 * Each clip is prepared on its own, so being in the same task says nothing
 * about whether its filmstrip exists. Reading the state never starts a
 * preparation; clips still preparing are polled until they are ready.
 */
export function useClipThumbnailStatus(taskId: string, clips: ClipSummary[], currentId: string, open: boolean) {
  const [statuses, setStatuses] = useState<Record<string, ThumbnailStatus>>({});
  // A ready preview stays ready for that file (a new file is a new key), so it is read once.
  const ready = useRef(new Set<string>());
  const others = JSON.stringify(
    clips.filter((c) => c.id !== currentId).map((c) => ({ id: c.id, filename: c.filename })),
  );
  useEffect(() => {
    if (!open) return;
    let stopped = false;
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const check = async (clip: Pick<ClipSummary, "id" | "filename">) => {
      let status: ThumbnailStatus;
      try {
        status = (await editorRequest<EditorState>(`/api/tasks/${taskId}/clips/${clip.id}/editor`)).status;
      } catch {
        status = "unknown";
      }
      if (stopped) return;
      if (status === "ready") ready.current.add(key(clip));
      setStatuses((current) => ({ ...current, [key(clip)]: status }));
      if (status === "preparing") {
        const timer = setTimeout(() => {
          timers.delete(timer);
          void check(clip);
        }, POLL_MS);
        timers.add(timer);
      }
    };
    for (const clip of JSON.parse(others) as Pick<ClipSummary, "id" | "filename">[])
      if (!ready.current.has(key(clip))) void check(clip);
    return () => {
      stopped = true;
      timers.forEach(clearTimeout);
    };
  }, [open, others, taskId]);
  return (clip: ClipSummary): ThumbnailStatus => statuses[key(clip)] ?? "loading";
}
