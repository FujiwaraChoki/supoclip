"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
  Clock,
  Scissors,
  SplitSquareVertical,
  GitMerge,
  RefreshCw,
  Subtitles,
  Settings2,
  Type,
  Clapperboard,
} from "lucide-react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { Progress } from "@/components/ui/progress";
import Link from "next/link";
import DynamicVideoPlayer from "@/components/dynamic-video-player";

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
  font_family?: string;
  font_size?: number;
  font_color?: string;
  caption_template?: string;
  include_broll?: boolean;
}

interface FontOption {
  name: string;
  display_name: string;
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
  const [selectedClipIds, setSelectedClipIds] = useState<string[]>([]);
  const [editingClipId, setEditingClipId] = useState<string | null>(null);
  const [startOffset, setStartOffset] = useState("0");
  const [endOffset, setEndOffset] = useState("0");
  const [splitTime, setSplitTime] = useState("5");
  const [captionText, setCaptionText] = useState("");
  const [captionPosition, setCaptionPosition] = useState("bottom");
  const [highlightWords, setHighlightWords] = useState("");
  const [exportPreset, setExportPreset] = useState("tiktok");

  const [projectFontFamily, setProjectFontFamily] = useState("TikTokSans-Regular");
  const [projectFontSize, setProjectFontSize] = useState("24");
  const [projectFontColor, setProjectFontColor] = useState("#FFFFFF");
  const [projectCaptionTemplate, setProjectCaptionTemplate] = useState("default");
  const [projectIncludeBroll, setProjectIncludeBroll] = useState(false);
  const [isApplyingSettings, setIsApplyingSettings] = useState(false);
  const [settingsSheetOpen, setSettingsSheetOpen] = useState(false);
  const [availableFonts, setAvailableFonts] = useState<FontOption[]>([]);
  const [availableTemplates, setAvailableTemplates] = useState<
    Array<{ id: string; name: string; description: string; animation: string }>
  >([]);
  const hasTriggeredAutoRefresh = useRef(false);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const taskApiUrl = "/api/tasks";

