"use client";

import { CaptionSizeControl } from "@/components/caption-size-control";

import { useState, useEffect, useCallback } from "react";
import { toast } from "sonner";
import { useTaskProgress } from "@/hooks/use-task-progress";
import { StatusBadge, ACTIVE_TASK_STATUSES } from "@/components/app/status-badge";
import { getClipUrl, requestAction, downloadBlob, EXPORT_PRESETS } from "@/lib/clip-actions";
import { useParams, useRouter } from "next/navigation";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageLoading, PageError } from "@/components/app/page-state";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
} from "@/components/ui/sheet";
import { useSession } from "@/lib/auth-client";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { buildFontOptionsPayload, FONT_TEMPLATE_DEFAULT_VALUE } from "@/lib/font-options";
import {
  ArrowLeft,
  Download,
  Star,
  AlertCircle,
  Trash2,
  Edit2,
  X,
  Check,
  Zap,
  MessageSquare,
  TrendingUp,
  Share2,
  Link2Off,
  Clock,
  Scissors,
  Settings2,
  Clapperboard,
} from "lucide-react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { Progress } from "@/components/ui/progress";
import Link from "next/link";
import DynamicVideoPlayer from "@/components/dynamic-video-player";
import { TranscriptPreview } from "@/components/transcript-preview";
import { FontSelectOption, type FontOption } from "@/components/font-select-option";

interface Clip {
  id: string;
  filename: string;
  file_path: string;
  start_time: string;
  end_time: string;
  duration: number;
  text: string;
  relevance_score: number;
  reasoning: string;
  clip_order: number;
  created_at: string;
  video_url: string;
  // Virality scores
  virality_score: number;
  hook_score: number;
  engagement_score: number;
  value_score: number;
  shareability_score: number;
  hook_type: string | null;
  hook_title: string | null;
}

interface TaskDetails {
  id: string;
  user_id: string;
  source_id: string;
  source_title: string;
  source_type: string;
  status: string;
  progress?: number;
  progress_message?: string;
  clips_count: number;
  created_at: string;
  updated_at: string;
  font_family?: string | null;
  font_size?: number | null;
  font_color?: string | null;
  caption_template?: string;
  cut_long_pauses?: boolean;
  pause_threshold_ms?: number;
  remove_filler_words?: boolean;
  filtered_words?: string[];
  share_enabled?: boolean;
}

