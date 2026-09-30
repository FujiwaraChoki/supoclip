"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import Image from "next/image";
import { ArrowRight, Link2Off, Sparkles } from "lucide-react";

import { ClipFocus, ClipTile, type WallClip } from "@/components/app/clip-wall";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { formatClipDuration } from "@/lib/generations";

interface SharedClip {
  id: string;
  filename: string;
  start_time: string;
  end_time: string;
  duration: number;
  text: string;
  relevance_score: number;
  reasoning: string;
  clip_order: number;
  virality_score: number;
  hook_title: string | null;
}

interface SharedTask {
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
  created_at: string;
  clips: SharedClip[];
}

export default function SharedGenerationPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const [task, setTask] = useState<SharedTask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [focusIndex, setFocusIndex] = useState<number | null>(null);

  const loadSharedTask = useCallback(async () => {
    if (!token) return;

    try {
      const response = await fetch(`/api/share/${encodeURIComponent(token)}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        const parsed = await parseApiError(response, "This share link is unavailable");
        throw new Error(formatSupportMessage(parsed));
      }
      setTask(await response.json());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "This share link is unavailable");
    } finally {
      setIsLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void loadSharedTask();
  }, [loadSharedTask]);

  const clipFileUrl = (clipId: string) =>
    `/api/share/${encodeURIComponent(token)}/clips/${encodeURIComponent(clipId)}/file`;

  const download = (clip: SharedClip) => {
    const link = document.createElement("a");
    link.href = clipFileUrl(clip.id);
    link.download = clip.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const header = (
    <header className="sticky top-0 z-30 border-b bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-8">
        <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
          <Image src="/logo.png" alt="" width={22} height={22} className="size-[22px]" priority />SupoClip
        </Link>
        <Button asChild size="sm" className="rounded-full">
          <Link href="/"><Sparkles className="size-4" />Make your own</Link>
        </Button>
      </div>
    </header>
  );

  if (isLoading) {
    return (
      <main className="min-h-dvh bg-canvas">
        {header}
        <div className="mx-auto max-w-7xl space-y-6 px-4 py-10 sm:px-8">
          <Skeleton className="h-10 w-2/3 max-w-xl" />
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {[0, 1, 2, 3, 4].map((key) => <Skeleton key={key} className="aspect-[9/16] rounded-xl" />)}
          </div>
        </div>
      </main>
    );
  }

  if (error || !task) {
    return (
      <main className="flex min-h-dvh flex-col bg-canvas">
        {header}
        <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
          <span className="flex size-14 items-center justify-center rounded-2xl bg-muted text-muted-foreground"><Link2Off className="size-7" /></span>
          <h1 className="mt-4 font-display text-2xl font-bold tracking-tight">This link isn&apos;t available</h1>
          <p role="alert" className="mt-2 max-w-md text-sm text-muted-foreground">{error || "This share link is unavailable"}</p>
          <Button asChild className="mt-6"><Link href="/">Create your own clips</Link></Button>
        </div>
      </main>
    );
  }

  const clips: WallClip[] = [...task.clips]
    .sort((a, b) => (b.virality_score ?? 0) - (a.virality_score ?? 0) || a.clip_order - b.clip_order)
    .map((clip) => ({
      ...clip,
      video_url: clipFileUrl(clip.id),
      hook_score: 0,
      engagement_score: 0,
      value_score: 0,
      shareability_score: 0,
      hook_type: null,
    }));
  const bestScore = clips.reduce((best, clip) => Math.max(best, clip.virality_score || 0), 0);
  const totalSeconds = clips.reduce((sum, clip) => sum + (clip.duration || 0), 0);
  const focused = focusIndex !== null ? clips[focusIndex] : null;

  return (
    <main className="min-h-dvh bg-canvas">
      {header}

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-8 md:py-12">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Shared generation</p>
        <h1 className="mt-2 max-w-4xl font-display text-3xl font-bold tracking-tight md:text-4xl">{task.source_title}</h1>
        <p className="mt-3 text-sm text-muted-foreground">
          {clips.length} {clips.length === 1 ? "clip" : "clips"} · {formatClipDuration(totalSeconds)} of highlights
          {bestScore > 0 && <> · top virality score <span className="font-semibold text-brand">{bestScore}</span></>}
          {" · "}{new Date(task.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
        </p>

        <div className="mt-8 grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {clips.map((clip, index) => (
            <ClipTile
              key={clip.id}
              clip={clip}
              taskId=""
              busy={false}
              best={bestScore > 0 && clip.virality_score === bestScore}
              editable={false}
              onOpen={() => setFocusIndex(index)}
              onDownload={() => download(task.clips.find((item) => item.id === clip.id)!)}
            />
          ))}
        </div>

        <section className="relative mt-16 overflow-hidden rounded-3xl bg-stone-950 px-6 py-10 text-white sm:px-10">
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_15%_0%,oklch(0.45_0.12_40/0.55),transparent_60%)]" />
          <div className="relative flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="font-display text-2xl font-bold tracking-tight">Turn your long videos into clips like these.</h2>
              <p className="mt-2 max-w-lg text-sm text-white/65">SupoClip finds the best moments, frames the speaker and writes the captions. Open source and free to self-host.</p>
            </div>
            <Button asChild size="lg" className="shrink-0 rounded-full bg-white text-stone-950 hover:bg-white/90">
              <Link href="/">Try SupoClip<ArrowRight className="size-4" /></Link>
            </Button>
          </div>
        </section>
      </div>

      <ClipFocus
        clip={focused}
        taskId=""
        index={focusIndex ?? 0}
        total={clips.length}
        busy={false}
        editable={false}
        onNavigate={(delta) => setFocusIndex((i) => (i === null ? i : (i + delta + clips.length) % clips.length))}
        onClose={() => setFocusIndex(null)}
        onDownload={() => { if (focused) download(task.clips.find((item) => item.id === focused.id)!); }}
        hookTypeLabel={() => ""}
      />
    </main>
  );
}