  const buildSupportError = useCallback(async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  }, []);

  const triggerAutoRefresh = useCallback(() => {
    if (hasTriggeredAutoRefresh.current) return;
    hasTriggeredAutoRefresh.current = true;
    setTimeout(() => {
      window.location.reload();
    }, 700);
  }, []);

  const fetchTaskStatus = useCallback(
    async (retryCount = 0, maxRetries = 5) => {
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
          return fetchTaskStatus(retryCount + 1, maxRetries);
        }

        if (!taskResponse.ok) {
          throw new Error(await buildSupportError(taskResponse, `Failed to fetch task: ${taskResponse.status}`));
        }

        const taskData = await taskResponse.json();
        setTask(taskData);
        setProjectFontFamily(taskData.font_family || "TikTokSans-Regular");
        setProjectFontSize(String(taskData.font_size || 24));
        setProjectFontColor(taskData.font_color || "#FFFFFF");
        setProjectCaptionTemplate(taskData.caption_template || "default");
        setProjectIncludeBroll(Boolean(taskData.include_broll));

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
        console.error("Error fetching task data:", err);
        setError(err instanceof Error ? err.message : "Failed to load task");
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
        const response = await fetch(`${apiUrl}/caption-templates`);
        if (response.ok) {
          const data = await response.json();
          setAvailableTemplates(data.templates || []);
        }
      } catch (error) {
        console.error("Failed to load caption templates:", error);
      }
    };
    void loadTemplates();
  }, [apiUrl]);

  // SSE effect - real-time progress updates
  useEffect(() => {
    const taskStatus = task?.status;
    if (!params.id || !taskStatus) return;

    // Only connect to SSE if task is queued or processing
    if (taskStatus !== "queued" && taskStatus !== "processing") return;

    const eventSource = new EventSource(`${taskApiUrl}/${params.id}/progress`);

    console.log("📡 Connected to SSE for real-time progress");

    eventSource.addEventListener("status", (e) => {
      const data = JSON.parse(e.data);
      console.log("📊 Status:", data);
      setProgress(data.progress || 0);
      setProgressMessage(data.message || "");

      if (data.status === "completed") {
        void fetchTaskStatus().then(() => triggerAutoRefresh());
      }
    });

    eventSource.addEventListener("progress", (e) => {
      const data = JSON.parse(e.data);
      console.log("📈 Progress:", data);
      setProgress(data.progress || 0);
      setProgressMessage(data.message || "");

      // Update task status if provided
      if (data.status) {
        setTask((currentTask) => (currentTask ? { ...currentTask, status: data.status } : currentTask));

        if (data.status === "completed") {
          void fetchTaskStatus().then(() => triggerAutoRefresh());
        }
      }
    });

    eventSource.addEventListener("clip_ready", (e) => {
      const data = JSON.parse(e.data);
      console.log("🎬 Clip ready:", data.clip_index + 1, "/", data.total_clips);
      if (data.clip) {
        setClips((prev) => {
          const exists = prev.some((c: Clip) => c.id === data.clip.id);
          if (exists) return prev;
          return [...prev, data.clip].sort(
            (a: Clip, b: Clip) => (a.clip_order ?? 0) - (b.clip_order ?? 0),
          );
        });
      }
    });

    eventSource.addEventListener("close", async (e) => {
      const data = JSON.parse(e.data);
      console.log("✅ Task completed:", data.status);
      eventSource.close();

      // Refresh task and clips
      await fetchTaskStatus();
      triggerAutoRefresh();
    });

    eventSource.addEventListener("error", (e) => {
      console.error("❌ SSE error:", e);
      const maybeMessageEvent = e as MessageEvent<string>;
      if (typeof maybeMessageEvent.data === "string" && maybeMessageEvent.data.length > 0) {
        const data = JSON.parse(maybeMessageEvent.data);
        setError(data.error || "Connection error");
      }
      eventSource.close();
    });

    return () => {
      console.log("🔌 Disconnecting SSE");
      eventSource.close();
    };
  }, [params.id, task?.status, fetchTaskStatus, taskApiUrl, triggerAutoRefresh]); // Re-run when task status changes

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
        alert(await buildSupportError(response, "Failed to update title"));
      }
    } catch (err) {
      console.error("Error updating title:", err);
      alert(err instanceof Error ? err.message : "Failed to update title");
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
        alert(await buildSupportError(response, "Failed to delete task"));
      }
    } catch (err) {
      console.error("Error deleting task:", err);
      alert(err instanceof Error ? err.message : "Failed to delete task");
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
        alert(await buildSupportError(response, "Failed to delete clip"));
      }
    } catch (err) {
      console.error("Error deleting clip:", err);
      alert(err instanceof Error ? err.message : "Failed to delete clip");
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

  const handleTrimClip = async (clipId: string) => {
    if (!session?.user?.id || !params.id) return;
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
      alert(await buildSupportError(response, "Failed to trim clip"));
      return;
    }
    await fetchTaskStatus();
  };

  const handleSplitClip = async (clipId: string) => {
    if (!session?.user?.id || !params.id) return;
    const response = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}/split`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ split_time: Number(splitTime || "5") }),
    });
    if (!response.ok) {
      alert(await buildSupportError(response, "Failed to split clip"));
      return;
    }
    await fetchTaskStatus();
  };

  const handleMergeClips = async () => {
    if (!session?.user?.id || !params.id || selectedClipIds.length < 2) return;
    const response = await fetch(`${taskApiUrl}/${params.id}/clips/merge`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ clip_ids: selectedClipIds }),
    });
    if (!response.ok) {
      alert(await buildSupportError(response, "Failed to merge clips"));
      return;
    }
    setSelectedClipIds([]);
    await fetchTaskStatus();
  };

  const handleUpdateCaptions = async (clipId: string) => {
    if (!session?.user?.id || !params.id) return;
    const response = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}/captions`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        caption_text: captionText,
        position: captionPosition,
        highlight_words: highlightWords
          .split(",")
          .map((w) => w.trim())
          .filter(Boolean),
      }),
    });
    if (!response.ok) {
      alert(await buildSupportError(response, "Failed to update captions"));
      return;
    }
    await fetchTaskStatus();
  };

  const handleApplyProjectSettings = async () => {
    if (!session?.user?.id || !params.id) return;
    const parsedSize = Number(projectFontSize || "24");
    const safeFontSize = Number.isFinite(parsedSize) ? Math.max(12, Math.min(72, Math.round(parsedSize))) : 24;
    const normalizedColor = /^#[0-9A-Fa-f]{6}$/.test(projectFontColor) ? projectFontColor : "#FFFFFF";

    setIsApplyingSettings(true);
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}/settings`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          font_family: projectFontFamily,
          font_size: safeFontSize,
          font_color: normalizedColor,
          caption_template: projectCaptionTemplate,
          include_broll: projectIncludeBroll,
          apply_to_existing: true,
        }),
      });
      if (!response.ok) {
        alert(await buildSupportError(response, "Failed to apply settings"));
        return;
      }
      await fetchTaskStatus();
    } finally {
      setIsApplyingSettings(false);
    }
  };

  const handleExportClip = async (clipId: string, fallbackFilename: string) => {
    if (!session?.user?.id || !task?.id) return;

    const response = await fetch(`${taskApiUrl}/${task.id}/clips/${clipId}/export?preset=${exportPreset}`, {
      cache: "no-store",
    });

    if (!response.ok) {
      alert(await buildSupportError(response, "Failed to export clip"));
      return;
    }

    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = blobUrl;
    link.download = `${fallbackFilename.replace(/\.mp4$/i, "")}_${exportPreset}.mp4`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(blobUrl);
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-white p-4">
        <div className="max-w-6xl mx-auto">
          <div className="mb-6">
            <Skeleton className="h-8 w-48 mb-2" />
            <Skeleton className="h-4 w-96" />
          </div>
          <div className="grid gap-6">
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
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-white p-4">
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
                    <Button size="sm" onClick={handleEditTitle} disabled={!editedTitle.trim()}>
                      <Check className="w-4 h-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
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
                    <h1 className={`text-2xl font-bold text-black ${task.status === "processing" || task.status === "queued" ? "shimmer" : ""}`}>{task.source_title}</h1>
                    <div className="flex items-center gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
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
                        className="text-red-600 hover:text-red-700 hover:bg-red-50"
                        onClick={() => setShowDeleteDialog(true)}
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </>
                )}
              </div>
              <div className="flex items-center gap-4 text-sm text-gray-600">
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
                {task.status === "completed" ? (
                  <span>
                    {clips.length} {clips.length === 1 ? "clip" : "clips"} generated
                  </span>
                ) : task.status === "processing" ? (
                  <div className="relative group">
                    <Badge className="bg-blue-100 text-blue-800 cursor-default shimmer">Processing</Badge>
                    <div className="absolute top-full mt-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md border bg-popover px-3 py-1.5 text-sm text-popover-foreground shadow-md opacity-0 scale-95 transition-all group-hover:opacity-100 group-hover:scale-100 pointer-events-none">
                      🔍&nbsp;&nbsp;We&apos;re currently processing your video. Check back in a couple minutes.
                    </div>
                  </div>
                ) : task.status === "queued" ? (
                  <Badge className="bg-yellow-100 text-yellow-800">Queued</Badge>
                ) : (
                  <Badge variant="outline" className="capitalize">
                    {task.status}
                  </Badge>
                )}
                {task.status === "completed" && clips.length > 0 && (
                  <Link href={`/tasks/${task.id}/edit`}>
                    <Button size="sm" variant="outline">
                      <Clapperboard className="w-4 h-4" />
                      Open Editor
                    </Button>
                  </Link>
                )}
                {(task.status === "queued" || task.status === "processing") && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      await fetch(`${taskApiUrl}/${task.id}/cancel`, {
                        method: "POST",
                      });
                      await fetchTaskStatus();
                    }}
                  >
                    Cancel
                  </Button>
                )}
                {(task.status === "cancelled" || task.status === "error") && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      await fetch(`${taskApiUrl}/${task.id}/resume`, {
                        method: "POST",
                      });
                      await fetchTaskStatus();
                    }}
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
                          <DynamicVideoPlayer src={`${apiUrl}${clip.video_url}`} poster="/placeholder-video.jpg" />
                        </div>
                        <div className="p-6 flex-1">
                          <div className="flex items-start justify-between mb-4">
                            <div>
                              <h3 className="font-semibold text-lg text-black mb-1">Clip {clip.clip_order}</h3>
                              <div className="flex items-center gap-2 text-sm text-gray-600">
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
                            <div className="mb-4">
                              <h4 className="font-medium text-black mb-2">Transcript</h4>
                              <p className="text-sm text-gray-700 bg-gray-50 p-3 rounded">{clip.text}</p>
                            </div>
                          )}
                          {clip.reasoning && (
                            <div className="mb-4">
                              <h4 className="font-medium text-black mb-2">AI Analysis</h4>
                              <p className="text-sm text-gray-600">{clip.reasoning}</p>
                            </div>
                          )}
                          <Button size="sm" variant="outline" asChild>
                            <a href={`${apiUrl}${clip.video_url}`} download={clip.filename}>
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
                    The task completed but no clips were generated. The video may not have had suitable content for
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
                    Your clips are being generated. This page will refresh automatically when they&apos;re ready.
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
                Project Settings
              </Button>
              {selectedClipIds.length >= 2 && (
                <Button variant="outline" size="sm" onClick={handleMergeClips}>
                  <GitMerge className="w-4 h-4" />
                  Merge Selected ({selectedClipIds.length})
                </Button>
              )}
            </div>

            <Sheet open={settingsSheetOpen} onOpenChange={setSettingsSheetOpen}>
              <SheetContent side="right" className="sm:max-w-md overflow-y-auto">
                <SheetHeader>
                  <SheetTitle className="flex items-center gap-2">
                    <Settings2 className="w-4 h-4" />
                    Project Settings
                  </SheetTitle>
                  <SheetDescription>
                    Configure font, caption, and B-roll settings for this task&apos;s clips.
                  </SheetDescription>
                </SheetHeader>

                <div className="space-y-5 px-4">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-gray-500">Font</label>
                    <Select value={projectFontFamily} onValueChange={setProjectFontFamily}>
                      <SelectTrigger>
                        <SelectValue placeholder="Font family" />
                      </SelectTrigger>
                      <SelectContent>
                        {availableFonts.map((font) => (
                          <SelectItem key={font.name} value={font.name}>
                            <span className="flex items-center gap-2">
                              <Type className="w-3 h-3" />
                              {font.display_name}
                            </span>
                          </SelectItem>
                        ))}
                        {availableFonts.length === 0 && (
                          <SelectItem value="TikTokSans-Regular">TikTok Sans Regular</SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-gray-500">Size</label>
                    <Input
                      type="number"
                      min={12}
                      max={72}
                      value={projectFontSize}
                      onChange={(e) => setProjectFontSize(e.target.value)}
                      placeholder="Font size"
                    />
                  </div>

                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-gray-500">Color</label>
                    <div className="flex items-center gap-2">
                      <input
                        type="color"
                        value={projectFontColor}
                        onChange={(e) => setProjectFontColor(e.target.value)}
                        className="h-9 w-9 rounded border border-gray-300 cursor-pointer"
                      />
                      <Input
                        value={projectFontColor}
                        onChange={(e) => setProjectFontColor(e.target.value)}
                        placeholder="#FFFFFF"
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

                  <label className="flex items-center gap-2 text-sm text-gray-700">
                    <input
                      type="checkbox"
                      checked={projectIncludeBroll}
                      onChange={(e) => setProjectIncludeBroll(e.target.checked)}
                      className="rounded"
                    />
                    Include B-roll
                  </label>
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

            {clips.map((clip) => (
              <Card key={clip.id} className="overflow-hidden">
                <CardContent className="p-0">
                  <div className="flex flex-col lg:flex-row">
                    {/* Video Player */}
                    <div className="relative flex-shrink-0 bg-black rounded-lg overflow-hidden m-3">
                      <DynamicVideoPlayer src={`${apiUrl}${clip.video_url}`} poster="/placeholder-video.jpg" />
                    </div>

                    {/* Clip Details */}
                    <div className="p-6 flex-1">
                      <div className="flex items-start justify-between mb-4">
                        <div>
                          <label className="flex items-center gap-2 text-xs text-gray-600 mb-2">
                            <input
                              type="checkbox"
                              checked={selectedClipIds.includes(clip.id)}
                              onChange={() => handleToggleClipSelection(clip.id)}
                            />
                            Select for merge
                          </label>
                          <h3 className="font-semibold text-lg text-black mb-1">Clip {clip.clip_order}</h3>
                          <div className="flex items-center gap-2 text-sm text-gray-600">
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
                        <div className="mb-4">
                          <h4 className="font-medium text-black mb-2">Transcript</h4>
                          <p className="text-sm text-gray-700 bg-gray-50 p-3 rounded">{clip.text}</p>
                        </div>
                      )}

                      {clip.reasoning && (
                        <div className="mb-4">
                          <h4 className="font-medium text-black mb-2">AI Analysis</h4>
                          <p className="text-sm text-gray-600">{clip.reasoning}</p>
                        </div>
                      )}

                      <div className="flex gap-2">
                        <Button size="sm" variant="outline" asChild>
                          <a href={`${apiUrl}${clip.video_url}`} download={clip.filename}>
                            <Download className="w-4 h-4" />
                            Download
                          </a>
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => handleExportClip(clip.id, clip.filename)}>
                          <Download className="w-4 h-4" />
                          Export
                        </Button>
                        <Select value={exportPreset} onValueChange={setExportPreset}>
                          <SelectTrigger className="h-8 w-28">
                            <SelectValue placeholder="Preset" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="tiktok">TikTok</SelectItem>
                            <SelectItem value="reels">Reels</SelectItem>
                            <SelectItem value="shorts">Shorts</SelectItem>
                          </SelectContent>
                        </Select>
                        <Button
                          size="sm"
                          variant="outline"
                          className="text-red-600 hover:text-red-700 hover:bg-red-50 border-red-200"
                          onClick={() => setDeletingClipId(clip.id)}
                        >
                          <Trash2 className="w-4 h-4" />
                          Delete
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setEditingClipId(editingClipId === clip.id ? null : clip.id);
                            setCaptionText(clip.text || "");
                          }}
                        >
                          <Scissors className="w-4 h-4" />
                          Edit
                        </Button>
                      </div>

                      {editingClipId === clip.id && (
                        <div className="mt-4 p-3 border rounded-lg space-y-3 bg-gray-50">
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                            <Input
                              value={startOffset}
                              onChange={(e) => setStartOffset(e.target.value)}
                              placeholder="Start trim (sec)"
                            />
                            <Input
                              value={endOffset}
                              onChange={(e) => setEndOffset(e.target.value)}
                              placeholder="End trim (sec)"
                            />
                            <Button size="sm" onClick={() => handleTrimClip(clip.id)}>
                              <Scissors className="w-4 h-4" />
                              Trim
                            </Button>
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                            <Input
                              value={splitTime}
                              onChange={(e) => setSplitTime(e.target.value)}
                              placeholder="Split at (sec)"
                            />
                            <Button size="sm" variant="outline" onClick={() => handleSplitClip(clip.id)}>
                              <SplitSquareVertical className="w-4 h-4" />
                              Split
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => handleTrimClip(clip.id)}>
                              <RefreshCw className="w-4 h-4" />
                              Regenerate
                            </Button>
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                            <Input
                              value={captionText}
                              onChange={(e) => setCaptionText(e.target.value)}
                              placeholder="Caption text"
                            />
                            <Select value={captionPosition} onValueChange={setCaptionPosition}>
                              <SelectTrigger>
                                <SelectValue placeholder="Caption position" />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="top">Top</SelectItem>
                                <SelectItem value="middle">Middle</SelectItem>
                                <SelectItem value="bottom">Bottom</SelectItem>
                              </SelectContent>
                            </Select>
                            <Input
                              value={highlightWords}
                              onChange={(e) => setHighlightWords(e.target.value)}
                              placeholder="Highlights: word1, word2"
                            />
                          </div>
                          <Button size="sm" variant="outline" onClick={() => handleUpdateCaptions(clip.id)}>
                            <Subtitles className="w-4 h-4" />
                            Update Captions
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
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