export default function TaskPage() {
  const params = useParams();
  const router = useRouter();
  const { data: session } = useSession();
  const [task, setTask] = useState<TaskDetails | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [editedTitle, setEditedTitle] = useState("");
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [deletingClipId, setDeletingClipId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [exportPreset, setExportPreset] = useState("original");
  const [shareState, setShareState] = useState<"idle" | "copying" | "copied">("idle");
  const [isRevokingShare, setIsRevokingShare] = useState(false);

  // null means "use the caption template's own value" — mirrors the create form's contract.
  const [projectFontFamily, setProjectFontFamily] = useState<string | null>(null);
  const [projectFontSize, setProjectFontSize] = useState<number | null>(null);
  const [projectFontColor, setProjectFontColor] = useState<string | null>(null);
  const [projectCaptionTemplate, setProjectCaptionTemplate] = useState("default");
  const [projectCutLongPauses, setProjectCutLongPauses] = useState(false);
  const [projectPauseThresholdMs, setProjectPauseThresholdMs] = useState("900");
  const [projectRemoveFillerWords, setProjectRemoveFillerWords] = useState(false);
  const [projectFilteredWords, setProjectFilteredWords] = useState("");
  const [isApplyingSettings, setIsApplyingSettings] = useState(false);
  const [settingsSheetOpen, setSettingsSheetOpen] = useState(false);
  const [availableFonts, setAvailableFonts] = useState<FontOption[]>([]);
  const [deletingFontName, setDeletingFontName] = useState<string | null>(null);
  const [availableTemplates, setAvailableTemplates] = useState<
    Array<{ id: string; name: string; description: string; animation: string }>
  >([]);
  const [fontToDelete, setFontToDelete] = useState<FontOption | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  const taskApiUrl = "/api/tasks";

  const buildSupportError = useCallback(async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  }, []);

  const fetchTaskStatus = useCallback(
    async (retryCount = 0, maxRetries = 5, background = false) => {
      if (!params.id) return false;

      try {
        const taskResponse = await fetch(`${taskApiUrl}/${params.id}`, {
          cache: "no-store",
        });

        // Handle 404 with retry logic (task might not be persisted yet)
        if (taskResponse.status === 404 && retryCount < maxRetries) {
          console.log(
            `Task not found yet, retrying in ${(retryCount + 1) * 500}ms... (${retryCount + 1}/${maxRetries})`,
          );
          await new Promise((resolve) => setTimeout(resolve, (retryCount + 1) * 500));
          return fetchTaskStatus(retryCount + 1, maxRetries, background);
        }

        if (!taskResponse.ok) {
          throw new Error(await buildSupportError(taskResponse, `Failed to fetch task: ${taskResponse.status}`));
        }

        const taskData = await taskResponse.json();
        setProgress(taskData.progress ?? 0);
        setProgressMessage(taskData.progress_message ?? "");
        setError(null);
        if (!background) {
        setProjectFontFamily(taskData.font_family ?? null);
        setProjectFontSize(typeof taskData.font_size === "number" ? taskData.font_size : null);
        setProjectFontColor(taskData.font_color ?? null);
        setProjectCaptionTemplate(taskData.caption_template || "default");
        setProjectCutLongPauses(Boolean(taskData.cut_long_pauses));
        setProjectPauseThresholdMs(String(taskData.pause_threshold_ms || 900));
        setProjectRemoveFillerWords(Boolean(taskData.remove_filler_words));
        setProjectFilteredWords((taskData.filtered_words || []).join(", "));

        }

        // Fetch clips if task is completed or processing (incremental clips)
        if (taskData.status !== "queued") {
          const clipsResponse = await fetch(`${taskApiUrl}/${params.id}/clips`, {
            cache: "no-store",
          });

          if (!clipsResponse.ok) {
            throw new Error(await buildSupportError(clipsResponse, `Failed to fetch clips: ${clipsResponse.status}`));
          }

          const clipsData = await clipsResponse.json();
          const nextClips = clipsData.clips || [];
          setClips((prev) => {
            if (taskData.status === "completed") {
              return nextClips;
            }

            const merged = new Map<string, Clip>();
            for (const clip of prev) {
              merged.set(clip.id, clip);
            }
            for (const clip of nextClips) {
              merged.set(clip.id, clip);
            }
            return Array.from(merged.values()).sort(
              (a, b) => (a.clip_order ?? 0) - (b.clip_order ?? 0),
            );
          });
        }

        setTask(taskData);
        return true;
      } catch (err) {
        console.error("Error fetching task data:", err);
        if (!background) setError(err instanceof Error ? err.message : "Failed to load task");
        return false;
      }
    },
    [buildSupportError, params.id, taskApiUrl],
  );

  // Initial fetch - runs immediately, doesn't wait for session
  useEffect(() => {
    if (!params.id) return;

    const fetchTaskData = async () => {
      try {
        setIsLoading(true);
        await fetchTaskStatus();
      } finally {
        setIsLoading(false);
      }
    };

    fetchTaskData();
  }, [params.id, fetchTaskStatus]);

  useEffect(() => {
    const loadFonts = async () => {
      try {
        const response = await fetch("/api/fonts", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const data = await response.json();
        setAvailableFonts(data.fonts || []);
      } catch (loadError) {
        console.error("Failed to load fonts:", loadError);
      }
    };

    void loadFonts();

    const loadTemplates = async () => {
      try {
        const response = await fetch("/api/caption-templates");
        if (response.ok) {
          const data = await response.json();
          setAvailableTemplates(data.templates || []);
        }
      } catch (error) {
        console.error("Failed to load caption templates:", error);
      }
    };
    void loadTemplates();
  }, []);

  const { reconnecting } = useTaskProgress<Clip>({
    taskId: String(params.id || ""),
    active: ACTIVE_TASK_STATUSES.includes(task?.status || ""),
    refresh: () => fetchTaskStatus(0, 0, true),
    onProgress: (data) => {
      if (typeof data.progress === "number") setProgress(data.progress);
      if (typeof data.message === "string") setProgressMessage(data.message);
    },
    onClip: (clip) => setClips((current) =>
      [...current.filter((item) => item.id !== clip.id), clip].sort((a, b) => a.clip_order - b.clip_order)),
  });

  const runAction = async (name: string, action: () => Promise<void>) => {
    if (pendingAction) return;
    setPendingAction(name);
    try { await action(); }
    catch (err) { toast.error(err instanceof Error ? err.message : "Could not complete the action. Please try again."); }
    finally { setPendingAction(null); }
  };

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const getScoreColor = (score: number) => {
    if (score >= 0.8) return "bg-green-100 text-green-800";
    if (score >= 0.6) return "bg-yellow-100 text-yellow-800";
    return "bg-red-100 text-red-800";
  };

  const getViralityColor = (score: number) => {
    if (score >= 80) return "text-green-600";
    if (score >= 60) return "text-yellow-600";
    if (score >= 40) return "text-orange-600";
    return "text-red-600";
  };

  const getViralityBgColor = (score: number) => {
    if (score >= 80) return "bg-green-500";
    if (score >= 60) return "bg-yellow-500";
    if (score >= 40) return "bg-orange-500";
    return "bg-red-500";
  };

  const getHookTypeLabel = (hookType: string | null) => {
    const labels: Record<string, string> = {
      question: "Question Hook",
      statement: "Bold Statement",
      statistic: "Data/Stats",
      story: "Story Hook",
      contrast: "Contrast Hook",
      none: "No Hook",
    };
    return labels[hookType || "none"] || hookType || "None";
  };

  const handleEditTitle = async () => {
    if (!editedTitle.trim() || !session?.user?.id || !params.id) return;

    try {
      const response = await fetch(`${taskApiUrl}/${params.id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ title: editedTitle }),
      });

      if (response.ok) {
        setTask(task ? { ...task, source_title: editedTitle } : null);
        setIsEditing(false);
      } else {
        toast.error(await buildSupportError(response, "Failed to update title"));
      }
    } catch (err) {
      console.error("Error updating title:", err);
      toast.error(err instanceof Error ? err.message : "Failed to update title");
    }
  };

  const handleDeleteTask = async () => {
    if (!session?.user?.id || !params.id) return;

    setIsDeleting(true);
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}`, {
        method: "DELETE",
      });

      if (response.ok) {
        router.push("/list");
      } else {
        toast.error(await buildSupportError(response, "Failed to delete task"));
      }
    } catch (err) {
      console.error("Error deleting task:", err);
      toast.error(err instanceof Error ? err.message : "Failed to delete task");
    } finally {
      setIsDeleting(false);
      setShowDeleteDialog(false);
    }
  };

  const handleDeleteClip = async (clipId: string) => {
    if (!session?.user?.id || !params.id) return;

    try {
      const response = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}`, {
        method: "DELETE",
      });

      if (response.ok) {
        setClips(clips.filter((clip) => clip.id !== clipId));
        setDeletingClipId(null);
      } else {
        toast.error(await buildSupportError(response, "Failed to delete clip"));
      }
    } catch (err) {
      console.error("Error deleting clip:", err);
      toast.error(err instanceof Error ? err.message : "Failed to delete clip");
    }
  };

  const handleApplyProjectSettings = async () => {
    if (!session?.user?.id || !params.id) return;
    const fontOptions = buildFontOptionsPayload(projectFontFamily, projectFontSize, projectFontColor);
    const parsedPauseThreshold = Number(projectPauseThresholdMs || "900");
    const safePauseThreshold = Number.isFinite(parsedPauseThreshold)
      ? Math.max(250, Math.min(3000, Math.round(parsedPauseThreshold)))
      : 900;
    const normalizedFilteredWords = projectFilteredWords
      .split(",")
      .map((word) => word.trim().toLowerCase())
      .filter(Boolean);

    setIsApplyingSettings(true);
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}/settings`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ...fontOptions,
          caption_template: projectCaptionTemplate,
          cut_long_pauses: projectCutLongPauses,
          pause_threshold_ms: safePauseThreshold,
          remove_filler_words: projectRemoveFillerWords,
          filtered_words: normalizedFilteredWords,
          apply_to_existing: true,
        }),
      });
      if (!response.ok) {
        toast.error(await buildSupportError(response, "Failed to apply settings"));
        return;
      }
      await fetchTaskStatus();
      setSettingsSheetOpen(false);
      toast.success("Generation settings applied");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to apply settings");
    } finally {
      setIsApplyingSettings(false);
    }
  };

  const handleDeleteFont = async (font: FontOption) => {
    if (font.scope !== "user" || deletingFontName) return;


    setDeletingFontName(font.name);
    try {
      const response = await fetch(`/api/fonts/${encodeURIComponent(font.name)}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, "Failed to delete font"));
      }

      const remainingFonts = availableFonts.filter((item) => item.name !== font.name);
      setAvailableFonts(remainingFonts);
      if (projectFontFamily === font.name) {
        // The deleted font was in use — fall back to the caption template's own font.
        setProjectFontFamily(null);
      }
    } catch (deleteError) {
      toast.error(deleteError instanceof Error ? deleteError.message : "Failed to delete font");
    } finally {
      setDeletingFontName(null);
    }
  };

  const handleExportClip = async (clipId: string, fallbackFilename: string) => {
    if (!session?.user?.id || !task?.id) return;

    const response = await fetch(`${taskApiUrl}/${task.id}/clips/${clipId}/export?preset=${exportPreset}`, {
      cache: "no-store",
    });

    if (!response.ok) {
      toast.error(await buildSupportError(response, "Failed to export clip"));
      return;
    }

    const blob = await response.blob();
    downloadBlob(blob, `${fallbackFilename.replace(/\.mp4$/i, "")}_${exportPreset}.mp4`);
  };

  const handleDownloadClip = (clip: Clip) => {
    if (exportPreset === "original") {
      const link = document.createElement("a");
      link.href = getClipUrl(clip.video_url, clip.filename);
      link.download = clip.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      return;
    }
    void runAction(clip.id, () => handleExportClip(clip.id, clip.filename));
  };

  const handleCopyShareLink = async () => {
    if (!task?.id || shareState === "copying") return;

    setShareState("copying");
    try {
      const response = await fetch(`${taskApiUrl}/${task.id}/share`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, "Failed to create share link"));
      }

      const data = (await response.json()) as { share_path: string };
      const shareUrl = new URL(data.share_path, window.location.origin).toString();
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(shareUrl);
      } else {
        const input = document.createElement("input");
        input.value = shareUrl;
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.appendChild(input);
        input.select();
        document.execCommand("copy");
        input.remove();
      }
      setTask((currentTask) =>
        currentTask ? { ...currentTask, share_enabled: true } : currentTask,
      );
      setShareState("copied");
      window.setTimeout(() => setShareState("idle"), 2500);
    } catch (shareError) {
      setShareState("idle");
      toast.error(shareError instanceof Error ? shareError.message : "Failed to create share link");
    }
  };

  const handleRevokeShareLink = async () => {
    if (!task?.id || isRevokingShare) return;

    setIsRevokingShare(true);
    try {
      const response = await fetch(`${taskApiUrl}/${task.id}/share`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, "Failed to disable share link"));
      }
      setTask((currentTask) =>
        currentTask ? { ...currentTask, share_enabled: false } : currentTask,
      );
    } catch (revokeError) {
      toast.error(revokeError instanceof Error ? revokeError.message : "Failed to disable share link");
    } finally {
      setIsRevokingShare(false);
    }
  };

  if (isLoading) return <PageLoading />;
  if (error) return <PageError message={error} retry={() => void fetchTaskStatus()} />;

  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <div className="border-b bg-white">
        <div className="max-w-6xl mx-auto px-4 py-6">
          <div className="flex items-center gap-4 mb-4">
            <Link href="/">
              <Button variant="ghost" size="sm">
                <ArrowLeft className="w-4 h-4" />
                Back
              </Button>
            </Link>
          </div>

          {task && (
            <div>
              <div className="flex items-center gap-3 mb-2">
                {isEditing ? (
                  <div className="flex items-center gap-2 flex-1">
                    <Input
                      value={editedTitle}
                      onChange={(e) => setEditedTitle(e.target.value)}
                      className="text-2xl font-bold h-auto py-1"
                      autoFocus
                    />
                    <Button aria-label="Save title" size="sm" onClick={handleEditTitle} disabled={!editedTitle.trim()}>
                      <Check className="w-4 h-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label="Cancel title edit"
                      onClick={() => {
                        setIsEditing(false);
                        setEditedTitle(task.source_title);
                      }}
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                ) : (
                  <>
                    <h1 className={`min-w-0 break-words font-[var(--font-syne)] text-2xl font-bold text-black ${task.status === "processing" || task.status === "queued" ? "shimmer" : ""}`}>{task.source_title}</h1>
                    <div className="flex items-center gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label="Edit title"
                        onClick={() => {
                          setIsEditing(true);
                          setEditedTitle(task.source_title);
                        }}
                      >
                        <Edit2 className="w-4 h-4" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label="Delete generation"
                        className="text-red-600 hover:text-red-700 hover:bg-red-50"
                        onClick={() => setShowDeleteDialog(true)}
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-4 text-sm text-gray-600">
                <Badge variant="outline" className="capitalize">
                  {task.source_type}
                </Badge>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="flex items-center gap-1 cursor-default">
                        <Clock className="w-4 h-4" />
                        {new Date(task.created_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      {new Date(task.created_at).toLocaleString(undefined, {
                        year: "numeric",
                        month: "long",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                        timeZoneName: "short",
                      })}
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                <StatusBadge status={task.status} />
                {task.status === "completed" && <span>{clips.length} {clips.length === 1 ? "clip" : "clips"} generated</span>}
                {task.status === "completed" && clips.length > 0 && (
                  <Link href={`/tasks/${task.id}/edit`}>
                    <Button size="sm" variant="outline">
                      <Clapperboard className="w-4 h-4" />
                      Open Editor
                    </Button>
                  </Link>
                )}
                {task.status === "completed" && clips.length > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleCopyShareLink}
                    disabled={shareState === "copying"}
                    aria-live="polite"
                  >
                    {shareState === "copied" ? (
                      <Check className="w-4 h-4" />
                    ) : (
                      <Share2 className="w-4 h-4" />
                    )}
                    {shareState === "copying"
                      ? "Creating link…"
                      : shareState === "copied"
                        ? "Link copied"
                        : "Copy share link"}
                  </Button>
                )}
                {task.status === "completed" && task.share_enabled && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleRevokeShareLink}
                    disabled={isRevokingShare}
                  >
                    <Link2Off className="w-4 h-4" />
                    {isRevokingShare ? "Disabling…" : "Disable share link"}
                  </Button>
                )}
                {(task.status === "queued" || task.status === "processing") && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={pendingAction !== null}
                    onClick={() => void runAction("cancel", async () => {
                      await requestAction(`${taskApiUrl}/${task.id}/cancel`, "POST");
                      await fetchTaskStatus();
                      toast.success("Generation cancelled");
                    })}
                  >
                    Cancel
                  </Button>
                )}
                {(task.status === "cancelled" || task.status === "error") && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={pendingAction !== null}
                    onClick={() => void runAction("resume", async () => {
                      await requestAction(`${taskApiUrl}/${task.id}/resume`, "POST");
                      await fetchTaskStatus();
                      toast.success("Generation resumed");
                    })}
                  >
                    Resume
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-6xl mx-auto px-4 py-8">
        {reconnecting && <p role="status" className="mb-4 text-sm text-muted-foreground">Reconnecting to live updates. Your video is still processing.</p>}
        {task?.status === "processing" || task?.status === "queued" ? (
          <div className="space-y-8">
            {/* Progress indicator */}
            <div className="flex flex-col items-center py-8">
              {/* Minimal animated dots */}
              <div className="relative group flex items-center gap-1.5 mb-8 cursor-default">
                <span className="w-2 h-2 bg-neutral-800 rounded-full animate-[pulse_1.4s_ease-in-out_infinite]" />
                <span className="w-2 h-2 bg-neutral-800 rounded-full animate-[pulse_1.4s_ease-in-out_0.2s_infinite]" />
                <span className="w-2 h-2 bg-neutral-800 rounded-full animate-[pulse_1.4s_ease-in-out_0.4s_infinite]" />
                <div className="absolute top-full mt-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md border bg-popover px-3 py-1.5 text-sm text-popover-foreground shadow-md opacity-0 scale-95 transition-all group-hover:opacity-100 group-hover:scale-100 pointer-events-none">
                  ☕&nbsp;&nbsp;Grab a coffee, and come back to ready-to-post clips.
                </div>
              </div>

              {/* Status message */}
              <p className="shimmer text-neutral-600/60 text-sm tracking-wide mb-8">
                {progressMessage || (task.status === "queued" ? "Waiting in queue" : "Processing")}
              </p>

              {/* Minimal progress bar */}
              {progress > 0 && (
                <div className="w-48">
                  <div className="h-px bg-neutral-200 w-full relative overflow-hidden">
                    <div
                      className="absolute inset-y-0 left-0 bg-neutral-800 transition-all duration-700 ease-out"
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                  <p className="text-[11px] text-neutral-400 text-center mt-3 tabular-nums">{progress}%</p>
                </div>
              )}
            </div>

            {/* Live clips grid — shows clips as they render */}
            {clips.length > 0 && (
              <div className="grid gap-6">
                <p className="text-sm text-neutral-500 text-center">
                  {clips.length} clip{clips.length !== 1 ? "s" : ""} ready
                </p>
                {clips.map((clip) => (
                  <Card key={clip.id} className="overflow-hidden">
                    <CardContent className="p-0">
                      <div className="flex flex-col lg:flex-row">
                        <div className="relative flex-shrink-0 bg-black rounded-lg overflow-hidden m-3">
                          <DynamicVideoPlayer src={getClipUrl(clip.video_url, clip.filename)} />
                        </div>
                        <div className="p-6 flex-1">
                          <div className="flex items-start justify-between mb-4">
                            <div>
                              <h3 className="font-semibold text-lg text-black mb-1">
                                {clip.hook_title || `Clip ${clip.clip_order}`}
                              </h3>
                              <div className="flex items-center gap-2 text-sm text-gray-600">
                                <span>Clip {clip.clip_order}</span>
                                <span>•</span>
                                <span>{clip.start_time} - {clip.end_time}</span>
                                <span>•</span>
                                <span>{formatDuration(clip.duration)}</span>
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              {clip.virality_score > 0 && (
                                <Badge className={`${getViralityBgColor(clip.virality_score)} text-white`}>
                                  <Zap className="w-3 h-3 mr-1" />
                                  {clip.virality_score}
                                </Badge>
                              )}
                              <Badge className={getScoreColor(clip.relevance_score)}>
                                <Star className="w-3 h-3 mr-1" />
                                {(clip.relevance_score * 100).toFixed(0)}%
                              </Badge>
                            </div>
                          </div>
                          {clip.text && (
                            <TranscriptPreview text={clip.text} clipTitle={`Clip ${clip.clip_order}`} />
                          )}
                          <Button size="sm" variant="outline" asChild>
                            <a href={getClipUrl(clip.video_url, clip.filename)} download={clip.filename}>
                              <Download className="w-4 h-4" />
                              Download
                            </a>
                          </Button>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </div>
        ) : !task ? (
          <div className="flex flex-col items-center justify-center min-h-[50vh] py-16">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 bg-neutral-300 rounded-full animate-[pulse_1.4s_ease-in-out_infinite]" />
              <span className="w-2 h-2 bg-neutral-300 rounded-full animate-[pulse_1.4s_ease-in-out_0.2s_infinite]" />
              <span className="w-2 h-2 bg-neutral-300 rounded-full animate-[pulse_1.4s_ease-in-out_0.4s_infinite]" />
            </div>
          </div>
        ) : task?.status === "cancelled" && clips.length === 0 ? (
          <Card><CardContent className="p-8 text-center space-y-3">
            <h2 className="text-xl font-semibold">Generation cancelled</h2>
            <p className="text-muted-foreground">Resume this generation when you are ready to continue.</p>
          </CardContent></Card>
        ) : task?.status === "error" ? (
          <Card>
            <CardContent className="p-8 text-center">
              <div className="text-red-600 mb-4">
                <AlertCircle className="w-12 h-12 mx-auto mb-2" />
                <h2 className="text-xl font-semibold">Processing Failed</h2>
              </div>
              <p className="text-gray-600 mb-4">There was an error processing your video. Please try again.</p>
              <Link href="/">
                <Button>
                  <ArrowLeft className="w-4 h-4" />
                  Back to Home
                </Button>
              </Link>
            </CardContent>
          </Card>
        ) : clips.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center">
              {task?.status === "completed" ? (
                <>
                  <div className="text-yellow-600 mb-4">
                    <AlertCircle className="w-12 h-12 mx-auto mb-2" />
                    <h2 className="text-xl font-semibold">No Clips Generated</h2>
                  </div>
                  <p className="text-gray-600 mb-4">
                    The generation completed but no clips were generated. The video may not have had suitable content for
                    clipping.
                  </p>
                  <Link href="/">
                    <Button>
                      <ArrowLeft className="w-4 h-4" />
                      Try Another Video
                    </Button>
                  </Link>
                </>
              ) : (
                <>
                  <div className="w-16 h-16 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-4">
                    <Clock className="w-8 h-8 text-blue-500 animate-pulse" />
                  </div>
                  <h2 className="text-xl font-semibold text-black mb-2">Still Generating...</h2>
                  <p className="text-gray-600">
                    Your clips are being generated. They will appear here as soon as they are ready.
                  </p>
                </>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-6">
            <div className="flex items-center justify-between">
              <Button variant="outline" size="sm" onClick={() => setSettingsSheetOpen(true)}>
                <Settings2 className="w-4 h-4" />
                Generation settings
              </Button>

            </div>

            <Sheet open={settingsSheetOpen} onOpenChange={setSettingsSheetOpen}>
              <SheetContent side="right" className="sm:max-w-md overflow-y-auto">
                <SheetHeader>
                  <SheetTitle className="flex items-center gap-2">
                    <Settings2 className="w-4 h-4" />
                    Generation settings
                  </SheetTitle>
                  <SheetDescription>
                    Configure font, caption, and cleanup settings for this generation&apos;s clips.
                  </SheetDescription>
                </SheetHeader>

                <div className="space-y-5 px-4">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-gray-500">Font</label>
                    <Select
                      value={projectFontFamily ?? FONT_TEMPLATE_DEFAULT_VALUE}
                      onValueChange={(value) =>
                        setProjectFontFamily(value === FONT_TEMPLATE_DEFAULT_VALUE ? null : value)
                      }
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Template default" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={FONT_TEMPLATE_DEFAULT_VALUE}>Template default</SelectItem>
                        {availableFonts.map((font) => (
                          <FontSelectOption
                            key={font.name}
                            font={font}
                            isDeleting={deletingFontName === font.name}
                            onDelete={setFontToDelete}
                          />
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <CaptionSizeControl value={projectFontSize} onChange={setProjectFontSize} disabled={isApplyingSettings} />

                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-medium text-gray-500">Color</label>
                      <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={projectFontColor === null}
                          onChange={(e) => setProjectFontColor(e.target.checked ? null : "#FFFFFF")}
                          className="rounded"
                        />
                        Template default
                      </label>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="color"
                        value={projectFontColor ?? "#FFFFFF"}
                        onChange={(e) => setProjectFontColor(e.target.value)}
                        disabled={projectFontColor === null}
                        className="h-9 w-9 rounded border border-gray-300 cursor-pointer disabled:cursor-not-allowed"
                      />
                      <Input
                        value={projectFontColor ?? ""}
                        onChange={(e) => setProjectFontColor(e.target.value)}
                        disabled={projectFontColor === null}
                        placeholder="Template default"
                      />
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-gray-500">Caption Template</label>
                    <Select value={projectCaptionTemplate} onValueChange={setProjectCaptionTemplate}>
                      <SelectTrigger>
                        <SelectValue>
                          {availableTemplates.find((t) => t.id === projectCaptionTemplate)?.name || "Select style"}
                        </SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {availableTemplates.map((template) => (
                          <SelectItem key={template.id} value={template.id}>
                            <div>
                              <div className="font-medium">{template.name}</div>
                              <div className="text-xs text-gray-500">{template.description}</div>
                            </div>
                          </SelectItem>
                        ))}
                        {availableTemplates.length === 0 && <SelectItem value="default">Default</SelectItem>}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="rounded-lg border bg-gray-50 p-3 space-y-3">
                    <div>
                      <div className="text-sm font-medium text-gray-900">Clip cleanup</div>
                      <div className="text-xs text-gray-500">Apply silence and filler-word cuts to regenerated clips.</div>
                    </div>

                    <label className="flex items-center gap-2 text-sm text-gray-700">
                      <input
                        type="checkbox"
                        checked={projectCutLongPauses}
                        onChange={(e) => setProjectCutLongPauses(e.target.checked)}
                        className="rounded"
                      />
                      Cut long pauses
                    </label>

                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-gray-500">Pause threshold (ms)</label>
                      <Input
                        type="number"
                        min={250}
                        max={3000}
                        step={50}
                        value={projectPauseThresholdMs}
                        onChange={(e) => setProjectPauseThresholdMs(e.target.value)}
                        disabled={!projectCutLongPauses}
                      />
                    </div>

                    <label className="flex items-center gap-2 text-sm text-gray-700">
                      <input
                        type="checkbox"
                        checked={projectRemoveFillerWords}
                        onChange={(e) => setProjectRemoveFillerWords(e.target.checked)}
                        className="rounded"
                      />
                      Remove filler words
                    </label>

                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-gray-500">Extra filtered words or phrases</label>
                      <Input
                        value={projectFilteredWords}
                        onChange={(e) => setProjectFilteredWords(e.target.value)}
                        placeholder="basically, literally, to be honest"
                      />
                    </div>
                  </div>
                </div>

                <SheetFooter>
                  <Button
                    className="w-full"
                    onClick={handleApplyProjectSettings}
                    disabled={isApplyingSettings}
                  >
                    {isApplyingSettings ? "Applying..." : "Apply to All Clips"}
                  </Button>
                </SheetFooter>
              </SheetContent>
            </Sheet>

            {clips.map((clip) => (
              <Card key={clip.id} className="overflow-hidden">
                <CardContent className="p-0">
                  <div className="flex flex-col lg:flex-row">
                    {/* Video Player */}
                    <div className="relative flex-shrink-0 bg-black rounded-lg overflow-hidden m-3">
                      <DynamicVideoPlayer src={getClipUrl(clip.video_url, clip.filename)} />
                    </div>

                    {/* Clip Details */}
                    <div className="p-6 flex-1">
                      <div className="flex items-start justify-between mb-4">
                        <div>
                          <h3 className="font-semibold text-lg text-black mb-1">
                            {clip.hook_title || `Clip ${clip.clip_order}`}
                          </h3>
                          <div className="flex items-center gap-2 text-sm text-gray-600">
                            <span>Clip {clip.clip_order}</span>
                            <span>•</span>
                            <span>
                              {clip.start_time} - {clip.end_time}
                            </span>
                            <span>•</span>
                            <span>{formatDuration(clip.duration)}</span>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {/* Virality Score Badge */}
                          {clip.virality_score > 0 && (
                            <Badge className={`${getViralityBgColor(clip.virality_score)} text-white`}>
                              <Zap className="w-3 h-3 mr-1" />
                              {clip.virality_score}
                            </Badge>
                          )}
                          <Badge className={getScoreColor(clip.relevance_score)}>
                            <Star className="w-3 h-3 mr-1" />
                            {(clip.relevance_score * 100).toFixed(0)}%
                          </Badge>
                        </div>
                      </div>

                      {/* Virality Score Breakdown */}
                      {clip.virality_score > 0 && (
                        <div className="mb-4 p-3 bg-gray-50 rounded-lg">
                          <div className="flex items-center justify-between mb-3">
                            <h4 className="font-medium text-black text-sm flex items-center gap-2">
                              <Zap className="w-4 h-4" />
                              Virality Score
                            </h4>
                            <span className={`text-lg font-bold ${getViralityColor(clip.virality_score)}`}>
                              {clip.virality_score}/100
                            </span>
                          </div>

                          <div className="grid grid-cols-2 gap-3 text-xs">
                            {/* Hook Score */}
                            <div className="space-y-1">
                              <div className="flex items-center justify-between">
                                <span className="flex items-center gap-1 text-gray-600">
                                  <MessageSquare className="w-3 h-3" />
                                  Hook
                                </span>
                                <span className="font-medium">{clip.hook_score}/25</span>
                              </div>
                              <Progress value={(clip.hook_score / 25) * 100} className="h-1.5" />
                            </div>

                            {/* Engagement Score */}
                            <div className="space-y-1">
                              <div className="flex items-center justify-between">
                                <span className="flex items-center gap-1 text-gray-600">
                                  <TrendingUp className="w-3 h-3" />
                                  Engagement
                                </span>
                                <span className="font-medium">{clip.engagement_score}/25</span>
                              </div>
                              <Progress value={(clip.engagement_score / 25) * 100} className="h-1.5" />
                            </div>

                            {/* Value Score */}
                            <div className="space-y-1">
                              <div className="flex items-center justify-between">
                                <span className="flex items-center gap-1 text-gray-600">
                                  <Star className="w-3 h-3" />
                                  Value
                                </span>
                                <span className="font-medium">{clip.value_score}/25</span>
                              </div>
                              <Progress value={(clip.value_score / 25) * 100} className="h-1.5" />
                            </div>

                            {/* Shareability Score */}
                            <div className="space-y-1">
                              <div className="flex items-center justify-between">
                                <span className="flex items-center gap-1 text-gray-600">
                                  <Share2 className="w-3 h-3" />
                                  Shareability
                                </span>
                                <span className="font-medium">{clip.shareability_score}/25</span>
                              </div>
                              <Progress value={(clip.shareability_score / 25) * 100} className="h-1.5" />
                            </div>
                          </div>

                          {clip.hook_type && clip.hook_type !== "none" && (
                            <div className="mt-3 pt-2 border-t">
                              <Badge variant="outline" className="text-xs">
                                {getHookTypeLabel(clip.hook_type)}
                              </Badge>
                            </div>
                          )}
                        </div>
                      )}

                      {clip.text && (
                        <TranscriptPreview text={clip.text} clipTitle={`Clip ${clip.clip_order}`} />
                      )}

                      <div className="flex items-center gap-2">
                        <div className="inline-flex items-stretch h-8 rounded-md border border-input bg-background shadow-xs overflow-hidden">
                          <button
                            type="button"
                            disabled={pendingAction === clip.id}
                            onClick={() => handleDownloadClip(clip)}
                            className="inline-flex items-center gap-1.5 px-3 text-sm font-medium hover:bg-accent transition-colors focus-visible:outline-none focus-visible:bg-accent"
                          >
                            <Download className="w-4 h-4" />
                            Download
                          </button>
                          <Select value={exportPreset} onValueChange={setExportPreset}>
                            <SelectTrigger
                              size="sm"
                              aria-label="Download format"
                              className="h-8 min-w-[112px] rounded-none border-0 border-l border-input shadow-none focus-visible:ring-0 focus-visible:border-input bg-transparent"
                            >
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent align="end">
                              <SelectItem value="original">Original</SelectItem>
                              {EXPORT_PRESETS.map((preset) => <SelectItem key={preset.id} value={preset.id}>{preset.label}</SelectItem>)}
                            </SelectContent>
                          </Select>
                        </div>

                        <Button size="sm" variant="outline" asChild>
                          <Link href={`/tasks/${task.id}/edit?clip=${clip.id}`}><Scissors className="w-4 h-4" />Edit</Link>
                        </Button>

                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label="Delete clip"
                          className="ml-auto text-red-600 hover:text-red-700 hover:bg-red-50"
                          onClick={() => setDeletingClipId(clip.id)}
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>


                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <AlertDialog open={fontToDelete !== null} onOpenChange={(open) => { if (!open) setFontToDelete(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete font?</AlertDialogTitle>
            <AlertDialogDescription>Delete {fontToDelete?.display_name}? This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Keep font</AlertDialogCancel>
            <AlertDialogAction onClick={() => { if (fontToDelete) void handleDeleteFont(fontToDelete); setFontToDelete(null); }}>Delete font</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {/* Delete generation confirmation */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Generation</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this generation? This will permanently delete all clips and cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteTask} disabled={isDeleting} className="bg-red-600 hover:bg-red-700">
              {isDeleting ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Clip Confirmation Dialog */}
      <AlertDialog open={!!deletingClipId} onOpenChange={(open) => !open && setDeletingClipId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Clip</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this clip? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deletingClipId && handleDeleteClip(deletingClipId)}
              className="bg-red-600 hover:bg-red-700"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
