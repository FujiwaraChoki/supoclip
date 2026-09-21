"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "@/lib/toast";
import { ArrowLeft, Copy, Download } from "lucide-react";

interface RankingClip {
  id: string;
  filename: string;
  duration: number;
}

interface RankingTask {
  id: string;
  status: string;
  progress: number;
  progress_message: string | null;
  task_type: string;
  clips: RankingClip[];
}

const STAGE_LABELS: Record<string, string> = {
  load: "Loading input videos",
  order: "Ordering clips",
  render: "Rendering compilation",
  export: "Applying export settings",
  complete: "Compilation ready",
};

const EXPORT_PRESETS = [
  { id: "tiktok", name: "TikTok" },
  { id: "reels", name: "Instagram Reels" },
  { id: "shorts", name: "YouTube Shorts" },
  { id: "youtube_shorts", name: "YouTube Shorts (long)" },
  { id: "facebook_reels", name: "Facebook Reels" },
  { id: "threads", name: "Threads" },
];

export default function RankingTaskPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const [task, setTask] = useState<RankingTask | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const [exportPreset, setExportPreset] = useState("tiktok");
  const [isExporting, setIsExporting] = useState(false);
  const [isDuplicating, setIsDuplicating] = useState(false);

  const fetchTask = useCallback(async () => {
    const response = await fetch(`/api/tasks/${id}`, { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    setTask(data);
  }, [id]);

  useEffect(() => {
    void fetchTask();
  }, [fetchTask]);

  useEffect(() => {
    if (!task || (task.status !== "queued" && task.status !== "processing")) return;

    const eventSource = new EventSource(`/api/tasks/${id}/progress`);
    const handleUpdate = (event: MessageEvent) => {
      const data = JSON.parse(event.data);
      if (data.stage) setStage(data.stage);
      if (data.status === "completed" || data.status === "error") {
        void fetchTask();
      }
    };
    eventSource.addEventListener("status", handleUpdate);
    eventSource.addEventListener("progress", handleUpdate);
    eventSource.addEventListener("close", () => {
      eventSource.close();
      void fetchTask();
    });
    return () => eventSource.close();
  }, [task, id, fetchTask]);

  const handleExport = async () => {
    const clip = task?.clips?.[0];
    if (!clip) return;
    setIsExporting(true);
    try {
      const response = await fetch(
        `/api/tasks/${id}/clips/${clip.id}/export?preset=${exportPreset}`
      );
      if (!response.ok) throw new Error("Export failed");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
      link.href = url;
      link.download = `ranking_${exportPreset}_${timestamp}.mp4`;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Failed to export compilation");
    } finally {
      setIsExporting(false);
    }
  };

  const handleDuplicate = async () => {
    setIsDuplicating(true);
    try {
      const duplicateResponse = await fetch(`/api/ranking/tasks/${id}/duplicate`, {
        method: "POST",
      });
      if (!duplicateResponse.ok) throw new Error("Failed to duplicate project");
      const { task_id: newTaskId } = await duplicateResponse.json();

      const renderResponse = await fetch(`/api/ranking/tasks/${newTaskId}/render`, {
        method: "POST",
      });
      if (!renderResponse.ok) throw new Error("Failed to start rendering the duplicate");

      router.push(`/rank/tasks/${newTaskId}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to duplicate project");
      setIsDuplicating(false);
    }
  };

  if (!task) {
    return (
      <div className="min-h-screen bg-background px-6 lg:px-12 py-8">
        <Skeleton className="h-8 w-64 mb-4" />
        <Skeleton className="h-96 w-full max-w-md" />
      </div>
    );
  }

  const isProcessing = task.status === "queued" || task.status === "processing";
  const clip = task.clips?.[0];

  return (
    <div className="min-h-screen bg-background">
      <div className="border-b border-border bg-background">
        <div className="max-w-3xl mx-auto px-6 lg:px-12 py-6">
          <Link href="/list">
            <Button variant="ghost" size="sm" className="text-muted-foreground -ml-2 mb-2">
              <ArrowLeft className="w-4 h-4" />
              Back
            </Button>
          </Link>
          <h1 className="text-[32px] font-bold leading-[1.15] tracking-[-0.01em] text-foreground">
            Ranking compilation
          </h1>
        </div>
      </div>

      <div className="max-w-3xl mx-auto px-6 lg:px-12 py-8 space-y-6">
        {isProcessing && (
          <div className="border border-border p-6 space-y-3">
            <p className="text-[15px] text-foreground">
              {stage ? STAGE_LABELS[stage] ?? task.progress_message : task.progress_message ?? "Starting…"}
            </p>
            <Progress value={task.progress} />
          </div>
        )}

        {task.status === "error" && (
          <div className="border border-border bg-foreground text-background p-6 space-y-3">
            <div>
              <p className="text-[15px] font-medium">Render failed</p>
              <p className="text-[13px] mt-1">{task.progress_message}</p>
            </div>
            <Button variant="outline" onClick={() => void handleDuplicate()} disabled={isDuplicating}>
              <Copy className="w-4 h-4" />
              {isDuplicating ? "Duplicating…" : "Duplicate & try again"}
            </Button>
          </div>
        )}

        {task.status === "completed" && clip && (
          <div className="space-y-6">
            <div className="border border-border bg-background aspect-[9/16] max-w-sm">
              <video
                src={`/api/tasks/${id}/clips/${clip.id}/file`}
                controls
                className="w-full h-full object-contain"
              />
            </div>
            <div className="flex items-center gap-3">
              <Select value={exportPreset} onValueChange={setExportPreset}>
                <SelectTrigger className="w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXPORT_PRESETS.map((preset) => (
                    <SelectItem key={preset.id} value={preset.id}>
                      {preset.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button onClick={() => void handleExport()} disabled={isExporting}>
                <Download className="w-4 h-4" />
                {isExporting ? "Exporting…" : "Export"}
              </Button>
              <Button variant="outline" onClick={() => void handleDuplicate()} disabled={isDuplicating}>
                <Copy className="w-4 h-4" />
                {isDuplicating ? "Duplicating…" : "Duplicate"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
