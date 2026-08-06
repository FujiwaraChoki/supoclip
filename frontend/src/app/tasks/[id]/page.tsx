"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { buildFontOptionsPayload, FONT_SIZE_OPTIONS, FONT_TEMPLATE_DEFAULT_VALUE } from "@/lib/font-options";
import {
  ArrowLeft,
  AlertCircle,
  Trash2,
  Edit2,
  X,
  Check,
  CheckSquare,
  Loader2,
  Share2,
  Link2Off,
  Clock,
  GitMerge,
  RefreshCw,
  Settings2,
  Clapperboard,
} from "lucide-react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import DynamicVideoPlayer from "@/components/dynamic-video-player";
import { FontSelectOption, type FontOption } from "@/components/font-select-option";
import { ClipCard } from "./_components/clip-card";
import { DownloadSplitButton } from "./_components/download-split-button";
import { PipelineProgress } from "./_components/pipeline-progress";
import { Clip, formatDuration, getViralityBgColor } from "./_components/clip-format";

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
  output_format?: string | null;
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

const TERMINAL_STATUSES = ["completed", "error", "cancelled"];

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
  const [isDeletingClip, setIsDeletingClip] = useState(false);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedClipIds, setSelectedClipIds] = useState<string[]>([]);
  const [isMerging, setIsMerging] = useState(false);
  const [exportPresets, setExportPresets] = useState<Record<string, string>>({});
  const [downloadingClipIds, setDownloadingClipIds] = useState<string[]>([]);
  const [isDownloadingAll, setIsDownloadingAll] = useState(false);
  const [bulkPreset, setBulkPreset] = useState("original");
  const [shareState, setShareState] = useState<"idle" | "copying" | "copied">("idle");
  const [isRevokingShare, setIsRevokingShare] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isResuming, setIsResuming] = useState(false);
  const [fontPendingDelete, setFontPendingDelete] = useState<FontOption | null>(null);

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
  // Once a task has loaded, a failed refetch is transient — keep the view and toast instead.
  const hasLoadedTaskRef = useRef(false);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const taskApiUrl = "/api/tasks";
  const getClipUrl = (videoUrl: string) =>
    videoUrl.startsWith("/api/") ? videoUrl : `/api${videoUrl}`;

  const buildSupportError = useCallback(async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  }, []);

  const fetchTaskStatus = useCallback(
    async (retryCount = 0, maxRetries = 5): Promise<boolean> => {
      if (!params.id) return false;

      try {
        const taskResponse = await fetch(`${taskApiUrl}/${params.id}`, {
          cache: "no-store",
        });

        // A freshly created task may not be persisted yet — retry before giving up.
        if (taskResponse.status === 404) {
          if (retryCount < maxRetries) {
            await new Promise((resolve) => setTimeout(resolve, (retryCount + 1) * 500));
            return fetchTaskStatus(retryCount + 1, maxRetries);
          }
          setError("This generation could not be found. It may have been deleted.");
          return false;
        }

        if (!taskResponse.ok) {
          throw new Error(await buildSupportError(taskResponse, `Failed to fetch task: ${taskResponse.status}`));
        }

        const taskData = await taskResponse.json();
        hasLoadedTaskRef.current = true;
        setError(null);
        setTask(taskData);
        if (typeof taskData.progress === "number") {
          setProgress((prev) => Math.max(prev, taskData.progress));
        }
        if (taskData.progress_message) {
          setProgressMessage(taskData.progress_message);
        }
        setProjectFontFamily(taskData.font_family ?? null);
        setProjectFontSize(typeof taskData.font_size === "number" ? taskData.font_size : null);
        setProjectFontColor(taskData.font_color ?? null);
        setProjectCaptionTemplate(taskData.caption_template || "default");
        setProjectCutLongPauses(Boolean(taskData.cut_long_pauses));
        setProjectPauseThresholdMs(String(taskData.pause_threshold_ms || 900));
        setProjectRemoveFillerWords(Boolean(taskData.remove_filler_words));
        setProjectFilteredWords((taskData.filtered_words || []).join(", "));

        // Fetch clips if task is completed or processing (incremental clips)
        if (taskData.status === "completed" || taskData.status === "processing") {
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

        return true;
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load task";
        if (hasLoadedTaskRef.current) {
          toast.error(message);
        } else {
          setError(message);
        }
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
      } catch {
        // Font list is optional — the template default still works.
      }
    };

    void loadFonts();

    const loadTemplates = async () => {
      try {
        const response = await fetch(`${apiUrl}/caption-templates`);
        if (response.ok) {
          const data = await response.json();
          setAvailableTemplates(data.templates || []);
        }
      } catch {
        // Template list is optional — the default template still works.
      }
    };
    void loadTemplates();
  }, [apiUrl]);

  // SSE effect — real-time progress with reconnect + polling fallback
  useEffect(() => {
    const taskStatus = task?.status;
    if (!params.id || !taskStatus) return;

    // Only connect to SSE if task is queued or processing
    if (taskStatus !== "queued" && taskStatus !== "processing") return;

    let stopped = false;
    let eventSource: EventSource | null = null;
    let reconnectTimer: number | undefined;
    let pollTimer: number | undefined;
    let attempt = 0;

    const parseEvent = (raw: unknown): Record<string, unknown> | null => {
      if (typeof raw !== "string" || raw.length === 0) return null;
      try {
        return JSON.parse(raw) as Record<string, unknown>;
      } catch {
        // A malformed frame must not take the stream down.
        return null;
      }
    };

    const stopPolling = () => {
      if (pollTimer !== undefined) {
        window.clearInterval(pollTimer);
        pollTimer = undefined;
      }
    };

    const startPolling = () => {
      if (pollTimer !== undefined || stopped) return;
      pollTimer = window.setInterval(() => {
        void fetchTaskStatus();
      }, 5000);
    };

    const teardown = () => {
      stopped = true;
      stopPolling();
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      eventSource?.close();
      eventSource = null;
    };

    const applyProgress = (data: Record<string, unknown>) => {
      if (typeof data.progress === "number") setProgress(data.progress);
      if (typeof data.message === "string") setProgressMessage(data.message);
    };

    const finish = () => {
      teardown();
      void fetchTaskStatus();
    };

    const scheduleReconnect = () => {
      if (stopped) return;
      startPolling();
      const delay = Math.min(15000, 1000 * 2 ** attempt);
      attempt += 1;
      reconnectTimer = window.setTimeout(connect, delay);
    };

    function connect() {
      if (stopped) return;
      const source = new EventSource(`${taskApiUrl}/${params.id}/progress`);
      eventSource = source;

      source.addEventListener("open", () => {
        attempt = 0;
        stopPolling();
      });

      source.addEventListener("status", (e) => {
        const data = parseEvent((e as MessageEvent).data);
        if (!data) return;
        applyProgress(data);
        if (typeof data.status === "string" && TERMINAL_STATUSES.includes(data.status)) {
          finish();
        }
      });

      source.addEventListener("progress", (e) => {
        const data = parseEvent((e as MessageEvent).data);
        if (!data) return;
        applyProgress(data);

        if (typeof data.status === "string") {
          const nextStatus = data.status;
          setTask((currentTask) => (currentTask ? { ...currentTask, status: nextStatus } : currentTask));
          if (TERMINAL_STATUSES.includes(nextStatus)) {
            finish();
          }
        }
      });

      source.addEventListener("clip_ready", (e) => {
        const data = parseEvent((e as MessageEvent).data);
        const incoming = data?.clip as Clip | undefined;
        if (!incoming) return;
        setClips((prev) => {
          if (prev.some((c) => c.id === incoming.id)) return prev;
          return [...prev, incoming].sort((a, b) => (a.clip_order ?? 0) - (b.clip_order ?? 0));
        });
      });

      source.addEventListener("close", () => {
        finish();
      });

      source.addEventListener("error", (e) => {
        const data = parseEvent((e as MessageEvent<string>).data);
        if (data) {
          // Server-sent failure: surface it and stop retrying.
          toast.error(typeof data.error === "string" ? data.error : "Processing failed");
          finish();
          return;
        }
        // Transport failure: back off, reconnect, and poll meanwhile.
        source.close();
        if (eventSource === source) eventSource = null;
        scheduleReconnect();
      });
    }

    connect();

    return teardown;
  }, [params.id, task?.status, fetchTaskStatus, taskApiUrl]);

  const sortedClips = useMemo(
    () =>
      [...clips].sort(
        (a, b) =>
          (b.virality_score ?? 0) - (a.virality_score ?? 0) ||
          (a.clip_order ?? 0) - (b.clip_order ?? 0),
      ),
    [clips],
  );

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
        toast.success("Title updated");
      } else {
        toast.error(await buildSupportError(response, "Failed to update title"));
      }
    } catch (err) {
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
        toast.success("Generation deleted");
        router.push("/list");
      } else {
        toast.error(await buildSupportError(response, "Failed to delete task"));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete task");
    } finally {
      setIsDeleting(false);
      setShowDeleteDialog(false);
    }
  };

  const handleDeleteClip = async (clipId: string) => {
    if (!session?.user?.id || !params.id) return;

    setIsDeletingClip(true);
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}`, {
        method: "DELETE",
      });

      if (response.ok) {
        setClips((prev) => prev.filter((clip) => clip.id !== clipId));
        setSelectedClipIds((prev) => prev.filter((id) => id !== clipId));
        setDeletingClipId(null);
        toast.success("Clip deleted");
      } else {
        toast.error(await buildSupportError(response, "Failed to delete clip"));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete clip");
    } finally {
      setIsDeletingClip(false);
    }
  };

  const handleToggleClipSelection = (clipId: string) => {
    setSelectedClipIds((prev) => {
      if (prev.includes(clipId)) {
        return prev.filter((id) => id !== clipId);
      }
      return [...prev, clipId];
    });
  };

  const handleToggleSelectMode = () => {
    setSelectMode((prev) => {
      if (prev) setSelectedClipIds([]);
      return !prev;
    });
  };

  const handleTrimClip = async (clipId: string, startOffset: string, endOffset: string) => {
    if (!session?.user?.id || !params.id) return;
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          start_offset: Number(startOffset || "0"),
          end_offset: Number(endOffset || "0"),
        }),
      });
      if (!response.ok) {
        toast.error(await buildSupportError(response, "Failed to trim clip"));
        return;
      }
      toast.success("Clip trimmed");
      await fetchTaskStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to trim clip");
    }
  };

  const handleSplitClip = async (clipId: string, splitTime: string) => {
    if (!session?.user?.id || !params.id) return;
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}/split`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ split_time: Number(splitTime || "5") }),
      });
      if (!response.ok) {
        toast.error(await buildSupportError(response, "Failed to split clip"));
        return;
      }
      toast.success("Clip split");
      await fetchTaskStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to split clip");
    }
  };

  const handleMergeClips = async () => {
    if (!session?.user?.id || !params.id || selectedClipIds.length < 2 || isMerging) return;
    setIsMerging(true);
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}/clips/merge`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ clip_ids: selectedClipIds }),
      });
      if (!response.ok) {
        toast.error(await buildSupportError(response, "Failed to merge clips"));
        return;
      }
      setSelectedClipIds([]);
      setSelectMode(false);
      toast.success("Clips merged");
      await fetchTaskStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to merge clips");
    } finally {
      setIsMerging(false);
    }
  };

  const handleUpdateCaptions = async (
    clipId: string,
    values: { text: string; position: string; highlightWords: string },
  ) => {
    if (!session?.user?.id || !params.id) return;
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}/captions`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          caption_text: values.text,
          position: values.position,
          highlight_words: values.highlightWords
            .split(",")
            .map((w) => w.trim())
            .filter(Boolean),
        }),
      });
      if (!response.ok) {
        toast.error(await buildSupportError(response, "Failed to update captions"));
        return;
      }
      toast.success("Captions updated");
      await fetchTaskStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update captions");
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
      toast.success("Settings applied to all clips");
      await fetchTaskStatus();
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
      toast.success(`Deleted ${font.display_name}`);
    } catch (deleteError) {
      toast.error(deleteError instanceof Error ? deleteError.message : "Failed to delete font");
    } finally {
      setDeletingFontName(null);
      setFontPendingDelete(null);
    }
  };

  const triggerBrowserDownload = (href: string, filename: string) => {
    const link = document.createElement("a");
    link.href = href;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const exportPresetFor = useCallback(
    (clipId: string) => exportPresets[clipId] ?? "original",
    [exportPresets],
  );

  /** Returns null on success, or the failure message so callers can report accurately. */
  const handleExportClip = async (
    clip: Clip,
    preset: string,
    { notify = true }: { notify?: boolean } = {},
  ): Promise<string | null> => {
    if (!task?.id) return "No task loaded";

    setDownloadingClipIds((prev) => [...prev, clip.id]);
    try {
      const response = await fetch(`${taskApiUrl}/${task.id}/clips/${clip.id}/export?preset=${preset}`, {
        cache: "no-store",
      });

      if (!response.ok) {
        const message = await buildSupportError(response, "Failed to export clip");
        if (notify) toast.error(message);
        return message;
      }

      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      triggerBrowserDownload(blobUrl, `${clip.filename.replace(/\.mp4$/i, "")}_${preset}.mp4`);
      URL.revokeObjectURL(blobUrl);
      return null;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to export clip";
      if (notify) toast.error(message);
      return message;
    } finally {
      setDownloadingClipIds((prev) => prev.filter((id) => id !== clip.id));
    }
  };

  const handleDownloadClip = (clip: Clip, preset = exportPresetFor(clip.id)) => {
    if (downloadingClipIds.includes(clip.id)) return;
    if (preset === "original") {
      triggerBrowserDownload(getClipUrl(clip.video_url), clip.filename);
      return;
    }
    void handleExportClip(clip, preset);
  };

  const handleDownloadAll = async () => {
    if (isDownloadingAll || sortedClips.length === 0 || !task?.id) return;

    setIsDownloadingAll(true);
    const toastId = toast.loading("Preparing download…");
    try {
      if (bulkPreset === "original") {
        // The workflows export job bundles the originals into a single archive.
        try {
          const response = await fetch("/api/workflows/exports", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ task_id: task.id, export_type: "zip" }),
          });
          if (response.ok) {
            const data = (await response.json()) as { file_url?: string };
            if (data.file_url) {
              const href = data.file_url.startsWith("/api/") ? data.file_url : `/api${data.file_url}`;
              triggerBrowserDownload(href, `${task.source_title || "clips"}.zip`);
              toast.success("Download started", { id: toastId });
              return;
            }
          }
        } catch {
          // Fall through to per-clip downloads.
        }
      }

      toast.loading(`Downloading ${sortedClips.length} clips…`, { id: toastId });
      const failures: string[] = [];
      for (const clip of sortedClips) {
        if (bulkPreset === "original") {
          triggerBrowserDownload(getClipUrl(clip.video_url), clip.filename);
          await new Promise((resolve) => setTimeout(resolve, 600));
        } else {
          // Report once at the end rather than one toast per failed clip.
          const failure = await handleExportClip(clip, bulkPreset, { notify: false });
          if (failure) failures.push(failure);
        }
      }

      const succeeded = sortedClips.length - failures.length;
      if (failures.length > 0) {
        toast.error(
          `Downloaded ${succeeded} of ${sortedClips.length} clips — ${failures.length} failed`,
          { id: toastId, description: failures[0] },
        );
      } else {
        toast.success(`Downloaded ${succeeded} clips`, { id: toastId });
      }
    } finally {
      setIsDownloadingAll(false);
    }
  };

  const handleCancelTask = async () => {
    if (!task?.id || isCancelling) return;
    setIsCancelling(true);
    try {
      const response = await fetch(`${taskApiUrl}/${task.id}/cancel`, { method: "POST" });
      if (!response.ok) {
        toast.error(await buildSupportError(response, "Failed to cancel generation"));
        return;
      }
      toast.success("Generation cancelled");
      await fetchTaskStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to cancel generation");
    } finally {
      setIsCancelling(false);
    }
  };

  const handleResumeTask = async () => {
    if (!task?.id || isResuming) return;
    setIsResuming(true);
    try {
      const response = await fetch(`${taskApiUrl}/${task.id}/resume`, { method: "POST" });
      if (!response.ok) {
        toast.error(await buildSupportError(response, "Failed to resume generation"));
        return;
      }
      toast.success("Generation resumed");
      await fetchTaskStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to resume generation");
    } finally {
      setIsResuming(false);
    }
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
      toast.success("Share link copied to clipboard");
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
      toast.success("Share link disabled");
    } catch (revokeError) {
      toast.error(revokeError instanceof Error ? revokeError.message : "Failed to disable share link");
    } finally {
      setIsRevokingShare(false);
    }
  };

  if (isLoading) {
    return (
      <AppShell back={{ href: "/list", label: "My Clips" }}>
        <div className="max-w-6xl mx-auto p-4">
          <div className="mb-6">
            <Skeleton className="h-8 w-48 mb-2" />
            <Skeleton className="h-4 w-96" />
          </div>
          <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3">
            {[1, 2, 3].map((i) => (
              <Card key={i}>
                <CardContent className="p-6">
                  <Skeleton className="h-48 w-full mb-4" />
                  <Skeleton className="h-4 w-full mb-2" />
                  <Skeleton className="h-4 w-3/4" />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </AppShell>
    );
  }

  if (error && !task) {
    return (
      <div className="min-h-screen bg-background p-4">
        <div className="max-w-6xl mx-auto">
          <Alert>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
          <Link href="/" className="mt-4 inline-block">
            <Button variant="outline">
              <ArrowLeft className="w-4 h-4" />
              Back to Home
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <AppShell back={{ href: "/list", label: "My Clips" }}>
      {/* Header */}
      <div className="border-b bg-background">
        <div className="max-w-6xl mx-auto px-4 py-6">
          {task && (
            <div>
              <div className="flex items-center gap-3 mb-2">
                {isEditing ? (
                  <div className="flex items-center gap-2 flex-1">
                    <Input
                      value={editedTitle}
                      onChange={(e) => setEditedTitle(e.target.value)}
                      className="text-2xl font-bold h-auto py-1"
                      aria-label="Generation title"
                      autoFocus
                    />
                    <Button size="sm" onClick={handleEditTitle} disabled={!editedTitle.trim()} aria-label="Save title">
                      <Check className="w-4 h-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label="Cancel editing title"
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
                    <h1 className={`text-2xl font-bold text-foreground ${task.status === "processing" || task.status === "queued" ? "shimmer" : ""}`}>{task.source_title}</h1>
                    <div className="flex items-center gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label="Rename generation"
                        onClick={() => {
                          setIsEditing(true);
                          setEditedTitle(task.source_title);
                        }}
                      >
                        <Edit2 className="w-4 h-4" aria-hidden="true" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label="Delete generation"
                        className="text-red-700 hover:text-red-800 hover:bg-red-50"
                        onClick={() => setShowDeleteDialog(true)}
                      >
                        <Trash2 className="w-4 h-4" aria-hidden="true" />
                      </Button>
                    </div>
                  </>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
                <Badge variant="outline" className="capitalize">
                  {task.source_type}
                </Badge>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="flex items-center gap-1 cursor-default">
                        <Clock className="w-4 h-4" aria-hidden="true" />
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
                {task.status === "completed" ? (
                  <span>
                    {clips.length} {clips.length === 1 ? "clip" : "clips"} generated
                  </span>
                ) : task.status === "processing" ? (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge className="bg-stone-800 text-white cursor-default shimmer">Processing</Badge>
                      </TooltipTrigger>
                      <TooltipContent>
                        We&apos;re currently processing your video. Check back in a couple minutes.
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : task.status === "queued" ? (
                  <Badge variant="outline">Queued</Badge>
                ) : (
                  <Badge variant="outline" className="capitalize">
                    {task.status}
                  </Badge>
                )}
                {task.status === "completed" && clips.length > 0 && (
                  <Link href={`/tasks/${task.id}/edit`}>
                    <Button size="sm" variant="outline">
                      <Clapperboard className="w-4 h-4" aria-hidden="true" />
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
                      <Check className="w-4 h-4" aria-hidden="true" />
                    ) : (
                      <Share2 className="w-4 h-4" aria-hidden="true" />
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
                    <Link2Off className="w-4 h-4" aria-hidden="true" />
                    {isRevokingShare ? "Disabling…" : "Disable share link"}
                  </Button>
                )}
                {(task.status === "cancelled" || task.status === "error") && (
                  <Button size="sm" variant="outline" onClick={handleResumeTask} disabled={isResuming}>
                    {isResuming ? (
                      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <RefreshCw className="w-4 h-4" aria-hidden="true" />
                    )}
                    {isResuming ? "Resuming…" : "Resume"}
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-6xl mx-auto px-4 py-8">
        {task?.status === "processing" || task?.status === "queued" ? (
          <div className="space-y-8">
            <PipelineProgress
              status={task.status}
              progress={progress}
              message={progressMessage}
              clipsReady={clips.length}
              onCancel={handleCancelTask}
              isCancelling={isCancelling}
            />

            {/* Live clips grid — shows clips as they render */}
            {clips.length > 0 && (
              <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
                {sortedClips.map((clip) => (
                  <Card key={clip.id} className="overflow-hidden py-0 gap-0">
                    <CardContent className="p-0">
                      <div className="relative bg-black">
                        <DynamicVideoPlayer
                          src={getClipUrl(clip.video_url)}
                          poster="/placeholder-video.jpg"
                          outputFormat={task.output_format}
                          sizing="fill"
                          className="rounded-none"
                        />
                        {clip.virality_score > 0 && (
                          <Badge
                            className={`absolute top-2 right-2 ${getViralityBgColor(clip.virality_score)} text-white`}
                          >
                            {clip.virality_score}
                          </Badge>
                        )}
                      </div>
                      <div className="p-4">
                        <h3 className="font-semibold text-foreground line-clamp-2">
                          {clip.hook_title || `Clip ${clip.clip_order}`}
                        </h3>
                        <p className="text-xs text-muted-foreground mt-1">
                          {formatDuration(clip.duration)}
                        </p>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </div>
        ) : !task ? (
          <div className="flex flex-col items-center justify-center min-h-[50vh] py-16">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" aria-label="Loading generation" />
          </div>
        ) : task.status === "error" || task.status === "cancelled" ? (
          <Card>
            <CardContent className="p-8 text-center">
              <div className="text-amber-700 mb-4">
                <AlertCircle className="w-12 h-12 mx-auto mb-2" aria-hidden="true" />
                <h2 className="text-xl font-semibold">
                  {task.status === "cancelled" ? "Generation cancelled" : "Processing failed"}
                </h2>
              </div>
              <p className="text-muted-foreground mb-6 max-w-xl mx-auto">
                {task.progress_message ||
                  (task.status === "cancelled"
                    ? "This generation was cancelled before it finished."
                    : "There was an error processing your video. Please try again.")}
              </p>
              <div className="flex items-center justify-center gap-2">
                <Button onClick={handleResumeTask} disabled={isResuming}>
                  {isResuming ? (
                    <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <RefreshCw className="w-4 h-4" aria-hidden="true" />
                  )}
                  {isResuming ? "Retrying…" : "Retry generation"}
                </Button>
                <Link href="/">
                  <Button variant="outline">
                    <ArrowLeft className="w-4 h-4" aria-hidden="true" />
                    Back to Home
                  </Button>
                </Link>
              </div>
            </CardContent>
          </Card>
        ) : clips.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center">
              {task.status === "completed" ? (
                <>
                  <div className="text-amber-700 mb-4">
                    <AlertCircle className="w-12 h-12 mx-auto mb-2" aria-hidden="true" />
                    <h2 className="text-xl font-semibold">No clips generated</h2>
                  </div>
                  <p className="text-muted-foreground mb-4">
                    The task completed but no clips were generated. The video may not have had suitable content for
                    clipping.
                  </p>
                  <Link href="/">
                    <Button>
                      <ArrowLeft className="w-4 h-4" aria-hidden="true" />
                      Try Another Video
                    </Button>
                  </Link>
                </>
              ) : (
                <>
                  <Loader2 className="w-8 h-8 mx-auto mb-4 animate-spin text-muted-foreground" aria-hidden="true" />
                  <h2 className="text-xl font-semibold text-foreground mb-2">Still generating…</h2>
                  <p className="text-muted-foreground">
                    Your clips are being generated. They&apos;ll appear here as soon as they&apos;re ready.
                  </p>
                </>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" onClick={() => setSettingsSheetOpen(true)}>
                  <Settings2 className="w-4 h-4" aria-hidden="true" />
                  Project Settings
                </Button>
                <Button asChild variant="outline" size="sm">
                  <Link href={`/tasks/${params.id}/workflows`}>
                    <Share2 className="w-4 h-4" aria-hidden="true" />
                    Workflows
                  </Link>
                </Button>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant={selectMode ? "default" : "outline"}
                  size="sm"
                  aria-pressed={selectMode}
                  onClick={handleToggleSelectMode}
                >
                  <CheckSquare className="w-4 h-4" aria-hidden="true" />
                  {selectMode ? "Done selecting" : "Select clips"}
                </Button>
                <DownloadSplitButton
                  preset={bulkPreset}
                  onPresetChange={setBulkPreset}
                  onDownload={handleDownloadAll}
                  isPending={isDownloadingAll}
                  targetLabel={`all ${sortedClips.length} clips`}
                />
              </div>
            </div>

            <Sheet open={settingsSheetOpen} onOpenChange={setSettingsSheetOpen}>
              <SheetContent side="right" className="sm:max-w-md overflow-y-auto">
                <SheetHeader>
                  <SheetTitle className="flex items-center gap-2">
                    <Settings2 className="w-4 h-4" aria-hidden="true" />
                    Project Settings
                  </SheetTitle>
                  <SheetDescription>
                    Configure font, caption, and cleanup settings for this task&apos;s clips.
                  </SheetDescription>
                </SheetHeader>

                <div className="space-y-5 px-4">
                  <div className="space-y-1.5">
                    <label htmlFor="project-font" className="text-xs font-medium text-muted-foreground">
                      Font
                    </label>
                    <Select
                      value={projectFontFamily ?? FONT_TEMPLATE_DEFAULT_VALUE}
                      onValueChange={(value) =>
                        setProjectFontFamily(value === FONT_TEMPLATE_DEFAULT_VALUE ? null : value)
                      }
                    >
                      <SelectTrigger id="project-font">
                        <SelectValue placeholder="Template default" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={FONT_TEMPLATE_DEFAULT_VALUE}>Template default</SelectItem>
                        {availableFonts.map((font) => (
                          <FontSelectOption
                            key={font.name}
                            font={font}
                            isDeleting={deletingFontName === font.name}
                            onDelete={setFontPendingDelete}
                          />
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-1.5">
                    <span className="text-xs font-medium text-muted-foreground" id="project-font-size-label">
                      Size
                    </span>
                    <div className="grid grid-cols-4 gap-1.5" role="group" aria-labelledby="project-font-size-label">
                      {FONT_SIZE_OPTIONS.map((option) => (
                        <button
                          key={option.label}
                          type="button"
                          aria-pressed={projectFontSize === option.value}
                          onClick={() => setProjectFontSize(option.value)}
                          className={`px-2 py-1.5 rounded-md text-xs font-medium border transition-colors ${
                            projectFontSize === option.value
                              ? "bg-stone-900 text-white border-stone-900"
                              : "bg-background text-stone-600 border-stone-300 hover:bg-stone-50"
                          }`}
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <label htmlFor="project-font-color" className="text-xs font-medium text-muted-foreground">
                        Color
                      </label>
                      <div className="flex items-center gap-1.5">
                        <Checkbox
                          id="project-font-color-default"
                          checked={projectFontColor === null}
                          onCheckedChange={(checked) => setProjectFontColor(checked === true ? null : "#FFFFFF")}
                        />
                        <label
                          htmlFor="project-font-color-default"
                          className="text-xs text-muted-foreground cursor-pointer"
                        >
                          Template default
                        </label>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        id="project-font-color"
                        type="color"
                        value={projectFontColor ?? "#FFFFFF"}
                        onChange={(e) => setProjectFontColor(e.target.value)}
                        disabled={projectFontColor === null}
                        className="h-9 w-9 rounded border border-input cursor-pointer disabled:cursor-not-allowed"
                      />
                      <Input
                        value={projectFontColor ?? ""}
                        onChange={(e) => setProjectFontColor(e.target.value)}
                        disabled={projectFontColor === null}
                        aria-label="Font color hex value"
                        placeholder="Template default"
                      />
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <label htmlFor="project-caption-template" className="text-xs font-medium text-muted-foreground">
                      Caption Template
                    </label>
                    <Select value={projectCaptionTemplate} onValueChange={setProjectCaptionTemplate}>
                      <SelectTrigger id="project-caption-template">
                        <SelectValue>
                          {availableTemplates.find((t) => t.id === projectCaptionTemplate)?.name || "Select style"}
                        </SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {availableTemplates.map((template) => (
                          <SelectItem key={template.id} value={template.id}>
                            <div>
                              <div className="font-medium">{template.name}</div>
                              <div className="text-xs text-muted-foreground">{template.description}</div>
                            </div>
                          </SelectItem>
                        ))}
                        {availableTemplates.length === 0 && <SelectItem value="default">Default</SelectItem>}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="rounded-lg border border-border bg-muted/40 p-3 space-y-3">
                    <div>
                      <div className="text-sm font-medium text-foreground">Clip cleanup</div>
                      <div className="text-xs text-muted-foreground">
                        Apply silence and filler-word cuts to regenerated clips.
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <Checkbox
                        id="project-cut-pauses"
                        checked={projectCutLongPauses}
                        onCheckedChange={(checked) => setProjectCutLongPauses(checked === true)}
                      />
                      <label htmlFor="project-cut-pauses" className="text-sm text-foreground cursor-pointer">
                        Cut long pauses
                      </label>
                    </div>

                    <div className="space-y-1.5">
                      <label htmlFor="project-pause-threshold" className="text-xs font-medium text-muted-foreground">
                        Pause threshold (ms)
                      </label>
                      <Input
                        id="project-pause-threshold"
                        type="number"
                        min={250}
                        max={3000}
                        step={50}
                        value={projectPauseThresholdMs}
                        onChange={(e) => setProjectPauseThresholdMs(e.target.value)}
                        disabled={!projectCutLongPauses}
                      />
                    </div>

                    <div className="flex items-center gap-2">
                      <Checkbox
                        id="project-remove-fillers"
                        checked={projectRemoveFillerWords}
                        onCheckedChange={(checked) => setProjectRemoveFillerWords(checked === true)}
                      />
                      <label htmlFor="project-remove-fillers" className="text-sm text-foreground cursor-pointer">
                        Remove filler words
                      </label>
                    </div>

                    <div className="space-y-1.5">
                      <label htmlFor="project-filtered-words" className="text-xs font-medium text-muted-foreground">
                        Extra filtered words or phrases
                      </label>
                      <Input
                        id="project-filtered-words"
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
                    onClick={() => {
                      handleApplyProjectSettings();
                      setSettingsSheetOpen(false);
                    }}
                    disabled={isApplyingSettings}
                  >
                    {isApplyingSettings ? "Applying..." : "Apply to All Clips"}
                  </Button>
                </SheetFooter>
              </SheetContent>
            </Sheet>

            <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {sortedClips.map((clip) => (
                <ClipCard
                  key={clip.id}
                  clip={clip}
                  src={getClipUrl(clip.video_url)}
                  outputFormat={task.output_format}
                  selectMode={selectMode}
                  isSelected={selectedClipIds.includes(clip.id)}
                  onToggleSelect={handleToggleClipSelection}
                  exportPreset={exportPresetFor(clip.id)}
                  onExportPresetChange={(clipId, preset) =>
                    setExportPresets((prev) => ({ ...prev, [clipId]: preset }))
                  }
                  onDownload={handleDownloadClip}
                  isDownloading={downloadingClipIds.includes(clip.id)}
                  onDelete={setDeletingClipId}
                  onTrim={handleTrimClip}
                  onSplit={handleSplitClip}
                  onUpdateCaptions={handleUpdateCaptions}
                />
              ))}
            </div>

            {selectMode && (
              <div className="sticky bottom-4 z-10 flex justify-center">
                <div className="flex items-center gap-3 rounded-full border border-border bg-background px-4 py-2 shadow-lg">
                  <span className="text-sm text-muted-foreground" aria-live="polite">
                    {selectedClipIds.length} selected
                  </span>
                  <Button
                    size="sm"
                    onClick={handleMergeClips}
                    disabled={selectedClipIds.length < 2 || isMerging}
                  >
                    {isMerging ? (
                      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <GitMerge className="w-4 h-4" aria-hidden="true" />
                    )}
                    {isMerging ? "Merging…" : "Merge selected"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={handleToggleSelectMode}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Delete Task Confirmation Dialog */}
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
            <AlertDialogAction onClick={handleDeleteTask} disabled={isDeleting} className="bg-red-700 hover:bg-red-800">
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
            <AlertDialogCancel disabled={isDeletingClip}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deletingClipId && handleDeleteClip(deletingClipId)}
              disabled={isDeletingClip}
              className="bg-red-700 hover:bg-red-800"
            >
              {isDeletingClip ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Font Confirmation Dialog */}
      <AlertDialog
        open={!!fontPendingDelete}
        onOpenChange={(open) => !open && setFontPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete font</AlertDialogTitle>
            <AlertDialogDescription>
              Delete {fontPendingDelete?.display_name}? This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={!!deletingFontName}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => fontPendingDelete && handleDeleteFont(fontPendingDelete)}
              disabled={!!deletingFontName}
              className="bg-red-700 hover:bg-red-800"
            >
              {deletingFontName ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppShell>
  );
}
