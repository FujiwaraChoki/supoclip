"use client";
import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { PageError, PageLoading } from "@/components/app/page-state";
import { Button } from "@/components/ui/button";
import { EditorWorkspace } from "@/components/editor/editor-workspace";
import { editorRequest } from "@/components/editor/use-editor";
import type { ClipSummary } from "@/components/editor/export-panel";

export default function TaskEditPage() {
  const { id } = useParams<{ id: string }>();
  const requested = useSearchParams().get("clip");
  const [task, setTask] = useState<{
    source_title: string;
    status: string;
  } | null>(null);
  const [clips, setClips] = useState<ClipSummary[]>([]),
    [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState<string | null>(null);
  const load = useCallback(
    async (select?: string) => {
      try {
        const next = await editorRequest<{
          source_title: string;
          status: string;
        }>(`/api/tasks/${id}`);
        setTask(next);
        if (next.status === "completed") {
          const result = await editorRequest<{ clips: ClipSummary[] }>(
            `/api/tasks/${id}/clips`,
          );
          setClips(result.clips);
          setSelected(
            (current) =>
              select ??
              (result.clips.some((c) => c.id === current)
                ? current
                : (result.clips.find((c) => c.id === requested)?.id ??
                  result.clips[0]?.id ??
                  null)),
          );
        }
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [id, requested],
  );
  useEffect(() => {
    void load();
  }, [load]);
  if (loading) return <PageLoading />;
  if (error) return <PageError message={error} retry={() => void load()} />;
  const clip = clips.find((c) => c.id === selected);
  if (!clip || task?.status !== "completed")
    return (
      <div className="max-w-lg mx-auto px-4 py-24 text-center space-y-4">
        <h1 className="text-xl font-semibold">
          {task?.status !== "completed"
            ? "Your clips are still processing"
            : "No clips to edit yet"}
        </h1>
        <p className="text-sm text-muted-foreground">
          The editor is ready once your generation completes.
        </p>
        <Button asChild>
          <Link href={`/tasks/${id}`}>Back to generation</Link>
        </Button>
      </div>
    );
  return (
    <EditorWorkspace
      key={`${id}:${clip.id}`}
      taskId={id}
      title={task?.source_title || "Untitled generation"}
      clips={clips}
      clip={clip}
      onSelect={(clipId) => {
        setSelected(clipId);
        const url = new URL(window.location.href);
        url.searchParams.set("clip", clipId);
        window.history.replaceState(null, "", url);
      }}
      refreshClips={load}
    />
  );
}
