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
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
} from "@/components/ui/sheet";
import { LOCAL_USER_ID } from "@/lib/local-user";
import { useDebouncedEffect } from "@/lib/use-debounced-effect";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { buildFontOptionsPayload, FONT_SIZE_OPTIONS, FONT_TEMPLATE_DEFAULT_VALUE } from "@/lib/font-options";
import { DEFAULT_HOOK_STYLE, hookStylePayload, type HookAnimation, type HookPosition, type HookStyle, type HookTitleVariant } from "@/lib/hook-style";
import { DEFAULT_SOCIAL_OVERLAY, socialOverlayPayload, type SocialOverlay } from "@/lib/retention-settings";
import { HookTitlePreview } from "@/components/hook-title-preview";
import { HookVariantCompare } from "@/components/hook-variant-compare";
import { ContentPolicyProjectPanel } from "@/components/editor/content-policy-project-panel";
import { ClipMetadataPanel } from "@/components/editor/clip-metadata-panel";
import { TemplatePicker, type TemplateInfo } from "@/components/template-picker";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { SafeZoneOverlay } from "@/components/safe-zone-overlay";
import {
  PLATFORM_SAFE_ZONES,
  SAFE_ZONE_PLATFORM_IDS,
  bandOverlapsUnsafeZone,
  platformForExportPreset,
  type SafeZoneSelection,
} from "@/lib/safe-zones";
import { getSafeZoneProjectState, setSafeZoneProjectState, getDefaultSafeZonePlatform } from "@/lib/safe-zone-settings";
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
  SplitSquareVertical,
  GitMerge,
  RefreshCw,
  Subtitles,
  Settings2,
  Clapperboard,
  Sparkles,
} from "lucide-react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { Progress } from "@/components/ui/progress";
import Link from "next/link";
import DynamicVideoPlayer from "@/components/dynamic-video-player";
import { TranscriptPreview } from "@/components/transcript-preview";
import { FontSelectOption, type FontOption } from "@/components/font-select-option";
import { useDelayedFlag } from "@/hooks/use-delayed-flag";
import { toast } from "@/lib/toast";
import { recordLastOpenedProject } from "@/lib/last-project";

const PROCESSING_STAGES = [
  { id: "download", label: "Download" },
  { id: "transcribe", label: "Transcribe" },
  { id: "analyze", label: "Analyze" },
  { id: "render", label: "Render" },
  { id: "policy_check", label: "Policy Check" },
  { id: "metadata", label: "Metadata" },
  { id: "complete", label: "Done" },
] as const;

/** Fallback stage guess for SSE payloads from before the `stage` field existed. */
function stageFromProgress(progress: number): string {
  if (progress >= 100) return "complete";
  if (progress >= 70) return "render";
  if (progress >= 50) return "analyze";
  if (progress >= 30) return "transcribe";
  return "download";
}

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
  hook_title_variants: HookTitleVariant[];
  selected_hook_variant_id: string | null;
  metadata_title?: string | null;
  metadata_description?: string | null;
  metadata_tags?: string[] | null;
  metadata_provider?: string | null;
  metadata_generation_ms?: number | null;
  metadata_stale?: boolean;
}

interface ExportPreset {
  name: string;
  width: number;
  height: number;
  video_bitrate: string;
  audio_bitrate: string;
  max_duration_seconds: number;
  safe_area_top_pct: number;
  safe_area_bottom_pct: number;
  target_lufs: number;
}

const EXPORT_PRESET_LABELS: Record<string, string> = {
  tiktok: "TikTok",
  reels: "Instagram Reels",
  shorts: "YouTube Shorts",
  youtube_shorts: "YouTube Shorts",
  facebook_reels: "Facebook Reels",
  threads: "Threads",
};

