"use client";
import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { PageError, PageLoading } from "@/components/app/page-state";
import { Button } from "@/components/ui/button";
import { EditorWorkspace } from "@/components/editor/editor-workspace";
import { editorRequest } from "@/components/editor/use-editor";
import { useSession } from "@/lib/auth-client";
import { signInPath } from "@/lib/auth-redirect";
import type { ClipSummary } from "@/components/editor/export-panel";

export default function TaskEditPage() {
  const t = useTranslations("editor");
  const { id } = useParams<{ id: string }>();
  const requested = useSearchParams().get("clip");
  const router = useRouter();
  const { data: session, isPending } = useSession();
  const signedIn = Boolean(session?.user);
  const signedOut = !isPending && !signedIn;
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
  // The iOS app links here in Safari, which may not have a session yet.
  useEffect(() => {
    if (signedOut) {
      router.replace(
        signInPath(window.location.pathname + window.location.search),
      );
    } else if (signedIn) {
      void load();
    }
  }, [signedOut, signedIn, load, router]);
  if (loading || signedOut) return <PageLoading />;
  if (error) return <PageError message={error} retry={() => void load()} />;
  const clip = clips.find((c) => c.id === selected);
  if (!clip || task?.status !== "completed")
    return (
      <div className="max-w-lg mx-auto px-4 py-24 text-center space-y-4">
        <h1 className="text-xl font-semibold">
          {task?.status !== "completed"
            ? t("editPage.processing")
            : t("editPage.empty")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("editPage.readyWhenComplete")}
        </p>
        <Button asChild>
          <Link href={`/tasks/${id}`}>{t("editPage.back")}</Link>
        </Button>
      </div>
    );
  return (
    <EditorWorkspace
      key={`${id}:${clip.id}`}
      taskId={id}
      title={task?.source_title || t("editPage.untitled")}
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
