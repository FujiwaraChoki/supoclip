"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Loader2, RefreshCw, Scissors } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { loadEditorProject } from "@/features/editor/editor-api";
import { EditorShell } from "@/features/editor/editor-shell";
import {
  loadCompatibleProject,
} from "@/features/editor/editor-utils";
import type {
  EditorAsset,
  EditorProject,
} from "@/features/editor/types";

interface TaskDetails {
  id: string;
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
}

interface Clip {
  id: string;
  filename: string;
  clip_order: number;
  duration: number;
  text: string;
  video_url: string;
}

interface EditorData {
  task: TaskDetails;
  assets: EditorAsset[];
  project: EditorProject;
  version: number;
  transcripts: Record<string, string>;
}

function clipUrl(videoUrl: string) {
  return videoUrl.startsWith("/api/") ? videoUrl : `/api${videoUrl}`;
}

function clipAsset(clip: Clip): EditorAsset {
  return {
    id: `clip:${clip.id}`,
    name: clip.filename.replace(/\.mp4$/i, "") || `Clip ${clip.clip_order}`,
    kind: "video",
    source: "generated",
    url: clipUrl(clip.video_url),
    duration: Number(clip.duration || 0),
    mimeType: "video/mp4",
  };
}

async function responseError(response: Response, fallback: string) {
  return formatSupportMessage(await parseApiError(response, fallback));
}

export default function TaskEditPage() {
  const params = useParams<{ id: string }>();
  const taskId = useMemo(() => Array.isArray(params.id) ? params.id[0] : params.id, [params.id]);
  const [data, setData] = useState<EditorData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const load = useCallback(async () => {
    if (!taskId) return;
    setIsLoading(true);
    setError(null);

    try {
      const [taskResponse, editorBootstrap] = await Promise.all([
        fetch(`/api/tasks/${taskId}`, { cache: "no-store" }),
        loadEditorProject(taskId),
      ]);
      if (!taskResponse.ok) {
        throw new Error(await responseError(taskResponse, "Failed to load the task"));
      }

      const task = await taskResponse.json() as TaskDetails;
      let clips: Clip[] = [];
      if (task.status === "completed") {
        const clipsResponse = await fetch(`/api/tasks/${taskId}/clips`, { cache: "no-store" });
        if (!clipsResponse.ok) {
          throw new Error(await responseError(clipsResponse, "Failed to load generated clips"));
        }
        const clipsData = await clipsResponse.json() as { clips?: Clip[] };
        clips = [...(clipsData.clips ?? [])].sort((a, b) => a.clip_order - b.clip_order);
      }

      const generatedAssets = clips.map(clipAsset);
      const allAssets = [...generatedAssets, ...editorBootstrap.assets];
      const project = loadCompatibleProject(
        editorBootstrap.project,
        taskId,
        task.source_title || "Untitled project",
        generatedAssets[0],
      );
      const transcripts = Object.fromEntries(
        clips.map((clip) => [`clip:${clip.id}`, clip.text || ""]),
      );

      setData({
        task,
        assets: allAssets,
        project,
        version: editorBootstrap.version,
        transcripts,
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load the editor");
    } finally {
      setIsLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (isLoading) {
    return (
      <div className="flex h-dvh min-h-[600px] items-center justify-center bg-[#0f0f0f] text-white">
        <div className="text-center">
          <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-2xl bg-white/10 text-zinc-300">
            <Loader2 className="size-5 animate-spin" />
          </div>
          <p className="text-sm font-semibold">Opening your studio</p>
          <p className="mt-1 text-xs text-zinc-600">Loading media and project edits…</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0f0f0f] p-6 text-white">
        <div className="w-full max-w-md space-y-4 rounded-2xl border border-white/10 bg-white/5 p-6">
          <div className="flex size-10 items-center justify-center rounded-xl bg-red-500/10 text-red-300">
            <Scissors className="size-4" />
          </div>
          <div>
            <h1 className="text-lg font-semibold">The editor could not open</h1>
            <p className="mt-1 text-sm leading-6 text-zinc-400">{error || "Task not found."}</p>
          </div>
          <div className="flex gap-2">
            <Button type="button" className="bg-white text-black hover:bg-zinc-200" onClick={() => void load()}>
              <RefreshCw />
              Try again
            </Button>
            <Button type="button" variant="ghost" className="border border-white/10 text-zinc-300 hover:bg-white/10 hover:text-white" asChild>
              <Link href={`/tasks/${taskId}`}><ArrowLeft /> Back to task</Link>
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (data.task.status !== "completed") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0f0f0f] p-6 text-white">
        <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-white/5 p-8 text-center">
          <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-2xl bg-white/10 text-zinc-300">
            <Loader2 className="size-5 animate-spin" />
          </div>
          <h1 className="text-xl font-semibold">Your clips are still processing</h1>
          <p className="mt-2 text-sm text-zinc-400">The studio unlocks as soon as processing finishes. Current status: {data.task.status}.</p>
          <Button type="button" variant="ghost" className="mt-5 border border-white/10 text-zinc-300 hover:bg-white/10 hover:text-white" asChild>
            <Link href={`/tasks/${taskId}`}><ArrowLeft /> Back to task</Link>
          </Button>
        </div>
      </div>
    );
  }

  return (
    <EditorShell
      taskId={taskId}
      initialProject={data.project}
      initialVersion={data.version}
      assets={data.assets}
      transcriptByAssetId={data.transcripts}
    />
  );
}