function exportPresetLabel(name: string): string {
  return EXPORT_PRESET_LABELS[name] ?? name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
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
  started_at?: string | null;
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
  // Local-first: no login, so there's no real session — kept as a constant so
  // the existing "if (!session?.user?.id) return" guards keep working.
  const session = { user: { id: LOCAL_USER_ID } };
  const [task, setTask] = useState<TaskDetails | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const showLoading = useDelayedFlag(isLoading);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState("");
  const [progressStage, setProgressStage] = useState<string | null>(null);
  const [renderingClip, setRenderingClip] = useState<{ index: number; total: number } | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  // Wall-clock time this run entered the render stage, and the render-stage
  // progress observed at that moment — lets the ETA be computed from this
  // run's own observed per-clip render speed instead of a guessed constant.
  const renderStageRef = useRef<{ enteredAt: number; index: number } | null>(null);
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
  const [exportPreset, setExportPreset] = useState("original");
  const [exportPresets, setExportPresets] = useState<ExportPreset[]>([]);
  const [safeZonesEnabled, setSafeZonesEnabled] = useState(false);
  const [safeZonePlatform, setSafeZonePlatform] = useState<SafeZoneSelection>("all");
  const [safeZoneStateLoaded, setSafeZoneStateLoaded] = useState(false);
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
  const [projectHookStyle, setProjectHookStyle] = useState<HookStyle>(DEFAULT_HOOK_STYLE);
  const updateProjectHookStyle = useCallback(<K extends keyof HookStyle>(key: K, value: HookStyle[K]) => {
    setProjectHookStyle((current) => ({ ...current, [key]: value }));
  }, []);
  const [projectSocialOverlay, setProjectSocialOverlay] = useState<SocialOverlay>(DEFAULT_SOCIAL_OVERLAY);
  const updateProjectSocialOverlay = useCallback(<K extends keyof SocialOverlay>(key: K, value: SocialOverlay[K]) => {
    setProjectSocialOverlay((current) => ({ ...current, [key]: value }));
  }, []);
  const [isApplyingSettings, setIsApplyingSettings] = useState(false);
  const [regeneratingHookClipId, setRegeneratingHookClipId] = useState<string | null>(null);
  const [exportAllOpen, setExportAllOpen] = useState(false);
  const [exportAllRunning, setExportAllRunning] = useState(false);
  const [regeneratingMetadata, setRegeneratingMetadata] = useState(false);
  const [exportAllStatus, setExportAllStatus] = useState<
    Record<string, "pending" | "exporting" | "retrying" | "success" | "failed">
  >({});
  const [projectTemplates, setProjectTemplates] = useState<
    Array<{ id: string; name: string; section_count: number }>
  >([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>("");
  const [newTemplateName, setNewTemplateName] = useState("");
  const [isSavingTemplate, setIsSavingTemplate] = useState(false);
  const [isApplyingTemplate, setIsApplyingTemplate] = useState<"replace" | "merge" | null>(null);
  const [settingsSheetOpen, setSettingsSheetOpen] = useState(false);
  const [availableFonts, setAvailableFonts] = useState<FontOption[]>([]);
  const [deletingFontName, setDeletingFontName] = useState<string | null>(null);
  const [fontPendingDelete, setFontPendingDelete] = useState<FontOption | null>(null);
  const [availableTemplates, setAvailableTemplates] = useState<TemplateInfo[]>([]);
  const hasTriggeredAutoRefresh = useRef(false);
  const lastSavedProjectSettingsRef = useRef<string | null>(null);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const taskApiUrl = "/api/tasks";
  const getClipUrl = (videoUrl: string) =>
    videoUrl.startsWith("/api/") ? videoUrl : `/api${videoUrl}`;

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
        recordLastOpenedProject({ id: taskData.id, title: taskData.source_title });
        const loadedSettings = {
          projectFontFamily: taskData.font_family ?? null,
          projectFontSize: typeof taskData.font_size === "number" ? taskData.font_size : null,
          projectFontColor: taskData.font_color ?? null,
          projectCaptionTemplate: taskData.caption_template || "default",
          projectCutLongPauses: Boolean(taskData.cut_long_pauses),
          projectPauseThresholdMs: String(taskData.pause_threshold_ms || 900),
          projectRemoveFillerWords: Boolean(taskData.remove_filler_words),
          projectFilteredWords: (taskData.filtered_words || []).join(", "),
          projectHookStyle: { ...DEFAULT_HOOK_STYLE, ...(taskData.hook_style || {}) },
          projectSocialOverlay: { ...DEFAULT_SOCIAL_OVERLAY, ...(taskData.social_overlay || {}) },
        };
        setProjectFontFamily(loadedSettings.projectFontFamily);
        setProjectFontSize(loadedSettings.projectFontSize);
        setProjectFontColor(loadedSettings.projectFontColor);
        setProjectCaptionTemplate(loadedSettings.projectCaptionTemplate);
        setProjectCutLongPauses(loadedSettings.projectCutLongPauses);
        setProjectPauseThresholdMs(loadedSettings.projectPauseThresholdMs);
        setProjectRemoveFillerWords(loadedSettings.projectRemoveFillerWords);
        setProjectFilteredWords(loadedSettings.projectFilteredWords);
        setProjectHookStyle(loadedSettings.projectHookStyle);
        setProjectSocialOverlay(loadedSettings.projectSocialOverlay);
        // Mark this as the "already saved" baseline so the auto-save effect
        // below doesn't immediately re-save data we just loaded from the
        // server (fetchTaskStatus runs often — after SSE events, edits, etc).
        lastSavedProjectSettingsRef.current = JSON.stringify(loadedSettings);

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

    const loadExportPresets = async () => {
      try {
        const response = await fetch(`${apiUrl}/export-presets`, { cache: "no-store" });
        if (response.ok) {
          const data = await response.json();
          const presets = (Array.isArray(data) ? data : data.presets || []) as ExportPreset[];
          setExportPresets(presets);
        }
      } catch (error) {
        console.error("Failed to load export presets:", error);
      }
    };
    void loadExportPresets();
  }, [apiUrl]);

  // Load the per-project Safe Zone Overlay toggle/platform once, falling back
  // to the export preset's platform (or the user's global default) when this
  // project has never set one.
  useEffect(() => {
    if (!task?.id || safeZoneStateLoaded) return;
    const saved = getSafeZoneProjectState(task.id);
    if (saved) {
      setSafeZonesEnabled(saved.enabled);
      setSafeZonePlatform(saved.platform);
    } else {
      setSafeZonePlatform(platformForExportPreset(exportPreset) ?? getDefaultSafeZonePlatform());
    }
    setSafeZoneStateLoaded(true);
  }, [task?.id, safeZoneStateLoaded, exportPreset]);

  // Persist safe-zone toggle/platform per project once loaded (skip the
  // initial load itself so we don't immediately rewrite what we just read).
  useEffect(() => {
    if (!task?.id || !safeZoneStateLoaded) return;
    setSafeZoneProjectState(task.id, { enabled: safeZonesEnabled, platform: safeZonePlatform });
  }, [task?.id, safeZoneStateLoaded, safeZonesEnabled, safeZonePlatform]);

  // SSE effect - real-time progress updates
  useEffect(() => {
    const taskStatus = task?.status;
    if (!params.id || !taskStatus) return;

    // Only connect to SSE if task is queued or processing
    if (taskStatus !== "queued" && taskStatus !== "processing") return;

    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempts = 0;
    let closedByUs = false;
    let eventSource: EventSource;

    function connect() {
      eventSource = new EventSource(`${taskApiUrl}/${params.id}/progress`);
      attachHandlers(eventSource);
    }

    function scheduleReconnect() {
      if (closedByUs || reconnectAttempts >= 5) return;
      reconnectAttempts += 1;
      // Exponential backoff (1s, 2s, 4s, 8s, 16s) so a transient blip
      // recovers the live connection instead of freezing the bar until
      // the user manually reloads the page.
      const delay = Math.min(1000 * 2 ** (reconnectAttempts - 1), 16000);
      reconnectTimer = setTimeout(connect, delay);
    }

    function attachHandlers(eventSource: EventSource) {
    console.log("📡 Connected to SSE for real-time progress");

    eventSource.addEventListener("status", (e) => {
      const data = JSON.parse(e.data);
      console.log("📊 Status:", data);
      setProgress(data.progress || 0);
      setProgressMessage(data.message || "");
      if (data.stage) setProgressStage(data.stage);

      if (data.status === "completed") {
        void fetchTaskStatus().then(() => triggerAutoRefresh());
      }
    });

    eventSource.addEventListener("progress", (e) => {
      const data = JSON.parse(e.data);
      console.log("📈 Progress:", data);
      setProgress(data.progress || 0);
      setProgressMessage(data.message || "");
      if (data.stage) setProgressStage(data.stage);

      // Update task status if provided
      if (data.status) {
        setTask((currentTask) => (currentTask ? { ...currentTask, status: data.status } : currentTask));

        if (data.status === "completed") {
          void fetchTaskStatus().then(() => triggerAutoRefresh());
        }
      }
    });

    eventSource.addEventListener("clip_progress", (e) => {
      const data = JSON.parse(e.data);
      console.log("🎞️ Clip rendering:", data.clip_index + 1, "/", data.total_clips);
      setRenderingClip({ index: data.clip_index, total: data.total_clips });
    });

    eventSource.addEventListener("clip_ready", (e) => {
      const data = JSON.parse(e.data);
      console.log("🎬 Clip ready:", data.clip_index + 1, "/", data.total_clips);
      setRenderingClip(null);
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
      closedByUs = true;
      eventSource.close();

      // Refresh task and clips
      await fetchTaskStatus();
      triggerAutoRefresh();
    });

    eventSource.addEventListener("error", (e) => {
      console.error("❌ SSE error:", e);
      const maybeMessageEvent = e as MessageEvent<string>;
      if (typeof maybeMessageEvent.data === "string" && maybeMessageEvent.data.length > 0) {
        // A real server-sent error payload — fatal, don't retry.
        const data = JSON.parse(maybeMessageEvent.data);
        setError(data.error || "Connection error");
        closedByUs = true;
        eventSource.close();
        return;
      }
      // Native EventSource connection error (network blip, backend
      // restart) — reconnect with backoff instead of leaving the bar frozen.
      eventSource.close();
      scheduleReconnect();
    });
    }

    connect();

    return () => {
      console.log("🔌 Disconnecting SSE");
      closedByUs = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      eventSource?.close();
    };
  }, [params.id, task?.status, fetchTaskStatus, taskApiUrl, triggerAutoRefresh]); // Re-run when task status changes

  // Elapsed-time ticker, driven by the task's own started_at (falling back to
  // created_at for a still-queued task) rather than "time since this tab
  // opened", so it survives a page refresh mid-processing.
  useEffect(() => {
    if (task?.status !== "queued" && task?.status !== "processing") return;
    const startedAt = task.started_at || task.created_at;
    if (!startedAt) return;
    const startMs = new Date(startedAt).getTime();
    if (Number.isNaN(startMs)) return;

    const tick = () => setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startMs) / 1000)));
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [task?.status, task?.started_at, task?.created_at]);

  // Marks the moment this run entered the render stage (and the clip index
  // at that moment) so the ETA below can be derived from this run's own
  // observed per-clip render speed instead of a guessed constant.
  useEffect(() => {
    if (progressStage !== "render") {
      renderStageRef.current = null;
      return;
    }
    if (!renderStageRef.current) {
      renderStageRef.current = { enteredAt: Date.now(), index: renderingClip?.index ?? 0 };
    }
  }, [progressStage, renderingClip?.index]);

  // Never fabricate an ETA: only shown once we have a real, this-run signal
  // to derive it from (observed per-clip render time during the render
  // stage) — every earlier stage honestly shows "estimating…" instead of a
  // guessed number.
  const estimatedSecondsRemaining = (() => {
    if (progressStage !== "render" || !renderingClip || !renderStageRef.current) return null;
    const clipsDoneSinceEntering = renderingClip.index - renderStageRef.current.index;
    if (clipsDoneSinceEntering <= 0) return null;
    const secondsSinceEntering = (Date.now() - renderStageRef.current.enteredAt) / 1000;
    const secondsPerClip = secondsSinceEntering / clipsDoneSinceEntering;
    const clipsRemaining = renderingClip.total - renderingClip.index;
    return Math.max(0, Math.round(secondsPerClip * clipsRemaining));
  })();

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const getScoreColor = (score: number) => {
    if (score >= 0.8) return "bg-primary text-primary-foreground";
    if (score >= 0.6) return "border border-border text-foreground bg-background";
    return "bg-foreground text-background";
  };

  const getViralityColor = (score: number) => {
    if (score >= 80) return "text-primary";
    if (score >= 60) return "text-foreground";
    return "text-foreground font-bold";
  };

  const getViralityBgColor = (score: number) => {
    if (score >= 80) return "bg-primary text-primary-foreground";
    if (score >= 60) return "border border-border text-foreground bg-background";
    return "bg-foreground text-background";
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
        toast.success("Title updated.");
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
        toast.success("Moved to Trash.");
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
        toast.success("Clip deleted.");
      } else {
        toast.error(await buildSupportError(response, "Failed to delete clip"));
      }
    } catch (err) {
      console.error("Error deleting clip:", err);
      toast.error(err instanceof Error ? err.message : "Failed to delete clip");
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
      toast.error(await buildSupportError(response, "Failed to trim clip"));
      return;
    }
    await fetchTaskStatus();
    toast.success("Clip trimmed.");
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
      toast.error(await buildSupportError(response, "Failed to split clip"));
      return;
    }
    await fetchTaskStatus();
    toast.success("Clip split.");
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
      toast.error(await buildSupportError(response, "Failed to merge clips"));
      return;
    }
    setSelectedClipIds([]);
    await fetchTaskStatus();
    toast.success("Clips merged.");
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
      toast.error(await buildSupportError(response, "Failed to update captions"));
      return;
    }
    await fetchTaskStatus();
    toast.success("Captions updated.");
  };

  const saveProjectSettings = useCallback(
    async (applyToExisting: boolean) => {
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
          hook_style: hookStylePayload(projectHookStyle),
          social_overlay: socialOverlayPayload(projectSocialOverlay),
          apply_to_existing: applyToExisting,
        }),
      });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, "Failed to save settings"));
      }
      await fetchTaskStatus();
    },
    [
      session?.user?.id, params.id, taskApiUrl, projectFontFamily, projectFontSize, projectFontColor,
      projectPauseThresholdMs, projectFilteredWords, projectCaptionTemplate, projectCutLongPauses,
      projectRemoveFillerWords, projectHookStyle, projectSocialOverlay, buildSupportError, fetchTaskStatus,
    ],
  );

  useEffect(() => {
    if (!settingsSheetOpen) return;
    fetch("/api/templates", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : { templates: [] }))
      .then((data) => setProjectTemplates(data.templates || []))
      .catch(() => setProjectTemplates([]));
  }, [settingsSheetOpen]);

  const handleSaveAsTemplate = async () => {
    const name = newTemplateName.trim();
    if (!name || !params.id) return;
    setIsSavingTemplate(true);
    try {
      const response = await fetch("/api/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, task_id: params.id }),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to save template"));
      toast.success(`Saved template "${name}".`);
      setNewTemplateName("");
      const listResponse = await fetch("/api/templates", { cache: "no-store" });
      if (listResponse.ok) {
        const data = await listResponse.json();
        setProjectTemplates(data.templates || []);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save template");
    } finally {
      setIsSavingTemplate(false);
    }
  };

  const handleApplyTemplate = async (mode: "replace" | "merge") => {
    if (!selectedTemplateId || !params.id) return;
    setIsApplyingTemplate(mode);
    try {
      const response = await fetch(`/api/templates/${selectedTemplateId}/apply/${params.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to apply template"));
      toast.success(mode === "replace" ? "Template applied — all settings replaced." : "Template merged into current settings.");
      await fetchTaskStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to apply template");
    } finally {
      setIsApplyingTemplate(null);
    }
  };

  const handleApplyProjectSettings = async () => {
    setIsApplyingSettings(true);
    try {
      await saveProjectSettings(true);
      toast.success("Settings applied to all clips — re-rendering.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to apply settings");
    } finally {
      setIsApplyingSettings(false);
    }
  };

  // Hooks are generated once (during analysis) and cached — this is the only
  // way to get a fresh LLM call for a single clip's hook without going
  // through the full A/B "Compare Hooks" dialog.
  const handleRegenerateHook = async (clipId: string) => {
    setRegeneratingHookClipId(clipId);
    try {
      const variantsResponse = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}/hook-variants`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: 1 }),
      });
      if (!variantsResponse.ok) {
        throw new Error(await buildSupportError(variantsResponse, "Failed to regenerate hook"));
      }
      const data = await variantsResponse.json();
      const variant = (data.variants || [])[0];
      if (!variant) throw new Error("No new hook was generated");

      const selectResponse = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}/hook-variants/select`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ variant_id: variant.id }),
      });
      if (!selectResponse.ok) {
        throw new Error(await buildSupportError(selectResponse, "Failed to apply regenerated hook"));
      }
      await fetchTaskStatus();
      toast.success("Hook regenerated.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to regenerate hook");
    } finally {
      setRegeneratingHookClipId(null);
    }
  };

  // Auto-save the settings themselves (cheap: just persists to the task's
  // metadata) shortly after the user stops editing — separate from "Apply to
  // All Clips", which re-renders every clip and stays an explicit action
  // since it's expensive (can take minutes for several clips).
  const [autoSaveState, setAutoSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  useDebouncedEffect(
    () => {
      const snapshot = JSON.stringify({
        projectFontFamily, projectFontSize, projectFontColor, projectCaptionTemplate,
        projectCutLongPauses, projectPauseThresholdMs, projectRemoveFillerWords, projectFilteredWords,
        projectHookStyle, projectSocialOverlay,
      });
      // fetchTaskStatus (SSE events, other edits) reloads these same states
      // from the server — skip re-saving when nothing actually changed.
      if (snapshot === lastSavedProjectSettingsRef.current) return;

      setAutoSaveState("saving");
      saveProjectSettings(false)
        .then(() => {
          lastSavedProjectSettingsRef.current = snapshot;
          setAutoSaveState("saved");
        })
        .catch(() => setAutoSaveState("error"));
    },
    [
      projectFontFamily, projectFontSize, projectFontColor, projectCaptionTemplate,
      projectCutLongPauses, projectPauseThresholdMs, projectRemoveFillerWords, projectFilteredWords,
      projectHookStyle, projectSocialOverlay,
    ],
    1000,
  );

  const handleDeleteFont = (font: FontOption) => {
    if (font.scope !== "user" || deletingFontName) return;
    setFontPendingDelete(font);
  };

  const confirmDeleteFont = async () => {
    const font = fontPendingDelete;
    if (!font) return;

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
      toast.success(`"${font.display_name}" deleted.`);
    } catch (deleteError) {
      toast.error(deleteError instanceof Error ? deleteError.message : "Failed to delete font");
    } finally {
      setDeletingFontName(null);
      setFontPendingDelete(null);
    }
  };

  // Shared by the single-clip download/export button and "Export All Clips":
  // triggers a browser download for one clip and returns whether it
  // succeeded instead of toasting directly, so the batch flow can decide when
  // to surface a toast (once, with an aggregate result) instead of one per
  // clip. "original" is a frontend-only sentinel (not a real backend preset,
  // see EXPORT_PRESETS) meaning "download the rendered file as-is" — same
  // special case handleDownloadClip already used for a single clip.
  const exportClipFile = async (clip: Clip, preset: string): Promise<{ ok: boolean; error?: string }> => {
    if (!session?.user?.id || !task?.id) return { ok: false, error: "Not ready" };

    if (preset === "original") {
      const link = document.createElement("a");
      link.href = getClipUrl(clip.video_url);
      link.download = clip.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      return { ok: true };
    }

    try {
      const response = await fetch(`${taskApiUrl}/${task.id}/clips/${clip.id}/export?preset=${preset}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        return { ok: false, error: await buildSupportError(response, "Failed to export clip") };
      }
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = `${clip.filename.replace(/\.mp4$/i, "")}_${preset}.mp4`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(blobUrl);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : "Failed to export clip" };
    }
  };

  const handleExportClip = async (clipId: string, fallbackFilename: string) => {
    const clip = clips.find((c) => c.id === clipId) ?? {
      id: clipId,
      filename: fallbackFilename,
      video_url: "",
    } as Clip;
    const result = await exportClipFile(clip, exportPreset);
    if (!result.ok) {
      toast.error(result.error || "Failed to export clip");
      return;
    }
    toast.success("Clip exported.");
  };

  // Exports every clip in sequence (not parallel — one export renders at a
  // time on the backend already, and sequential downloads avoid the browser's
  // multi-download popup-blocker). One retry per failed clip before it's
  // marked failed; one clip failing never stops the rest of the batch.
  const handleRegenerateMetadata = async () => {
    if (!task?.id) return;
    setRegeneratingMetadata(true);
    try {
      const response = await fetch(`/api/tasks/${task.id}/metadata/regenerate`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, "Failed to regenerate metadata"));
      }
      const data = await response.json();
      toast.success(`Metadata generated for ${data.generated}/${data.total} clips (${data.provider}).`);
      await fetchTaskStatus();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to regenerate metadata");
    } finally {
      setRegeneratingMetadata(false);
    }
  };

  const handleExportAllMetadata = () => {
    if (clips.length === 0) return;
    const ordered = [...clips].sort((a, b) => (a.clip_order ?? 0) - (b.clip_order ?? 0));
    const blocks = ordered.map((clip, idx) => {
      const title = clip.metadata_title || "(no title generated)";
      const description = clip.metadata_description || "(no description generated)";
      const tags = (clip.metadata_tags ?? []).join(", ") || "(no tags generated)";
      return `Clip ${idx + 1}\nTitle: ${title}\nDescription: ${description}\nTags: ${tags}`;
    });
    const text = blocks.join("\n\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(task?.source_title || "clips").replace(/[^a-z0-9]+/gi, "-")}-metadata.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleExportAllClips = async () => {
    if (clips.length === 0) return;
    setExportAllRunning(true);
    setExportAllOpen(true);
    const initialStatus: Record<string, "pending"> = {};
    for (const clip of clips) initialStatus[clip.id] = "pending";
    setExportAllStatus(initialStatus);

    const failures: string[] = [];
    let succeeded = 0;

    for (const clip of clips) {
      setExportAllStatus((current) => ({ ...current, [clip.id]: "exporting" }));
      let result = await exportClipFile(clip, exportPreset);
      if (!result.ok) {
        setExportAllStatus((current) => ({ ...current, [clip.id]: "retrying" }));
        result = await exportClipFile(clip, exportPreset);
      }
      if (result.ok) {
        succeeded += 1;
        setExportAllStatus((current) => ({ ...current, [clip.id]: "success" }));
      } else {
        failures.push(clip.filename);
        setExportAllStatus((current) => ({ ...current, [clip.id]: "failed" }));
      }
    }

    setExportAllRunning(false);
    if (failures.length === 0) {
      toast.success(`All ${succeeded} clips exported`);
    } else {
      toast.error(`${succeeded} succeeded, ${failures.length} failed: ${failures.join(", ")}`);
    }
  };

  const handleRetryClipExport = async (clip: Clip) => {
    setExportAllStatus((current) => ({ ...current, [clip.id]: "exporting" }));
    const result = await exportClipFile(clip, exportPreset);
    setExportAllStatus((current) => ({ ...current, [clip.id]: result.ok ? "success" : "failed" }));
    if (!result.ok) {
      toast.error(result.error || `Failed to export ${clip.filename}`);
    } else {
      toast.success(`${clip.filename} exported.`);
    }
  };

  // Approximate on-screen bands for burned-in text: captions sit at ~75% down
  // the frame, the hook title in the top safe area. A rough overlap check
  // against the target platform's unsafe zones — not pixel-exact, just enough
  // to warn before export. Never auto-moves anything; the user decides.
  const warnIfTextInUnsafeZone = (presetName: string) => {
    const platformId = platformForExportPreset(presetName);
    if (!platformId) return;
    const platform = PLATFORM_SAFE_ZONES[platformId];
    const captionOverlap = bandOverlapsUnsafeZone(70, 80, platform.insets);
    const hookOverlap = bandOverlapsUnsafeZone(0, 15, platform.insets);
    if (captionOverlap || hookOverlap) {
      toast.warning(
        `Some ${captionOverlap && hookOverlap ? "captions/hooks" : captionOverlap ? "captions" : "hook text"} may be covered by ${platform.label} UI. Preview safe zones?`,
      );
    }
  };

  const handleDownloadClip = (clip: Clip) => {
    warnIfTextInUnsafeZone(exportPreset);
    if (exportPreset === "original") {
      const link = document.createElement("a");
      link.href = getClipUrl(clip.video_url);
      link.download = clip.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast.success("Clip downloaded.");
      return;
    }
    void handleExportClip(clip.id, clip.filename);
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
      toast.success("Share link copied to clipboard.");
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
      toast.success("Share link disabled.");
    } catch (revokeError) {
      toast.error(revokeError instanceof Error ? revokeError.message : "Failed to disable share link");
    } finally {
      setIsRevokingShare(false);
    }
  };

  if (showLoading) {
    return (
      <div className="min-h-screen bg-background p-4">
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

  if (!task) {
    // isLoading is still true but showLoading hasn't kicked in yet (avoids a
    // skeleton flash on fast loads) — render nothing for this brief window.
    return null;
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b bg-background">
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
                    <h1 className={`text-2xl font-bold text-foreground ${task.status === "processing" || task.status === "queued" ? "shimmer" : ""}`}>{task.source_title}</h1>
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
                        className="text-foreground hover:bg-foreground hover:text-background"
                        onClick={() => setShowDeleteDialog(true)}
                      >
                        <Trash2 className="w-4 h-4" />
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
                    <Badge className="bg-secondary text-secondary-foreground cursor-default shimmer">Processing</Badge>
                    <div className="absolute top-full mt-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md border bg-popover px-3 py-1.5 text-sm text-popover-foreground opacity-0 scale-95 transition-all group-hover:opacity-100 group-hover:scale-100 pointer-events-none">
                      🔍&nbsp;&nbsp;We&apos;re currently processing your video. Check back in a couple minutes.
                    </div>
                  </div>
                ) : task.status === "queued" ? (
                  <Badge className="bg-background text-foreground border border-border">Queued</Badge>
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
                {task.status === "completed" && clips.length > 0 && (
                  <Button size="sm" variant="outline" onClick={handleExportAllClips} disabled={exportAllRunning}>
                    <Download className="w-4 h-4" />
                    {exportAllRunning ? "Exporting All..." : "Export All Clips"}
                  </Button>
                )}
                {task.status === "completed" && clips.length > 0 && (
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
                      <Switch checked={safeZonesEnabled} onCheckedChange={setSafeZonesEnabled} />
                      Safe Zones
                    </label>
                    {safeZonesEnabled && (
                      <Select
                        value={safeZonePlatform}
                        onValueChange={(value) => setSafeZonePlatform(value as SafeZoneSelection)}
                      >
                        <SelectTrigger size="sm" aria-label="Safe zone platform" className="h-8 min-w-[140px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent align="start">
                          <SelectItem value="all">All</SelectItem>
                          {SAFE_ZONE_PLATFORM_IDS.map((id) => (
                            <SelectItem key={id} value={id}>
                              {PLATFORM_SAFE_ZONES[id].label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  </div>
                )}
                {task.status === "completed" && clips.length > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleRegenerateMetadata}
                    disabled={regeneratingMetadata}
                  >
                    <Sparkles className="w-4 h-4" />
                    {regeneratingMetadata ? "Generating..." : "Regenerate Metadata"}
                  </Button>
                )}
                {task.status === "completed" && clips.length > 0 && (
                  <Button size="sm" variant="outline" onClick={handleExportAllMetadata}>
                    <Download className="w-4 h-4" />
                    Export All Metadata
                  </Button>
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
                <span className="w-2 h-2 bg-foreground rounded-full animate-[pulse_1.4s_ease-in-out_infinite]" />
                <span className="w-2 h-2 bg-foreground rounded-full animate-[pulse_1.4s_ease-in-out_0.2s_infinite]" />
                <span className="w-2 h-2 bg-foreground rounded-full animate-[pulse_1.4s_ease-in-out_0.4s_infinite]" />
                <div className="absolute top-full mt-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md border bg-popover px-3 py-1.5 text-sm text-popover-foreground opacity-0 scale-95 transition-all group-hover:opacity-100 group-hover:scale-100 pointer-events-none">
                  ☕&nbsp;&nbsp;Grab a coffee, and come back to ready-to-post clips.
                </div>
              </div>

              {/* Status message */}
              <p className="shimmer text-muted-foreground text-sm tracking-wide mb-8">
                {progressMessage || (task.status === "queued" ? "Waiting in queue" : "Processing")}
              </p>

              {/* Minimal progress bar */}
              {progress > 0 && (
                <div className="w-48">
                  <div className="h-px bg-border w-full relative overflow-hidden">
                    <div
                      className="absolute inset-y-0 left-0 bg-primary transition-all duration-700 ease-out"
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                  <p className="text-[11px] text-muted-foreground text-center mt-3 tabular-nums">{progress}%</p>
                  <p className="text-[11px] text-muted-foreground text-center mt-1 tabular-nums">
                    Elapsed {formatDuration(elapsedSeconds)}
                    {" · "}
                    {estimatedSecondsRemaining !== null
                      ? `~${formatDuration(estimatedSecondsRemaining)} remaining`
                      : "estimating…"}
                  </p>
                </div>
              )}

              {/* Stage stepper */}
              <div className="flex items-center gap-1.5 mt-6">
                {PROCESSING_STAGES.map((s, idx) => {
                  const currentIdx = PROCESSING_STAGES.findIndex(
                    (st) => st.id === (progressStage ?? stageFromProgress(progress)),
                  );
                  const state = idx < currentIdx ? "done" : idx === currentIdx ? "current" : "pending";
                  return (
                    <div key={s.id} className="flex items-center gap-1.5">
                      <span
                        className={`text-[11px] px-2 py-1 border transition-colors ${
                          state === "done"
                            ? "bg-foreground text-background border-foreground"
                            : state === "current"
                              ? "border-foreground text-foreground font-medium"
                              : "border-border text-muted-foreground"
                        }`}
                      >
                        {s.label}
                      </span>
                      {idx < PROCESSING_STAGES.length - 1 && (
                        <span className="w-3 h-px bg-border" />
                      )}
                    </div>
                  );
                })}
              </div>

              {renderingClip && (
                <p className="text-[11px] text-muted-foreground mt-3">
                  Rendering clip {renderingClip.index + 1} of {renderingClip.total}…
                </p>
              )}
            </div>

            {/* Live clips grid — shows clips as they render */}
            {clips.length > 0 && (
              <div className="grid gap-6">
                <p className="text-sm text-muted-foreground text-center">
                  {clips.length} clip{clips.length !== 1 ? "s" : ""} ready
                  {renderingClip ? ` · rendering ${renderingClip.index + 1}/${renderingClip.total}` : ""}
                </p>
                {clips.map((clip) => (
                  <Card key={clip.id} className="overflow-hidden">
                    <CardContent className="p-0">
                      <div className="flex flex-col lg:flex-row">
                        <div className="relative flex-shrink-0 bg-foreground overflow-hidden m-3">
                          <DynamicVideoPlayer
                        src={getClipUrl(clip.video_url)}
                        poster="/placeholder-video.jpg"
                        overlay={safeZonesEnabled ? <SafeZoneOverlay selection={safeZonePlatform} /> : undefined}
                      />
                        </div>
                        <div className="p-6 flex-1">
                          <div className="flex items-start justify-between mb-4">
                            <div>
                              <h3 className="font-semibold text-lg text-foreground mb-1">
                                {clip.hook_title || `Clip ${clip.clip_order}`}
                              </h3>
                              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                <span>Clip {clip.clip_order}</span>
                                <span>•</span>
                                <span>{clip.start_time} - {clip.end_time}</span>
                                <span>•</span>
                                <span>{formatDuration(clip.duration)}</span>
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              {clip.virality_score > 0 && (
                                <Badge className={getViralityBgColor(clip.virality_score)}>
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
                            <a href={getClipUrl(clip.video_url)} download={clip.filename}>
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
              <span className="w-2 h-2 bg-muted-foreground rounded-full animate-[pulse_1.4s_ease-in-out_infinite]" />
              <span className="w-2 h-2 bg-muted-foreground rounded-full animate-[pulse_1.4s_ease-in-out_0.2s_infinite]" />
              <span className="w-2 h-2 bg-muted-foreground rounded-full animate-[pulse_1.4s_ease-in-out_0.4s_infinite]" />
            </div>
          </div>
        ) : task?.status === "error" ? (
          <Card>
            <CardContent className="p-8 text-center">
              <div className="text-foreground font-bold mb-4">
                <AlertCircle className="w-12 h-12 mx-auto mb-2" />
                <h2 className="text-xl font-semibold">Processing Failed</h2>
              </div>
              <p className="text-muted-foreground mb-4">There was an error processing your video. Please try again.</p>
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
                  <div className="text-foreground font-bold mb-4">
                    <AlertCircle className="w-12 h-12 mx-auto mb-2" />
                    <h2 className="text-xl font-semibold">No Clips Generated</h2>
                  </div>
                  <p className="text-muted-foreground mb-4">
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
                  <div className="w-16 h-16 bg-secondary rounded-full flex items-center justify-center mx-auto mb-4">
                    <Clock className="w-8 h-8 text-secondary-foreground animate-pulse" />
                  </div>
                  <h2 className="text-xl font-semibold text-foreground mb-2">Still Generating...</h2>
                  <p className="text-muted-foreground">
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
              <SheetContent side="right" className="w-full sm:max-w-xl overflow-y-auto">
                <SheetHeader>
                  <SheetTitle className="flex items-center gap-2">
                    <Settings2 className="w-4 h-4" />
                    Project Settings
                  </SheetTitle>
                  <SheetDescription>
                    Configure font, caption, and cleanup settings for this task&apos;s clips.
                  </SheetDescription>
                </SheetHeader>

                <div className="space-y-5 px-4">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground">Font</label>
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
                            onDelete={handleDeleteFont}
                          />
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground">Size</label>
                    <div className="grid grid-cols-4 gap-1.5">
                      {FONT_SIZE_OPTIONS.map((option) => (
                        <button
                          key={option.label}
                          type="button"
                          onClick={() => setProjectFontSize(option.value)}
                          className={`px-2 py-1.5 rounded-md text-xs font-medium border transition-colors ${
                            projectFontSize === option.value
                              ? "bg-foreground text-background border-foreground"
                              : "bg-background text-muted-foreground border-border hover:border-primary"
                          }`}
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-medium text-muted-foreground">Color</label>
                      <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
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
                        className="h-9 w-9 rounded border border-border cursor-pointer disabled:cursor-not-allowed"
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
                    <label className="text-xs font-medium text-muted-foreground">Caption Template</label>
                    <TemplatePicker
                      templates={availableTemplates}
                      selectedId={projectCaptionTemplate}
                      onSelect={setProjectCaptionTemplate}
                    />
                  </div>

                  <div className="border border-border p-3 space-y-3">
                    <div>
                      <div className="text-sm font-medium text-foreground">Hook title</div>
                      <div className="text-xs text-muted-foreground">Style of the AI-written headline burned in for the first few seconds.</div>
                    </div>

                    <HookTitlePreview style={projectHookStyle} captionTemplate={projectCaptionTemplate} availableTemplates={availableTemplates} />

                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">Size</label>
                      <div className="grid grid-cols-4 gap-1.5">
                        {[
                          { label: "Small", value: 0.65 },
                          { label: "Default", value: null },
                          { label: "Large", value: 1.0 },
                          { label: "XL", value: 1.3 },
                        ].map((option) => (
                          <button
                            key={option.label}
                            type="button"
                            onClick={() => updateProjectHookStyle("hook_font_size_scale", option.value)}
                            className={`px-2 py-1.5 rounded-md text-xs font-medium border transition-colors ${
                              projectHookStyle.hook_font_size_scale === option.value
                                ? "bg-foreground text-background border-foreground"
                                : "bg-background text-muted-foreground border-border hover:bg-border"
                            }`}
                          >
                            {option.label}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-muted-foreground">Text color</label>
                        <input
                          type="color"
                          value={projectHookStyle.hook_font_color ?? "#FFFFFF"}
                          onChange={(e) => updateProjectHookStyle("hook_font_color", e.target.value)}
                          className="w-full h-8 rounded border border-border cursor-pointer"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-muted-foreground flex items-center justify-between">
                          Background box
                          <Switch
                            checked={projectHookStyle.hook_background_color !== null}
                            onCheckedChange={(checked) =>
                              updateProjectHookStyle(
                                "hook_background_color",
                                checked ? (projectHookStyle.hook_background_color ?? "#00000099") : null,
                              )
                            }
                          />
                        </label>
                        {projectHookStyle.hook_background_color !== null && (
                          <input
                            type="color"
                            value={projectHookStyle.hook_background_color.slice(0, 7)}
                            onChange={(e) => updateProjectHookStyle("hook_background_color", `${e.target.value}99`)}
                            className="w-full h-8 rounded border border-border cursor-pointer"
                          />
                        )}
                      </div>
                    </div>

                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">Position</label>
                      <div className="grid grid-cols-3 gap-1.5">
                        {(["top", "center", "bottom"] as HookPosition[]).map((position) => (
                          <button
                            key={position}
                            type="button"
                            onClick={() => updateProjectHookStyle("hook_position", position)}
                            className={`px-2 py-1.5 rounded-md text-xs font-medium border capitalize transition-colors ${
                              (projectHookStyle.hook_position ?? "top") === position
                                ? "bg-foreground text-background border-foreground"
                                : "bg-background text-muted-foreground border-border hover:bg-border"
                            }`}
                          >
                            {position}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">Animation</label>
                      <Select
                        value={projectHookStyle.hook_animation ?? "fade_pop"}
                        onValueChange={(value) => updateProjectHookStyle("hook_animation", value as HookAnimation)}
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="fade_pop">Fade + Pop</SelectItem>
                          <SelectItem value="fade">Fade</SelectItem>
                          <SelectItem value="slide_down">Slide Down</SelectItem>
                          <SelectItem value="zoom_punch">Zoom Punch</SelectItem>
                          <SelectItem value="bounce">Bounce</SelectItem>
                          <SelectItem value="pulse">Pulse</SelectItem>
                          <SelectItem value="none">None</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    <label className="flex items-center justify-between text-sm text-foreground">
                      Drop shadow
                      <Switch
                        checked={projectHookStyle.hook_shadow ?? true}
                        onCheckedChange={(checked) => updateProjectHookStyle("hook_shadow", checked)}
                      />
                    </label>

                    <button
                      type="button"
                      className="text-xs text-muted-foreground hover:text-foreground underline"
                      onClick={() => setProjectHookStyle(DEFAULT_HOOK_STYLE)}
                    >
                      Reset hook styling to template default
                    </button>
                  </div>

                  <div className="border border-border p-3 space-y-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-sm font-medium text-foreground">Fake social overlay</div>
                        <div className="text-xs text-muted-foreground">Username, verified badge, like/comment counts.</div>
                      </div>
                      <Switch
                        checked={projectSocialOverlay.enabled}
                        onCheckedChange={(checked) => updateProjectSocialOverlay("enabled", checked)}
                      />
                    </div>
                    {projectSocialOverlay.enabled && (
                      <div className="grid grid-cols-2 gap-2">
                        <Input
                          placeholder="Username"
                          value={projectSocialOverlay.username}
                          onChange={(e) => updateProjectSocialOverlay("username", e.target.value)}
                        />
                        <Input
                          placeholder="Likes (24.5K)"
                          value={projectSocialOverlay.likes}
                          onChange={(e) => updateProjectSocialOverlay("likes", e.target.value)}
                        />
                      </div>
                    )}
                  </div>

                  <div className="border border-border p-3 space-y-3">
                    <div>
                      <div className="text-sm font-medium text-foreground">Clip cleanup</div>
                      <div className="text-xs text-muted-foreground">Apply silence and filler-word cuts to regenerated clips.</div>
                    </div>

                    <label className="flex items-center gap-2 text-sm text-foreground">
                      <input
                        type="checkbox"
                        checked={projectCutLongPauses}
                        onChange={(e) => setProjectCutLongPauses(e.target.checked)}
                        className="rounded"
                      />
                      Cut long pauses
                    </label>

                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">Pause threshold (ms)</label>
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

                    <label className="flex items-center gap-2 text-sm text-foreground">
                      <input
                        type="checkbox"
                        checked={projectRemoveFillerWords}
                        onChange={(e) => setProjectRemoveFillerWords(e.target.checked)}
                        className="rounded"
                      />
                      Remove filler words
                    </label>

                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">Extra filtered words or phrases</label>
                      <Input
                        value={projectFilteredWords}
                        onChange={(e) => setProjectFilteredWords(e.target.value)}
                        placeholder="basically, literally, to be honest"
                      />
                    </div>
                  </div>
                </div>

                <Separator className="my-2" />

                <div className="space-y-3">
                  <h4 className="text-sm font-medium text-foreground">Templates</h4>

                  <div className="flex items-center gap-2">
                    <Input
                      value={newTemplateName}
                      onChange={(e) => setNewTemplateName(e.target.value)}
                      placeholder="Template name"
                      className="flex-1"
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!newTemplateName.trim() || isSavingTemplate}
                      onClick={handleSaveAsTemplate}
                    >
                      {isSavingTemplate ? "Saving..." : "Save as Template"}
                    </Button>
                  </div>

                  {projectTemplates.length > 0 && (
                    <div className="space-y-2">
                      <Select value={selectedTemplateId} onValueChange={setSelectedTemplateId}>
                        <SelectTrigger>
                          <SelectValue placeholder="Load a template..." />
                        </SelectTrigger>
                        <SelectContent>
                          {projectTemplates.map((template) => (
                            <SelectItem key={template.id} value={template.id}>
                              {template.name} ({template.section_count} sections)
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          className="flex-1"
                          disabled={!selectedTemplateId || isApplyingTemplate !== null}
                          onClick={() => handleApplyTemplate("merge")}
                        >
                          {isApplyingTemplate === "merge" ? "Merging..." : "Merge into current"}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="flex-1"
                          disabled={!selectedTemplateId || isApplyingTemplate !== null}
                          onClick={() => handleApplyTemplate("replace")}
                        >
                          {isApplyingTemplate === "replace" ? "Replacing..." : "Replace all settings"}
                        </Button>
                      </div>
                    </div>
                  )}
                  <Link href="/settings/templates" className="text-xs text-muted-foreground underline block">
                    Manage templates (rename, duplicate, delete, export/import)
                  </Link>
                </div>

                <div className="px-4 pt-2">
                  <ContentPolicyProjectPanel taskId={task.id} />
                </div>

                <SheetFooter className="gap-2">
                  <p className="text-xs text-muted-foreground text-center" aria-live="polite">
                    {autoSaveState === "saving"
                      ? "Saving settings…"
                      : autoSaveState === "saved"
                        ? "Settings saved automatically"
                        : autoSaveState === "error"
                          ? "Couldn't auto-save settings"
                          : ""}
                  </p>
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
                  <p className="text-xs text-muted-foreground text-center">
                    Settings auto-save as you edit; this re-renders every clip with them (can take a few minutes).
                  </p>
                </SheetFooter>
              </SheetContent>
            </Sheet>

            {clips.length > 0 && (
              <p className="text-sm text-muted-foreground">
                {clips.filter((c) => c.metadata_title).length}/{clips.length} clips have metadata
                {clips.some((c) => c.metadata_stale) &&
                  ` · ${clips.filter((c) => c.metadata_stale).length} stale`}
              </p>
            )}

            {clips.map((clip) => (
              <Card key={clip.id} className="overflow-hidden">
                <CardContent className="p-0">
                  <div className="flex flex-col lg:flex-row">
                    {/* Video Player */}
                    <div className="relative flex-shrink-0 bg-foreground overflow-hidden m-3">
                      <DynamicVideoPlayer
                        src={getClipUrl(clip.video_url)}
                        poster="/placeholder-video.jpg"
                        overlay={safeZonesEnabled ? <SafeZoneOverlay selection={safeZonePlatform} /> : undefined}
                      />
                    </div>

                    {/* Clip Details */}
                    <div className="p-6 flex-1">
                      <div className="flex items-start justify-between mb-4">
                        <div>
                          <label className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
                            <input
                              type="checkbox"
                              checked={selectedClipIds.includes(clip.id)}
                              onChange={() => handleToggleClipSelection(clip.id)}
                            />
                            Select for merge
                          </label>
                          <h3 className="font-semibold text-lg text-foreground mb-1">
                            {clip.hook_title || `Clip ${clip.clip_order}`}
                          </h3>
                          <div className="flex items-center gap-2 text-sm text-muted-foreground">
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
                            <Badge className={getViralityBgColor(clip.virality_score)}>
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
                        <div className="mb-4 p-3 border border-border">
                          <div className="flex items-center justify-between mb-3">
                            <h4 className="font-medium text-foreground text-sm flex items-center gap-2">
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
                                <span className="flex items-center gap-1 text-muted-foreground">
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
                                <span className="flex items-center gap-1 text-muted-foreground">
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
                                <span className="flex items-center gap-1 text-muted-foreground">
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
                                <span className="flex items-center gap-1 text-muted-foreground">
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

                      <div className="mb-4 p-3 border border-border">
                        <h4 className="font-medium text-foreground text-sm flex items-center gap-2 mb-3">
                          <Sparkles className="w-4 h-4" />
                          Metadata
                        </h4>
                        <ClipMetadataPanel
                          taskId={task?.id ?? ""}
                          clipId={clip.id}
                          title={clip.metadata_title}
                          description={clip.metadata_description}
                          tags={clip.metadata_tags ?? undefined}
                          provider={clip.metadata_provider as "ollama" | "gemini" | null | undefined}
                          generationMs={clip.metadata_generation_ms}
                          stale={clip.metadata_stale}
                          onSaved={triggerAutoRefresh}
                        />
                      </div>

                      {clip.text && (
                        <TranscriptPreview text={clip.text} clipTitle={`Clip ${clip.clip_order}`} />
                      )}

                      <div className="flex items-center gap-2">
                        <div className="inline-flex items-stretch h-8 rounded-md border border-input bg-background shadow-xs overflow-hidden">
                          <button
                            type="button"
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
                              {exportPresets.map((preset) => (
                                <SelectItem key={preset.name} value={preset.name}>
                                  {exportPresetLabel(preset.name)}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        {exportPreset !== "original" &&
                          (() => {
                            const preset = exportPresets.find((p) => p.name === exportPreset);
                            if (!preset) return null;
                            return (
                              <span className="text-xs text-muted-foreground">
                                Target: {preset.target_lufs} LUFS &middot; Max {preset.max_duration_seconds}s
                              </span>
                            );
                          })()}

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

                        <HookVariantCompare
                          taskApiUrl={taskApiUrl}
                          taskId={String(params.id)}
                          clipId={clip.id}
                          currentHookTitle={clip.hook_title}
                          currentHookType={clip.hook_type}
                          initialVariants={clip.hook_title_variants || []}
                          hookStyle={projectHookStyle}
                          captionTemplate={projectCaptionTemplate}
                          availableTemplates={availableTemplates}
                          onApplied={fetchTaskStatus}
                        />

                        <Button
                          size="sm"
                          variant="outline"
                          disabled={regeneratingHookClipId === clip.id}
                          onClick={() => handleRegenerateHook(clip.id)}
                          title="Generate a fresh hook with one click, without opening the comparison dialog"
                        >
                          <RefreshCw className={`w-4 h-4 ${regeneratingHookClipId === clip.id ? "animate-spin" : ""}`} />
                          {regeneratingHookClipId === clip.id ? "Regenerating..." : "Regenerate Hook"}
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

                      {editingClipId === clip.id && (
                        <div className="mt-4 p-3 border border-border space-y-3">
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
            <AlertDialogTitle>Move to Trash?</AlertDialogTitle>
            <AlertDialogDescription>
              This generation and its clips will be moved to Trash. You can restore it later, or permanently delete
              it from there.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteTask} disabled={isDeleting} className="bg-red-600 hover:bg-red-700">
              {isDeleting ? "Moving..." : "Move to Trash"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Export All Clips progress */}
      <Dialog open={exportAllOpen} onOpenChange={(open) => !exportAllRunning && setExportAllOpen(open)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Exporting all clips</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            {(() => {
              const done = Object.values(exportAllStatus).filter((s) => s === "success" || s === "failed").length;
              const total = clips.length;
              const failedCount = Object.values(exportAllStatus).filter((s) => s === "failed").length;
              return (
                <>
                  <p className="text-sm text-muted-foreground">
                    {done} / {total} clips processed
                    {failedCount > 0 && <span className="text-red-600"> &middot; {failedCount} failed</span>}
                  </p>
                  <div className="h-2 w-full rounded-full bg-border overflow-hidden">
                    <div
                      className="h-full bg-foreground transition-all"
                      style={{ width: total ? `${(done / total) * 100}%` : "0%" }}
                    />
                  </div>
                </>
              );
            })()}
            <div className="max-h-72 overflow-y-auto space-y-1.5">
              {clips.map((clip) => {
                const status = exportAllStatus[clip.id] || "pending";
                return (
                  <div key={clip.id} className="flex items-center justify-between text-sm py-1">
                    <span className="truncate flex-1 text-foreground">{clip.filename}</span>
                    {status === "success" && <span className="text-green-600 text-xs">Exported</span>}
                    {status === "failed" && (
                      <div className="flex items-center gap-2">
                        <span className="text-red-600 text-xs">Failed</span>
                        <Button size="sm" variant="outline" onClick={() => handleRetryClipExport(clip)}>
                          Retry
                        </Button>
                      </div>
                    )}
                    {(status === "exporting" || status === "retrying") && (
                      <span className="text-muted-foreground text-xs">{status === "retrying" ? "Retrying…" : "Exporting…"}</span>
                    )}
                    {status === "pending" && <span className="text-muted-foreground text-xs">Pending</span>}
                  </div>
                );
              })}
            </div>
            {!exportAllRunning && (
              <Button className="w-full" variant="outline" onClick={() => setExportAllOpen(false)}>
                Close
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>

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

      {/* Delete Font Confirmation Dialog */}
      <AlertDialog open={!!fontPendingDelete} onOpenChange={(open) => !open && setFontPendingDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete font?</AlertDialogTitle>
            <AlertDialogDescription>
              {fontPendingDelete && `"${fontPendingDelete.display_name}" `}
              will be permanently deleted. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={!!deletingFontName}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void confirmDeleteFont()}
              disabled={!!deletingFontName}
              className="bg-red-600 hover:bg-red-700"
            >
              {deletingFontName ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
