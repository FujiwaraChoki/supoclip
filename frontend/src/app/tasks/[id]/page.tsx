"use client";

import { removeTaskDrafts } from "@/lib/editor/draft-cache";
import { CaptionSizeControl } from "@/components/caption-size-control";

import { useState, useEffect, useCallback, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import { useTaskProgress } from "@/hooks/use-task-progress";
import { StatusBadge, ACTIVE_TASK_STATUSES } from "@/components/app/status-badge";
import { getClipUrl, requestAction, EXPORT_PRESETS } from "@/lib/clip-actions";
import { clipExportFilePath, pollClipExport, triggerFileDownload } from "@/lib/clip-export";
import { followClipJob } from "@/lib/clip-job";
import { downloadAllClips } from "@/lib/clip-archive";
import { trackClipExport } from "@/lib/clip-export-tracking";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { taskPathFromCreate } from "@/lib/task-create";
import { Button } from "@/components/ui/button";
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
import { Switch } from "@/components/ui/switch";
import { useEditStylesInfo } from "@/components/edit-intensity";
import { DEFAULT_EDIT_STYLE, editStyleToJson, INTENSITIES, isFxTemplate, type EditStyle, type Intensity } from "@/lib/fx/core/types";
import { offeredFxTemplates, readEditStyle } from "@/lib/fx/edit-style-request";
import { ApiRequestError, formatSupportMessage, parseApiError } from "@/lib/api-error";
import { useCopyText } from "@/lib/copy-text";
import { useBillingSummary } from "@/hooks/use-billing-summary";
import { UpgradeDialog, upgradeReasonForError, type UpgradeReason } from "@/components/billing/upgrade-prompt";
import { buildFontOptionsPayload, FONT_TEMPLATE_DEFAULT_VALUE } from "@/lib/font-options";
import {
  AlertCircle,
  ArrowLeft,
  Check,
  Clapperboard,
  Clock,
  Edit2,
  ExternalLink,
  Link2Off,
  Loader2,
  MessageSquareText,
  MoreHorizontal,
  PauseCircle,
  RotateCcw,
  Settings2,
  Share2,
  Trash2,
  X,
} from "lucide-react";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import Link from "next/link";
import { ClipTile, ClipFocus, ProcessingPanel, ProcessingSlots, EmptyState, clipProgressFromMessage, sourceTimestamp } from "@/components/app/clip-wall";
import { orderedParts, seriesPartTotal, SeriesView, unfinishedSeries } from "@/components/app/series-view";
import { HeroClipView } from "@/components/app/hero-clip-view";
import { LockedMomentTile, PreviewBanner, usePreviewUnlock } from "@/components/app/preview-locked";
import { isLockedPreview, previewOf, requestUnlockWhenPaid } from "@/lib/onboarding";
import { hasPaidAccess } from "@/lib/subscription-status";
import { DownloadAllButton } from "@/components/app/download-all-button";
import { buildRecreateBody, createActionId, isWorkflowErrorCode, WorkflowFailure } from "@/components/app/workflow-failure";
import { availableWorkflows, useWorkflowsInfo } from "@/components/workflow-picker";
import { intlLocale } from "@/i18n/config";
import { timeAgoLabel } from "@/lib/generations";
import { VIDEO_SOURCE_LABELS, VideoSourceIcon, videoSourceKind, type VideoSourceKind } from "@/components/video-source-icon";
import { cn } from "@/lib/utils";
import { formatClock } from "@/lib/source-range";
import { FontSelectOption, type FontOption } from "@/components/font-select-option";
import { ClipPostList, ClipPublishButton, useSocialPostToasts } from "@/components/clip-publisher";
import {
  dropDeletedTaskFromLists,
  forgetTaskDetail,
  taskDetailLoadCount,
  taskPageKeys,
  updateTaskDetail,
  useBrollAvailable,
  useCaptionTemplates,
  useSocialPublishContext,
  useTaskDetail,
  useTaskFonts,
  useTaskSocialPosts,
  type CaptionTemplateOption,
} from "@/lib/queries/task-detail";
import type { SocialConnection, SocialProviderStatus } from "@/lib/social";
import {
  defaultSort,
  expectedOutputs,
  SCORED,
  SINGLE_OUTPUT,
  taskWorkflow,
  type Workflow,
  type WorkflowClipFields,
  type WorkflowNote,
  type WorkflowPlan,
  type WorkflowTaskFields,
} from "@/lib/workflows";

interface Clip extends WorkflowClipFields {
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
  /** Dynamic edits on this clip (only present when it has them): render level, caption timing, intensity, template. */
  fx_meta?: ClipFxMeta | null;
}

type ClipFxMeta = { fx_level?: string | null; timing?: string | null; intensity?: string | null; template?: string | null };

/** A clip edit running on the video worker (202 from settings, captions, trim, split or merge). */
type ClipJob = {
  id: string; kind: string; clipIds: string[] | "all"; progress: number;
  /** The settings body that queued it, resent as it is by "Try again". */
  request?: string;
  /** The sentence shown once following ended badly. */
  error?: string | null;
  /** What "Try again" does: follow the job again (its status could not be read) or send `request` again. */
  retry?: "resume" | "resend";
};
/** The edit toggles the restyle sheet offers (the create screen has the full set). */
const SHEET_FX_TOGGLES = ["sfx", "music", "zooms", "transitions", "progress_bar"] as const satisfies readonly (keyof EditStyle)[];
const clipJobStorageKey = (taskId: string) => `supoclip.clipJob.${taskId}`;

function readStoredClipJob(taskId: string): ClipJob | null {
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(clipJobStorageKey(taskId)) ?? "null");
    return parsed && typeof parsed.id === "string" && /^[0-9a-f]{32}$/.test(parsed.id)
      ? {
          id: parsed.id, kind: String(parsed.kind ?? "settings"), clipIds: Array.isArray(parsed.clipIds) ? parsed.clipIds : "all", progress: 0,
          request: typeof parsed.request === "string" ? parsed.request : undefined,
        }
      : null;
  } catch {
    return null;
  }
}

function storeClipJob(taskId: string, job: ClipJob | null) {
  try {
    if (job) window.sessionStorage.setItem(clipJobStorageKey(taskId), JSON.stringify({ id: job.id, kind: job.kind, clipIds: job.clipIds, request: job.request }));
    else window.sessionStorage.removeItem(clipJobStorageKey(taskId));
  } catch { /* storage blocked: polling still works for this visit */ }
}

/** The notes a clip's dynamic edits need: approximate caption timing, effects the renderer had to simplify. */
function clipFxNotes(meta: ClipFxMeta | null | undefined): ("approxTiming" | "simplified")[] {
  if (!meta) return [];
  return [
    ...(meta.timing && meta.timing !== "word" ? ["approxTiming" as const] : []),
    ...(meta.fx_level && meta.fx_level !== "full" ? ["simplified" as const] : []),
  ];
}

interface TaskDetails extends WorkflowTaskFields {
  id: string;
  /** Set on a free preview project (onboarding/preview.py). */
  preview?: unknown;
  user_id: string;
  source_id: string;
  source_title: string;
  source_type: string;
  source_url?: string | null;
  status: string;
  progress?: number;
  progress_message?: string;
  clips_count: number;
  source_duration_seconds?: number | null;
  /** Set when only part of the video was clipped; seconds into the original. Clip times are already on the original's timeline. */
  source_start_seconds?: number | null;
  source_end_seconds?: number | null;
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
  include_broll?: boolean;
  share_enabled?: boolean;
  processing_mode?: string | null;
  output_format?: string | null;
  clip_length?: string | null;
  add_subtitles?: boolean | null;
  error_code?: string | null;
  /** Dynamic edits as stored (`tasks.edit_style`); null or absent when off. */
  edit_style?: unknown;
}

// Params each note needs; a note missing one is not shown rather than shown half-filled.
const NOTE_PARAMS: Record<string, string[]> = {
  PART_COUNT_REDUCED: ["requested", "actual"],
  STORY_ARC_SELECTED: ["start", "end"],
  CHAPTERS_LEFT_OUT: ["chapters"],
  TOPIC_THIN: ["count"],
  TOPIC_BROADENED: ["topic", "interpretation"],
};

/** Note params ready for ICU: source times in seconds become M:SS. */
function noteParams(note: WorkflowNote, plan: WorkflowPlan | null, clipCount: number) {
  const params: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(note.params ?? {})) {
    params[key] = (key === "start" || key === "end") && typeof value === "number" ? sourceTimestamp(value) : value;
  }
  if (note.code === "TOPIC_THIN" && params.count === undefined) params.count = plan?.topic?.found ?? clipCount;
  return (NOTE_PARAMS[note.code] ?? []).every((key) => params[key] !== undefined && params[key] !== null) ? params : null;
}

const NO_CLIPS: Clip[] = [];
const NO_FONTS: FontOption[] = [];
const NO_TEMPLATES: CaptionTemplateOption[] = [];
const NO_CONNECTIONS: SocialConnection[] = [];
const NO_PROVIDERS: SocialProviderStatus[] = [];

function sourceLabel(source: VideoSourceKind, uploadLabel: string) {
  return source === "upload" ? uploadLabel : VIDEO_SOURCE_LABELS[source];
}

/** Link back to the YouTube/Twitch/Kick video a task was cut from, when its URL is stored. */
function originalVideoUrl(task: Pick<TaskDetails, "source_type" | "source_url" | "source_start_seconds">): string | null {
  if (videoSourceKind(task.source_type) === "upload" || !task.source_url) return null;
  try {
    const url = new URL(task.source_url);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    // A clipped part opens the original where the part starts.
    const start = Math.floor(task.source_start_seconds ?? 0);
    if (start > 0) {
      const kind = videoSourceKind(task.source_type);
      if (kind === "youtube") url.searchParams.set("t", `${start}s`);
      else if (kind === "twitch") url.searchParams.set("t", `${Math.floor(start / 3600)}h${Math.floor((start % 3600) / 60)}m${start % 60}s`);
    }
    return url.href;
  } catch {
    return null;
  }
}

export default function TaskPage() {
  const t = useTranslations("task");
  const tHome = useTranslations("home");
  const tLibrary = useTranslations("library");
  const copyText = useCopyText();
  const locale = useLocale();
  const params = useParams();
  const searchParams = useSearchParams();
  const alreadyProcessing = searchParams.get("already") === "1";
  const router = useRouter();
  const { data: session } = useSession();
  const queryClient = useQueryClient();
  const taskId = params.id ? String(params.id) : "";
  const taskQuery = useTaskDetail<TaskDetails, Clip>(taskId, (kind, status) =>
    kind === "task" ? t("errors.fetchTask", { status }) : t("errors.fetchClips", { status }));
  const task = taskQuery.data?.task ?? null;
  const clips = taskQuery.data?.clips ?? NO_CLIPS;
  // Progress lives on the cached task: a fetch sets it and SSE events overwrite it.
  const progress = task?.progress ?? 0;
  const progressMessage = task?.progress_message ?? "";
  // A failed load the user asked for (first load, Retry, refresh after an action) replaces the page;
  // a failed background refresh keeps the last task on screen.
  // `errorUpdatedAt` of that failure: it stands until the task loads again (any later success clears it).
  const [foregroundErrorAt, setForegroundErrorAt] = useState<number | null>(null);
  const foregroundFailed = foregroundErrorAt !== null && taskQuery.dataUpdatedAt <= foregroundErrorAt;
  const error = taskQuery.status === "error" && (foregroundFailed || !taskQuery.data)
    ? (taskQuery.error instanceof Error ? taskQuery.error.message : t("errors.load"))
    : null;
  const failureCode = task?.status === "error" && isWorkflowErrorCode(task.error_code) ? task.error_code : null;
  // Source failures a retry can't fix get their own explanation.
  const failedBody = task?.error_code === "youtube_age_restricted"
    ? t("failedBodyAgeRestricted")
    : task?.error_code === "youtube_geo_restricted"
      ? t("failedBodyGeoRestricted")
      : t("failedBody");
  // Next steps on the failure screen stay inside what this user may make.
  const workflowsInfo = useWorkflowsInfo(Boolean(failureCode));
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
  const [projectPauseThresholdMs, setProjectPauseThresholdMs] = useState("1200");
  const [projectRemoveFillerWords, setProjectRemoveFillerWords] = useState(false);
  const [projectIncludeBroll, setProjectIncludeBroll] = useState(false);
  // Dynamic edits: what the task has stored, and the sheet's working copy.
  const [storedEditStyle, setStoredEditStyle] = useState<EditStyle>(DEFAULT_EDIT_STYLE);
  const [projectEditStyle, setProjectEditStyle] = useState<EditStyle>(DEFAULT_EDIT_STYLE);
  const [clipJob, setClipJob] = useState<ClipJob | null>(null);
  const brollAvailable = useBrollAvailable();
  const [projectFilteredWords, setProjectFilteredWords] = useState("");
  const [isApplyingSettings, setIsApplyingSettings] = useState(false);
  const [settingsSheetOpen, setSettingsSheetOpen] = useState(false);
  const availableFonts = useTaskFonts().data ?? NO_FONTS;
  const [deletingFontName, setDeletingFontName] = useState<string | null>(null);
  const [fontToDelete, setFontToDelete] = useState<FontOption | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [billingSummary] = useBillingSummary(Boolean(session?.user?.id));
  const [upgradeReason, setUpgradeReason] = useState<UpgradeReason | null>(null);
  // A free preview (onboarding): its best clip is here, the rest are listed until a plan unlocks them.
  const preview = previewOf(task);
  const lockedPreview = isLockedPreview(task) && task?.status === "completed";
  const paidReturn = searchParams.get("billing") === "success";
  const previewUnlock = usePreviewUnlock(taskId, lockedPreview, paidReturn, () => {
    window.history.replaceState(null, "", `/tasks/${taskId}`);
    void taskQuery.refetch();
  });
  const askToUnlock = () => {
    if (hasPaidAccess(billingSummary?.plan, billingSummary?.subscription_status, billingSummary?.subscription_provider)) {
      void previewUnlock.unlock({ retryUntilPaid: false });
      return;
    }
    void requestUnlockWhenPaid(taskId);
    setUpgradeReason({ kind: "preview", more: Math.max(1, (preview?.found ?? 2) - 1) });
  };
  const [sortBy, setSortBy] = useState<"score" | "timeline">("score");
  // The first load picks the workflow's default order; after that the user's choice stays.
  const sortChosen = useRef(false);
  const [focusIndex, setFocusIndex] = useState<number | null>(null);
  const [isDownloadingAll, setIsDownloadingAll] = useState(false);

  const taskApiUrl = "/api/tasks";

  // Publishing context loads once the task is done; posts poll while one is in flight or scheduled.
  const socialEnabled = Boolean(session?.user?.id) && task?.status === "completed";
  const socialContext = useSocialPublishContext(socialEnabled);
  const socialConnections = socialContext.connections ?? NO_CONNECTIONS;
  const socialProviders = socialContext.providers ?? NO_PROVIDERS;
  const { posts: socialPosts, reload: loadSocialPosts } = useTaskSocialPosts(taskId, socialEnabled);
  useSocialPostToasts(socialPosts);

  const buildSupportError = useCallback(async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  }, []);

  /** Fill the restyle sheet from the task as stored; the first load also picks the default sort. */
  const syncSettingsFromTask = useCallback((taskData: TaskDetails) => {
    setProjectFontFamily(taskData.font_family ?? null);
    setProjectFontSize(typeof taskData.font_size === "number" ? taskData.font_size : null);
    setProjectFontColor(taskData.font_color ?? null);
    setProjectCaptionTemplate(taskData.caption_template || "default");
    setProjectCutLongPauses(Boolean(taskData.cut_long_pauses));
    setProjectPauseThresholdMs(String(taskData.pause_threshold_ms || 1200));
    setProjectRemoveFillerWords(Boolean(taskData.remove_filler_words));
    setProjectIncludeBroll(Boolean(taskData.include_broll));
    setProjectFilteredWords((taskData.filtered_words || []).join(", "));
    setStoredEditStyle(readEditStyle(taskData.edit_style));
    setProjectEditStyle(readEditStyle(taskData.edit_style));
    if (!sortChosen.current) {
      sortChosen.current = true;
      setSortBy(defaultSort(taskWorkflow(taskData)));
    }
  }, []);

  // The sheet fills from the task as soon as there is one, cached from an earlier visit included. The first
  // load of this visit fills it again, unless the sheet has been opened by then (it never overwrites the user).
  const [firstLoadOfVisit] = useState(() => taskDetailLoadCount() + 1);
  const sheetSync = useRef<"none" | "cached" | "done">("none");
  const shownTask = taskQuery.data?.task;
  const shownLoad = taskQuery.data?.loadNumber ?? 0;
  useEffect(() => {
    if (!shownTask || sheetSync.current === "done") return;
    const fresh = shownLoad >= firstLoadOfVisit;
    if (sheetSync.current === "cached" && (settingsSheetOpen || !fresh)) {
      if (settingsSheetOpen) sheetSync.current = "done";
      return;
    }
    syncSettingsFromTask(shownTask);
    sheetSync.current = fresh ? "done" : "cached";
  }, [shownTask, shownLoad, firstLoadOfVisit, settingsSheetOpen, syncSettingsFromTask]);

  const { refetch: refetchTask } = taskQuery;
  /** Reload the task the user is waiting on: a failure shows the error page, success refills the sheet. */
  const reloadTask = useCallback(async () => {
    const result = await refetchTask();
    setForegroundErrorAt(result.status === "error" ? result.errorUpdatedAt : null);
    if (result.status === "success") syncSettingsFromTask(result.data.task);
  }, [refetchTask, syncSettingsFromTask]);
  /** Refresh behind the scenes (SSE reconcile, finished clip job): a failure keeps what is on screen. */
  const refreshTaskInBackground = useCallback(async () => {
    await refetchTask({ cancelRefetch: false });
  }, [refetchTask]);

  // Dynamic edits are offered (and animated templates asked for) only when the server says they're on for this user.
  const editStylesInfo = useEditStylesInfo(Boolean(session?.user?.id));
  const fxEnabled = editStylesInfo?.enabled === true;
  const availableTemplates = useCaptionTemplates(fxEnabled).data ?? NO_TEMPLATES;

  const { reconnecting } = useTaskProgress<Clip>({
    taskId,
    active: ACTIVE_TASK_STATUSES.includes(task?.status || ""),
    refresh: refreshTaskInBackground,
    onProgress: (data) => updateTaskDetail<TaskDetails, Clip>(queryClient, taskId, (current) => ({
      ...current,
      task: {
        ...current.task,
        ...(typeof data.progress === "number" ? { progress: data.progress } : {}),
        ...(typeof data.message === "string" ? { progress_message: data.message } : {}),
      },
    })),
    onClip: (clip) => updateTaskDetail<TaskDetails, Clip>(queryClient, taskId, (current) => ({
      ...current,
      clips: [...current.clips.filter((item) => item.id !== clip.id), clip].sort((a, b) => a.clip_order - b.clip_order),
    })),
  });

  const runAction = async (name: string, action: () => Promise<void>) => {
    if (pendingAction) return;
    setPendingAction(name);
    try { await action(); }
    catch (err) { toast.error(err instanceof Error ? err.message : t("errors.action")); }
    finally { setPendingAction(null); }
  };

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const getHookTypeLabel = (hookType: string | null) => {
    const labels: Record<string, string> = {
      question: t("hooks.question"),
      statement: t("hooks.statement"),
      statistic: t("hooks.statistic"),
      story: t("hooks.story"),
      contrast: t("hooks.contrast"),
      none: t("hooks.none"),
    };
    return labels[hookType || "none"] || hookType || t("hooks.fallback");
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
        updateTaskDetail<TaskDetails, Clip>(queryClient, taskId, (current) => ({ ...current, task: { ...current.task, source_title: editedTitle } }));
        setIsEditing(false);
      } else {
        toast.error(await buildSupportError(response, t("errors.updateTitle")));
      }
    } catch (err) {
      console.error("Error updating title:", err);
      toast.error(err instanceof Error ? err.message : t("errors.updateTitle"));
    }
  };

  // A deleted task's cache goes once the page has left, so Back shows the server's answer, not the old task.
  const taskDeleted = useRef(false);
  useEffect(() => () => {
    if (taskDeleted.current) forgetTaskDetail(queryClient, taskId);
  }, [queryClient, taskId]);

  const handleDeleteTask = async () => {
    if (!session?.user?.id || !params.id) return;

    setIsDeleting(true);
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}`, {
        method: "DELETE",
      });

      if (response.ok) {
        removeTaskDrafts(String(params.id));
        taskDeleted.current = true;
        dropDeletedTaskFromLists(queryClient, taskId, session.user.id);
        router.push("/list");
      } else {
        toast.error(await buildSupportError(response, t("errors.deleteTask")));
      }
    } catch (err) {
      console.error("Error deleting task:", err);
      toast.error(err instanceof Error ? err.message : t("errors.deleteTask"));
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
        removeTaskDrafts(String(params.id), clipId);
        updateTaskDetail<TaskDetails, Clip>(queryClient, taskId, (current) => ({ ...current, clips: current.clips.filter((clip) => clip.id !== clipId) }));
        setDeletingClipId(null);
      } else {
        toast.error(await buildSupportError(response, t("errors.deleteClip")));
      }
    } catch (err) {
      console.error("Error deleting clip:", err);
      toast.error(err instanceof Error ? err.message : t("errors.deleteClip"));
    }
  };

  /** A clip job is queued or running: its clips are locked and no second re-render can be queued. */
  const clipJobActive = Boolean(clipJob && !clipJob.error);

  const handleApplyProjectSettings = async () => {
    if (!session?.user?.id || !params.id) return;
    const fontOptions = buildFontOptionsPayload(projectFontFamily, projectFontSize, projectFontColor);
    const parsedPauseThreshold = Number(projectPauseThresholdMs || "1200");
    const safePauseThreshold = Number.isFinite(parsedPauseThreshold)
      ? Math.max(250, Math.min(3000, Math.round(parsedPauseThreshold)))
      : 1200;
    const normalizedFilteredWords = projectFilteredWords
      .split(",")
      .map((word) => word.trim().toLowerCase())
      .filter(Boolean);

    // Sent only when it changed: an omitted style keeps the stored one, `{}` turns edits off.
    const nextStyle = editStyleToJson(projectEditStyle);
    const styleChanged = JSON.stringify(nextStyle) !== JSON.stringify(editStyleToJson(storedEditStyle));

    await submitProjectSettings(JSON.stringify({
      ...fontOptions,
      caption_template: projectCaptionTemplate,
      cut_long_pauses: projectCutLongPauses,
      pause_threshold_ms: safePauseThreshold,
      remove_filler_words: projectRemoveFillerWords,
      // Omitted when B-roll isn't offered, so the task keeps its setting.
      ...(brollAvailable ? { include_broll: projectIncludeBroll } : {}),
      filtered_words: normalizedFilteredWords,
      ...(styleChanged ? { edit_style: nextStyle } : {}),
      apply_to_existing: true,
    }), projectEditStyle);
  };

  /** Sends a settings body. "Try again" passes the failed job's own body, without `style` (it is stored already). */
  const submitProjectSettings = async (body: string, style?: EditStyle) => {
    // One re-render at a time: a second request would queue every clip again.
    if (!params.id || isApplyingSettings || clipJobActive) return;
    setIsApplyingSettings(true);
    try {
      const response = await fetch(`${taskApiUrl}/${params.id}/settings`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body,
      });
      if (response.status === 409) {
        // A re-render started somewhere else (another tab or device): follow that one instead of failing.
        const refused = (await response.clone().json().catch(() => null)) as
          { detail?: { code?: string; job_id?: string; kind?: string } } | null;
        const inFlight = refused?.detail;
        if (inFlight?.code === "CLIP_JOB_IN_PROGRESS" && inFlight.job_id) {
          startClipJob({ id: inFlight.job_id, kind: inFlight.kind ?? "settings", clipIds: "all", progress: 0 });
          setSettingsSheetOpen(false);
          toast.message(t("fx.jobInProgress"));
          return;
        }
      }
      if (!response.ok) {
        toast.error(await buildSupportError(response, t("errors.applySettings")));
        return;
      }
      if (response.status === 202) {
        // Clips with dynamic edits re-render on the video worker: mark them and poll the job.
        const queued = (await response.json()) as { job_id?: string; kind?: string };
        if (queued.job_id) startClipJob({ id: queued.job_id, kind: queued.kind ?? "settings", clipIds: "all", progress: 0, request: body });
        if (style) setStoredEditStyle(style);
        setSettingsSheetOpen(false);
        toast.success(t("fx.queued"));
        return;
      }
      await reloadTask();
      setSettingsSheetOpen(false);
      toast.success(t("toasts.settingsApplied"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("errors.applySettings"));
    } finally {
      setIsApplyingSettings(false);
    }
  };

  const startClipJob = useCallback((job: ClipJob) => {
    if (params.id) storeClipJob(String(params.id), job);
    setClipJob(job);
  }, [params.id]);

  // A job queued before a reload keeps being followed.
  useEffect(() => {
    if (!params.id) return;
    const stored = readStoredClipJob(String(params.id));
    if (stored) setClipJob(stored);
  }, [params.id]);

  const clipJobId = clipJob && !clipJob.error ? clipJob.id : null;
  useEffect(() => {
    if (!clipJobId || !params.id) return;
    const taskId = String(params.id);
    const fail = (error: string, retry?: (job: ClipJob) => ClipJob["retry"]) =>
      setClipJob((current) => current && current.id === clipJobId ? { ...current, error, retry: retry?.(current) } : current);
    return followClipJob(`${taskApiUrl}/${taskId}/clip-jobs/${clipJobId}`, {
      onProgress: (progress) => setClipJob((current) => current && current.id === clipJobId ? { ...current, progress } : current),
      onEnd: (end) => {
        // Its status could not be read, but the job may still be running: it stays stored and can be followed again.
        if (end.status === "unreachable") return fail(t("fx.jobUnreachable"), () => "resume");
        storeClipJob(taskId, null);
        if (end.status === "denied") return fail(t("fx.jobDenied"));
        if (end.status === "failed") {
          const known = end.code && t.has(`fx.jobErrors.${end.code}`) ? t(`fx.jobErrors.${end.code}`) : null;
          return fail(t("fx.jobFailed", { message: known ?? (end.message || t("errors.applySettings")) }), (job) => (job.request ? "resend" : undefined));
        }
        setClipJob(null);
        void refreshTaskInBackground().then(() => { if (end.status === "done") toast.success(t("fx.applied")); });
      },
    });
  }, [clipJobId, params.id, taskApiUrl, refreshTaskInBackground, t]);

  const dismissClipJob = () => {
    if (params.id) storeClipJob(String(params.id), null);
    setClipJob(null);
  };
  const retryClipJob = () => {
    if (!clipJob?.error) return;
    if (clipJob.retry === "resume") {
      setClipJob({ ...clipJob, error: null, retry: undefined });
    } else if (clipJob.retry === "resend" && clipJob.request) {
      const { request } = clipJob;
      setClipJob(null);
      void submitProjectSettings(request);
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
        throw new Error(await buildSupportError(response, t("errors.deleteFont")));
      }

      queryClient.setQueryData<FontOption[]>(taskPageKeys.fonts, (current) => current?.filter((item) => item.name !== font.name));
      if (projectFontFamily === font.name) {
        // The deleted font was in use — fall back to the caption template's own font.
        setProjectFontFamily(null);
      }
    } catch (deleteError) {
      toast.error(deleteError instanceof Error ? deleteError.message : t("errors.deleteFont"));
    } finally {
      setDeletingFontName(null);
    }
  };

  const handleExportClip = async (clipId: string, fallbackFilename: string) => {
    if (!session?.user?.id || !task?.id) return;

    const taskId = task.id;
    const job = await pollClipExport({
      start: () => fetch(`${taskApiUrl}/${taskId}/clips/${clipId}/exports`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset: exportPreset }),
        cache: "no-store",
      }),
      status: (exportId) => fetch(`${taskApiUrl}/${taskId}/clips/${clipId}/exports/${exportId}`, {
        cache: "no-store",
      }),
      readError: (response) => buildSupportError(response, t("errors.exportClip")),
      fallbackMessage: t("errors.exportClip"),
      timeoutMessage: t("errors.exportClip"),
    });
    triggerFileDownload(
      clipExportFilePath(taskApiUrl, taskId, clipId, job.id),
      `${fallbackFilename.replace(/\.mp4$/i, "")}_${exportPreset}.mp4`,
    );
    void trackClipExport({ userId: session.user.id, surface: "task", format: exportPreset });
  };

  const downloadOriginal = (clip: Clip) => {
    const link = document.createElement("a");
    const url = getClipUrl(clip.video_url, clip.filename);
    link.href = `${url}${url.includes("?") ? "&" : "?"}download=true`;
    link.download = clip.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    if (session?.user?.id) void trackClipExport({ userId: session.user.id, surface: "task", format: "original" });
  };

  const handleDownloadClip = (clip: Clip) => {
    if (exportPreset === "original") {
      downloadOriginal(clip);
      return;
    }
    void runAction(clip.id, () => handleExportClip(clip.id, clip.filename));
  };

  const handleDownloadAll = async () => {
    if (!task?.id || isDownloadingAll) return;

    setIsDownloadingAll(true);
    try {
      await downloadAllClips({
        taskApiUrl,
        taskId: task.id,
        readError: (response) => buildSupportError(response, t("errors.downloadAll")),
        fallbackMessage: t("errors.downloadAll"),
      });
      if (session?.user?.id) void trackClipExport({ userId: session.user.id, surface: "task", format: "zip" });
    } catch (downloadError) {
      toast.error(downloadError instanceof Error ? downloadError.message : t("errors.downloadAll"));
    } finally {
      setIsDownloadingAll(false);
    }
  };

  const handleResume = () => {
    if (!task) return;
    void runAction("retry", async () => {
      try {
        await requestAction(`${taskApiUrl}/${task.id}/resume`, "POST");
      } catch (err) {
        const reason = err instanceof ApiRequestError ? upgradeReasonForError(err.code, err.detail, undefined) : null;
        if (!reason) throw err;
        setUpgradeReason(reason);
        return;
      }
      // Finished clips stay and the worker renders only what's missing.
      await reloadTask();
      toast.success(t("toasts.retryQueued"));
    });
  };

  // A new generation from the same source and settings (failure next steps).
  const handleStartWorkflow = (workflow: Workflow, topic?: string) => {
    if (!task?.source_url) return;
    void runAction(createActionId(workflow, topic), async () => {
      const response = await fetch("/api/tasks/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildRecreateBody(
          task,
          { workflow, topic },
          workflowsInfo?.workflows.find((entry) => entry.id === workflow)?.customizable !== false && workflowsInfo?.enabled !== false,
          // The task's dynamic edits carry over only while they're on for this user (same sanitiser as the create screen).
          editStylesInfo,
        )),
      });
      if (!response.ok) {
        const info = await parseApiError(response, t("errors.createTask"));
        const reason = upgradeReasonForError(info.code, info.detail, undefined);
        if (reason) {
          setUpgradeReason(reason);
          return;
        }
        throw new Error(formatSupportMessage(info));
      }
      const created = (await response.json()) as { task_id?: string; duplicate?: boolean; code?: string };
      if (!created.task_id) throw new Error(t("errors.createTask"));
      window.location.href = taskPathFromCreate(created);
    });
  };

  const handleCopyShareLink = async () => {
    if (!task?.id || shareState === "copying") return;

    setShareState("copying");
    try {
      const response = await fetch(`${taskApiUrl}/${task.id}/share`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, t("errors.share")));
      }

      const data = (await response.json()) as { share_path: string };
      const shareUrl = new URL(data.share_path, window.location.origin).toString();
      updateTaskDetail<TaskDetails, Clip>(queryClient, taskId, (current) => ({ ...current, task: { ...current.task, share_enabled: true } }));
      const outcome = await copyText(shareUrl);
      if (outcome !== "copied") {
        setShareState("idle");
        return;
      }
      setShareState("copied");
      window.setTimeout(() => setShareState("idle"), 2500);
    } catch (shareError) {
      setShareState("idle");
      toast.error(shareError instanceof Error ? shareError.message : t("errors.share"));
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
        throw new Error(await buildSupportError(response, t("errors.disableShare")));
      }
      updateTaskDetail<TaskDetails, Clip>(queryClient, taskId, (current) => ({ ...current, task: { ...current.task, share_enabled: false } }));
    } catch (revokeError) {
      toast.error(revokeError instanceof Error ? revokeError.message : t("errors.disableShare"));
    } finally {
      setIsRevokingShare(false);
    }
  };

  if (taskQuery.isPending) return <PageLoading />;
  if (error) return <PageError message={error} retry={() => void reloadTask()} />;

  const readOnly = Boolean(session?.user?.id && task?.user_id && session.user.id !== task.user_id);
  const sourceVideoUrl = task ? originalVideoUrl(task) : null;
  const hasClips = task?.status === "completed" && clips.length > 0;
  const isActive = task?.status === "processing" || task?.status === "queued";
  const workflow = taskWorkflow(task);
  const plan = task?.workflow_plan ?? null;
  const isSeries = workflow === "series";
  // Series keep story order and recaps/trailers are one video, so only highlights and topic clips sort.
  const sortable = !isSeries && !SINGLE_OUTPUT.has(workflow);
  const sortedClips = isSeries
    ? orderedParts(clips)
    : sortable && sortBy === "score"
      ? [...clips].sort((a, b) => (b.virality_score ?? 0) - (a.virality_score ?? 0) || a.clip_order - b.clip_order)
      : clips;
  const focusedClip = focusIndex !== null ? sortedClips[focusIndex] : null;
  const bestScore = SCORED.has(workflow) ? clips.reduce((best, clip) => Math.max(best, clip.virality_score || 0), 0) : 0;
  const totalSeconds = clips.reduce((sum, clip) => sum + (clip.duration || 0), 0);
  const partTotal = isSeries ? seriesPartTotal(plan, clips) : undefined;
  // A split recap or trailer is no longer one video; show its pieces as a grid.
  const heroClip = SINGLE_OUTPUT.has(workflow) && clips.length === 1 ? clips[0] : null;
  const seriesIncomplete = isSeries && unfinishedSeries(task?.status, plan, clips) !== null;
  const topicName = plan?.topic?.query || task?.workflow_options?.topic || plan?.title || "";
  const instructions = task?.workflow_options?.instructions?.trim();
  const expectedTotal = task
    ? (isSeries && plan?.parts?.length) || expectedOutputs(workflow, task.source_duration_seconds, task.workflow_options?.part_count)
    : 0;
  const workflowBadge = workflow === "highlights" ? null
    : workflow === "series"
      ? (partTotal || task?.workflow_options?.part_count
        ? t("workflowBadge.series", { count: partTotal || task?.workflow_options?.part_count || 0 })
        : t("workflowBadge.seriesPending"))
      : workflow === "topic" ? t("workflowBadge.topic", { topic: topicName }) : t(`workflowBadge.${workflow}`);
  const planNotes = (plan?.notes ?? []).flatMap((note) => {
    // The topic header already says when coverage is thin.
    if (note.code === "TOPIC_THIN" || !t.has(`workflowNotes.${note.code}`)) return [];
    const values = noteParams(note, plan, clips.length);
    return values ? [{ code: note.code, text: t(`workflowNotes.${note.code}`, values) }] : [];
  });

  // Dynamic edits in the restyle sheet: offered while the flag is on, and always to a task that already has them
  // (turning them off or keeping them is never gated).
  const editsOffered = fxEnabled || storedEditStyle.intensity !== "off";
  const sheetIntensities = fxEnabled ? INTENSITIES : INTENSITIES.filter((level) => level === "off" || level === storedEditStyle.intensity);
  const offeredFx = new Set<string>(offeredFxTemplates(editStylesInfo));
  // Animated templates appear only while dynamic edits are on, plus the task's own (so its name still shows).
  const sheetTemplates = availableTemplates.filter((template) => !isFxTemplate(template.id) || offeredFx.has(template.id) || template.id === task?.caption_template);
  const templateName = (id: string) => {
    const key = `animatedCaptions.templates.${id}.name`;
    return isFxTemplate(id) && tHome.has(key) ? tHome(key) : availableTemplates.find((template) => template.id === id)?.name;
  };
  const templateDescription = (id: string) => {
    const key = `animatedCaptions.templates.${id}.description`;
    return isFxTemplate(id) && tHome.has(key) ? tHome(key) : undefined;
  };

  const formatSelect = (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span className="hidden sm:inline">{t("downloadAs")}</span>
      <Select value={exportPreset} onValueChange={setExportPreset}>
        <SelectTrigger size="sm" aria-label={t("downloadFormat")} className="h-8 min-w-[112px] bg-background"><SelectValue /></SelectTrigger>
        <SelectContent align="end">
          <SelectItem value="original">{t("original")}</SelectItem>
          {EXPORT_PRESETS.map((preset) => <SelectItem key={preset.id} value={preset.id}>{preset.label}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );

  // One lock for every view: a clip being re-rendered can't be edited, deleted, published or opened in the studio.
  const renderingClip = (clip: Clip) => Boolean(clipJob && clipJobActive && (clipJob.clipIds === "all" || clipJob.clipIds.includes(clip.id)));

  const publishButton = (clip: Clip) => task?.status === "completed" && !renderingClip(clip) ? (
    <ClipPublishButton
      taskId={task.id}
      clip={clip}
      connections={socialConnections}
      providers={socialProviders}
      onChanged={loadSocialPosts}
    />
  ) : null;

  const focusedRendering = focusedClip ? renderingClip(focusedClip) : false;
  const renderTile = (clip: Clip, index: number) => renderingClip(clip) ? (
    <div key={clip.id} className="relative" aria-busy="true">
      <ClipTile
        clip={clip}
        taskId={task?.id ?? String(params.id)}
        busy
        best={bestScore > 0 && clip.virality_score === bestScore}
        partTotal={partTotal}
        onOpen={() => setFocusIndex(index)}
        onDownload={() => handleDownloadClip(clip)}
        editable={false}
      />
      <div className="pointer-events-none absolute inset-x-0 top-0 flex aspect-[9/16] items-center justify-center rounded-xl bg-background/60 backdrop-blur-[2px]">
        <span className="flex items-center gap-1.5 rounded-full bg-background/90 px-2.5 py-1 text-xs font-medium shadow-sm">
          <Loader2 className="size-3.5 animate-spin" />{t("fx.rendering")}
        </span>
      </div>
    </div>
  ) : (
    <ClipTile
      key={clip.id}
      clip={clip}
      taskId={task?.id ?? String(params.id)}
      busy={pendingAction === clip.id}
      best={bestScore > 0 && clip.virality_score === bestScore}
      partTotal={partTotal}
      onOpen={() => setFocusIndex(index)}
      onDownload={() => handleDownloadClip(clip)}
      onDelete={readOnly ? undefined : () => setDeletingClipId(clip.id)}
      editable={task?.status === "completed" && !readOnly && !lockedPreview}
    />
  );

  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-8 md:py-8">
      <Link href="/list" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" />{t("back")}
      </Link>

      {task && (
        <header className="mt-3 flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
          <div className="min-w-0 flex-[1_1_32rem]">
            {isEditing ? (
              <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); void handleEditTitle(); }}>
                <Input
                  value={editedTitle}
                  onChange={(e) => setEditedTitle(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Escape") { setIsEditing(false); setEditedTitle(task.source_title); } }}
                  className="h-auto py-1 font-display text-xl font-bold md:text-2xl"
                  aria-label={t("titleLabel")}
                  autoFocus
                />
                <Button type="submit" aria-label={t("saveTitle")} size="icon" disabled={!editedTitle.trim()}><Check className="size-4" /></Button>
                <Button type="button" size="icon" variant="ghost" aria-label={t("cancelTitle")} onClick={() => { setIsEditing(false); setEditedTitle(task.source_title); }}><X className="size-4" /></Button>
              </form>
            ) : (
              <div className="group flex items-start gap-2">
                <div className="min-w-0">
                  <h1 className={cn("min-w-0 break-words font-display text-xl font-bold tracking-tight md:text-2xl", isActive && "title-sheen")}>{task.source_title}</h1>
                  {plan?.title && workflow !== "topic" && workflow !== "highlights" && (
                    <p className="mt-0.5 text-sm text-muted-foreground">{plan.title}</p>
                  )}
                </div>
                {!readOnly && (
                  <Button size="icon-sm" variant="ghost" aria-label={t("editTitle")} className="mt-0.5 shrink-0 text-muted-foreground opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100"
                    onClick={() => { setIsEditing(true); setEditedTitle(task.source_title); }}>
                    <Edit2 className="size-4" />
                  </Button>
                )}
              </div>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted-foreground">
              {!isActive && <StatusBadge status={task.status} />}
              {workflowBadge && (
                <span className="max-w-full truncate rounded-full border bg-background px-2.5 py-0.5 text-xs font-medium text-foreground">{workflowBadge}</span>
              )}
              {sourceVideoUrl ? (
                <a href={sourceVideoUrl} target="_blank" rel="noopener noreferrer" title={t("originalVideo")} className="flex items-center gap-1.5 hover:text-foreground">
                  <VideoSourceIcon source={videoSourceKind(task.source_type)} className="size-3.5" aria-hidden />{sourceLabel(videoSourceKind(task.source_type), tLibrary("card.upload"))}<ExternalLink className="size-3" aria-hidden />
                </a>
              ) : (
                <span className="flex items-center gap-1.5"><VideoSourceIcon source={videoSourceKind(task.source_type)} className="size-3.5" aria-hidden />{sourceLabel(videoSourceKind(task.source_type), tLibrary("card.upload"))}</span>
              )}
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="flex cursor-default items-center gap-1.5"><Clock className="size-3.5" />{timeAgoLabel(task.created_at, locale)}</span>
                </TooltipTrigger>
                <TooltipContent>
                  {new Date(task.created_at).toLocaleString(intlLocale(locale), { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}
                </TooltipContent>
              </Tooltip>
              {task.source_start_seconds != null && task.source_end_seconds != null && (
                <span className="tabular-nums" data-testid="source-range">
                  {t("sourceRange", { start: formatClock(task.source_start_seconds), end: formatClock(task.source_end_seconds) })}
                </span>
              )}
              {isActive && task.source_duration_seconds ? <span className="tabular-nums">{t("sourceLength", { duration: formatDuration(task.source_duration_seconds) })}</span> : null}
              {clips.length > 0 && (
                <span className="tabular-nums">
                  {/* Series, recaps and trailers already name their output in the badge. */}
                  {workflow === "highlights" || workflow === "topic"
                    ? t("clipsTotal", { count: clips.length, duration: formatDuration(totalSeconds) })
                    : t("outputLength", { duration: formatDuration(totalSeconds) })}
                </span>
              )}
              {instructions && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button type="button" className="flex items-center gap-1.5 hover:text-foreground"><MessageSquareText className="size-3.5" />{t("instructionsUsed")}</button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-sm whitespace-pre-line text-left">{instructions}</TooltipContent>
                </Tooltip>
              )}
              {readOnly && <span>{t("readOnly")}</span>}
            </div>
            {planNotes.length > 0 && (
              <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
                {planNotes.map((note) => <li key={note.code}>{note.text}</li>)}
              </ul>
            )}
          </div>

          {!readOnly && (
          <div className="flex shrink-0 items-center gap-2">
            {isActive && (
              <Button size="sm" variant="outline" disabled={pendingAction !== null}
                onClick={() => void runAction("cancel", async () => {
                  await requestAction(`${taskApiUrl}/${task.id}/cancel`, "POST");
                  await reloadTask();
                  toast.success(t("toasts.cancelled"));
                })}>
                {t("cancel")}
              </Button>
            )}
            {/* Workflow failures and an unfinished series offer their own next step in the body. */}
            {((task.status === "cancelled" && !seriesIncomplete) || (task.status === "error" && !failureCode)) && (
              <Button size="sm" disabled={pendingAction !== null} onClick={handleResume}>
                <RotateCcw className="size-4" />{t("retryGeneration")}
              </Button>
            )}
            {hasClips && (
              <>
                <Button size="sm" variant="outline" onClick={handleCopyShareLink} disabled={shareState === "copying"} aria-live="polite">
                  {shareState === "copied" ? <Check className="size-4" /> : <Share2 className="size-4" />}
                  <span className="max-sm:sr-only">{shareState === "copying" ? t("creatingLink") : shareState === "copied" ? t("linkCopied") : t("copyShare")}</span>
                </Button>
                {!lockedPreview && (
                  <Button size="sm" asChild>
                    <Link href={`/tasks/${task.id}/edit`}><Clapperboard className="size-4" />{t("openEditor")}</Link>
                  </Button>
                )}
              </>
            )}
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <Button size="icon-sm" variant="ghost" aria-label={t("moreActions")}>
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {hasClips && (
                  <>
                    <DropdownMenuItem onSelect={() => setSettingsSheetOpen(true)} disabled={clipJobActive}>
                      <Settings2 />{t("restyle")}
                    </DropdownMenuItem>
                    {task.share_enabled && (
                      <DropdownMenuItem onSelect={() => void handleRevokeShareLink()} disabled={isRevokingShare}>
                        <Link2Off />{isRevokingShare ? t("disabling") : t("disableShare")}
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuSeparator />
                  </>
                )}
                <DropdownMenuItem variant="destructive" onSelect={() => setShowDeleteDialog(true)}>
                  <Trash2 />{t("deleteGeneration")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          )}
        </header>
      )}

      <div className="mt-8">
        {alreadyProcessing && task && (task.status === "queued" || task.status === "processing") && (
          <p role="status" className="mb-4 text-sm text-muted-foreground">{t("alreadyProcessing")}</p>
        )}
        {reconnecting && <p role="status" className="mb-4 text-sm text-muted-foreground">{readOnly ? t("reconnectingOther") : t("reconnectingOwn")}</p>}
        {clipJob && (
          clipJob.error ? (
            <div role="alert" className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/20 bg-destructive/5 p-4">
              <p className="text-sm">{clipJob.error}</p>
              <span className="flex gap-2">
                <Button size="sm" variant="ghost" onClick={dismissClipJob}>{t("cancel")}</Button>
                {clipJob.retry && (
                  <Button size="sm" onClick={retryClipJob} disabled={isApplyingSettings || clipJobActive}>
                    <RotateCcw className="size-4" />{t("fx.retry")}
                  </Button>
                )}
              </span>
            </div>
          ) : (
            <div role="status" className="mb-6 flex items-center gap-3 rounded-lg border bg-muted/40 p-3 text-sm">
              <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />
              <span className="min-w-0 flex-1">{t("fx.rendering")}</span>
              {clipJob.progress > 0 && <span className="tabular-nums text-muted-foreground">{Math.round(clipJob.progress * 100)}%</span>}
            </div>
          )
        )}

        {isActive && task ? (
          <div className="space-y-8">
            <ProcessingPanel status={task.status} progress={progress} message={progressMessage} workflow={workflow}
              phaseSince={Date.parse(task.updated_at)} sourceSeconds={task.source_duration_seconds}
              upload={videoSourceKind(task.source_type) === "upload"} outputs={preview && !preview.unlocked_at ? 1 : Math.max(clips.length, expectedTotal)} />
            <ProcessingSlots status={task.status} progress={progress} message={progressMessage}
              className={SINGLE_OUTPUT.has(workflow) ? "mx-auto max-w-[16rem] grid-cols-1 sm:grid-cols-1 lg:grid-cols-1 xl:grid-cols-1" : undefined}
              total={preview && !preview.unlocked_at ? 1 : clipProgressFromMessage(progressMessage)?.total ?? Math.max(clips.length, expectedTotal)}>
              {sortedClips.map(renderTile)}
            </ProcessingSlots>
          </div>
        ) : !task ? (
          <div className="flex min-h-[50vh] items-center justify-center"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
        ) : task.status === "cancelled" && clips.length === 0 ? (
          <EmptyState icon={<PauseCircle className="size-7" />} title={t("cancelledTitle")} body={t("cancelledBody")} />
        ) : failureCode ? (
          <WorkflowFailure
            code={failureCode}
            workflow={workflow}
            plan={plan}
            topic={task.workflow_options?.topic}
            sourceSeconds={task.source_duration_seconds}
            canCreate={Boolean(task.source_url)}
            offered={workflowsInfo ? availableWorkflows(workflowsInfo) : null}
            readOnly={readOnly}
            pendingAction={pendingAction}
            onCreate={handleStartWorkflow}
            onResume={handleResume}
          />
        ) : task.status === "error" && clips.length === 0 ? (
          <EmptyState tone="error" icon={<AlertCircle className="size-7" />} title={t("failedTitle")} body={failedBody}>
            <Button asChild variant="outline"><Link href="/"><ArrowLeft className="size-4" />{t("backHome")}</Link></Button>
          </EmptyState>
        ) : clips.length === 0 ? (
          <EmptyState icon={<AlertCircle className="size-7" />} title={t("noClipsTitle")} body={t("noClipsBody")}>
            <Button asChild><Link href="/"><ArrowLeft className="size-4" />{t("tryAnother")}</Link></Button>
          </EmptyState>
        ) : isSeries ? (
          <SeriesView
            clips={sortedClips}
            plan={plan}
            taskId={task.id}
            status={task.status}
            editable={task.status === "completed" && !readOnly}
            locked={renderingClip}
            busyId={pendingAction}
            downloadingAll={isDownloadingAll}
            toolbar={formatSelect}
            onOpen={(clip) => setFocusIndex(sortedClips.findIndex((item) => item.id === clip.id))}
            onDownload={handleDownloadClip}
            onDownloadAll={() => void handleDownloadAll()}
            onResume={readOnly ? undefined : handleResume}
            resumeDisabled={pendingAction !== null}
            renderActions={publishButton}
          />
        ) : heroClip && SINGLE_OUTPUT.has(workflow) ? (
          <>
            <div className="mb-5 flex justify-end">{formatSelect}</div>
            <HeroClipView
              workflow={workflow as "recap" | "trailer"}
              clip={heroClip}
              plan={plan}
              taskId={task.id}
              editable={task.status === "completed" && !readOnly && !renderingClip(heroClip)}
              busy={pendingAction === heroClip.id || renderingClip(heroClip)}
              onDownload={() => handleDownloadClip(heroClip)}
              actions={publishButton(heroClip)}
            />
          </>
        ) : (
          <>
            {task.status === "error" && (
              <div role="status" className="mb-6 rounded-lg border border-destructive/20 bg-destructive/5 p-4">
                <p className="font-medium">{t("failedTitle")}</p>
                <p className="mt-1 text-sm text-muted-foreground">{failedBody}</p>
              </div>
            )}
                        {workflow === "topic" && topicName && (
              <div className="mb-5">
                <h2 className="text-base font-semibold">{t("topicHeader", { topic: topicName, count: clips.length })}</h2>
                {plan?.topic?.coverage === "thin" && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{t("workflowNotes.TOPIC_THIN", { count: plan.topic.found ?? clips.length })}</p>
                )}
              </div>
            )}
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              {sortable ? (
                <div className="inline-flex rounded-lg border bg-background p-0.5" role="tablist" aria-label={t("sortLabel")}>
                  {(["score", "timeline"] as const).map((value) => (
                    <button key={value} type="button" role="tab" aria-selected={sortBy === value} onClick={() => { sortChosen.current = true; setSortBy(value); }}
                      className={cn("h-7 rounded-md px-3 text-xs font-medium transition-colors", sortBy === value ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground")}>
                      {t(`sort.${value}`)}
                    </button>
                  ))}
                </div>
              ) : <span />}
              <div className="flex items-center gap-2">
                <DownloadAllButton label={t("downloadAll")} hint={t("downloadAllHint")} busy={isDownloadingAll} onClick={() => void handleDownloadAll()} />
                {formatSelect}
              </div>
            </div>
            {lockedPreview && preview && (
              <PreviewBanner preview={preview} onUnlock={askToUnlock} unlocking={previewUnlock.unlocking} />
            )}
            <div data-tour="task-clips" className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {sortedClips.map((clip, index) => lockedPreview && index === 0 ? <div key={clip.id} data-tour="preview-clip">{renderTile(clip, index)}</div> : renderTile(clip, index))}
              {lockedPreview && (preview?.locked ?? []).map((moment, index) => (
                <LockedMomentTile
                  key={moment.position}
                  moment={moment}
                  sourceUrl={task?.source_url}
                  backdropVideo={sortedClips[0] ? getClipUrl(sortedClips[0].video_url, sortedClips[0].filename) : null}
                  index={index}
                  onUnlock={askToUnlock}
                />
              ))}
            </div>
          </>
        )}
      </div>

      <ClipFocus
        clip={focusedClip}
        taskId={task?.id ?? String(params.id)}
        index={focusIndex ?? 0}
        total={sortedClips.length}
        busy={focusedClip ? pendingAction === focusedClip.id || focusedRendering : false}
        editable={task?.status === "completed" && !readOnly && !focusedRendering && !lockedPreview}
        onNavigate={(delta) => setFocusIndex((i) => (i === null ? i : (i + delta + sortedClips.length) % sortedClips.length))}
        onClose={() => setFocusIndex(null)}
        onDownload={() => focusedClip && handleDownloadClip(focusedClip)}
        onDelete={readOnly || focusedRendering ? undefined : () => { if (focusedClip) { setDeletingClipId(focusedClip.id); setFocusIndex(null); } }}
        hookTypeLabel={getHookTypeLabel}
        actions={focusedClip ? publishButton(focusedClip) : null}
      >
        {focusedClip && clipFxNotes(focusedClip.fx_meta).length > 0 && (
          <ul className="space-y-1 text-xs text-muted-foreground">
            {clipFxNotes(focusedClip.fx_meta).map((note) => <li key={note}>{t(`fx.${note}`)}</li>)}
          </ul>
        )}
        {focusedClip && (
          <ClipPostList
            posts={socialPosts.filter((post) => post.clip_id === focusedClip.id)}
            onChanged={loadSocialPosts}
          />
        )}
      </ClipFocus>

      <Sheet open={settingsSheetOpen} onOpenChange={setSettingsSheetOpen}>
        <SheetContent side="right" className="overflow-y-auto sm:max-w-md">
          <SheetHeader>
            <SheetTitle className="flex items-center gap-2"><Settings2 className="size-4" />{t("settingsTitle")}</SheetTitle>
            <SheetDescription>{t("settingsBody")}</SheetDescription>
          </SheetHeader>
            <div className="space-y-5 px-4">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">{t("font")}</label>
                <Select
                  value={projectFontFamily ?? FONT_TEMPLATE_DEFAULT_VALUE}
                  onValueChange={(value) =>
                    setProjectFontFamily(value === FONT_TEMPLATE_DEFAULT_VALUE ? null : value)
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t("templateDefault")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={FONT_TEMPLATE_DEFAULT_VALUE}>{t("templateDefault")}</SelectItem>
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
                  <label className="text-xs font-medium text-muted-foreground">{t("color")}</label>
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                    <input
                      type="checkbox"
                      checked={projectFontColor === null}
                      onChange={(e) => setProjectFontColor(e.target.checked ? null : "#FFFFFF")}
                      className="rounded"
                    />
                    {t("templateDefault")}
                  </label>
                </div>
                <div className="flex items-center gap-2">
                  <input
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
                    placeholder={t("templateDefault")}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">{t("captionTemplate")}</label>
                <Select value={projectCaptionTemplate} onValueChange={setProjectCaptionTemplate}>
                  <SelectTrigger>
                    <SelectValue>
                      {templateName(projectCaptionTemplate) || t("selectStyle")}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {sheetTemplates.map((template) => (
                      <SelectItem key={template.id} value={template.id}>
                        <div>
                          <div className="font-medium">{templateName(template.id) || template.name}</div>
                          <div className="text-xs text-muted-foreground">{templateDescription(template.id) || template.description}</div>
                        </div>
                      </SelectItem>
                    ))}
                    {availableTemplates.length === 0 && <SelectItem value="default">{t("defaultTemplate")}</SelectItem>}
                  </SelectContent>
                </Select>
              </div>

              {editsOffered && (
                <div className="space-y-3 rounded-lg border bg-muted p-3">
                  <div>
                    <label htmlFor="project-intensity" className="text-sm font-medium text-foreground">{t("fx.intensity")}</label>
                    <p className="text-xs text-muted-foreground">{t("fx.intensityHint")}</p>
                  </div>
                  <Select value={projectEditStyle.intensity} onValueChange={(value) => setProjectEditStyle((style) => ({ ...style, intensity: value as Intensity }))}>
                    <SelectTrigger id="project-intensity" className="w-full bg-background"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {sheetIntensities.map((level) => (
                        <SelectItem key={level} value={level}>
                          <div>
                            <div className="font-medium">{tHome(`edits.intensity.${level}.label`)}</div>
                            <div className="text-xs text-muted-foreground">{tHome(`edits.intensity.${level}.hint`)}</div>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {projectEditStyle.intensity !== "off" && (
                    <div className="divide-y divide-border/70">
                      {SHEET_FX_TOGGLES.map((key) => (
                        <div key={key} className="flex items-center justify-between gap-3 py-2">
                          <label htmlFor={`project-fx-${key}`} className="text-sm text-foreground/80">{tHome(`edits.options.${key}.label`)}</label>
                          <Switch
                            id={`project-fx-${key}`}
                            checked={projectEditStyle[key]}
                            onCheckedChange={(checked) => setProjectEditStyle((style) => ({ ...style, [key]: checked }))}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {brollAvailable && (
                <div className="rounded-lg border bg-muted p-3 space-y-2">
                  <label className="flex items-start gap-2 text-sm text-foreground/80">
                    <input
                      type="checkbox"
                      checked={projectIncludeBroll}
                      onChange={(e) => setProjectIncludeBroll(e.target.checked)}
                      className="mt-0.5 rounded"
                    />
                    <span>
                      <span className="block font-medium text-foreground">{t("brollTitle")}</span>
                      <span className="block text-xs text-muted-foreground">{t("brollBody")}</span>
                    </span>
                  </label>
                </div>
              )}

              <div className="rounded-lg border bg-muted p-3 space-y-3">
                <div>
                  <div className="text-sm font-medium text-foreground">{t("cleanupTitle")}</div>
                  <div className="text-xs text-muted-foreground">{t("cleanupBody")}</div>
                </div>

                <label className="flex items-center gap-2 text-sm text-foreground/80">
                  <input
                    type="checkbox"
                    checked={projectCutLongPauses}
                    onChange={(e) => setProjectCutLongPauses(e.target.checked)}
                    className="rounded"
                  />
                  {t("shortenPauses")}
                </label>

                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">{t("pauseThreshold")}</label>
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

                <label className="flex items-center gap-2 text-sm text-foreground/80">
                  <input
                    type="checkbox"
                    checked={projectRemoveFillerWords}
                    onChange={(e) => setProjectRemoveFillerWords(e.target.checked)}
                    className="rounded"
                  />
                  {t("removeFillers")}
                </label>

                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">{t("extraWords")}</label>
                  <Input
                    value={projectFilteredWords}
                    onChange={(e) => setProjectFilteredWords(e.target.value)}
                    placeholder={t("extraWordsPlaceholder")}
                  />
                </div>
              </div>
            </div>

            <SheetFooter>
              <Button
                className="w-full"
                onClick={handleApplyProjectSettings}
                disabled={isApplyingSettings || clipJobActive}
              >
                {isApplyingSettings ? t("applying") : t("apply")}
              </Button>
            </SheetFooter>
        </SheetContent>
      </Sheet>
      <AlertDialog open={fontToDelete !== null} onOpenChange={(open) => { if (!open) setFontToDelete(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>{t("deleteFontTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("deleteFontBody", { name: fontToDelete?.display_name ?? "" })}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>{t("keepFont")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => { if (fontToDelete) void handleDeleteFont(fontToDelete); setFontToDelete(null); }}>{t("deleteFont")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {/* Delete generation confirmation */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteGenerationTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteGenerationBody")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteTask} disabled={isDeleting} className="bg-red-600 hover:bg-red-700">
              {isDeleting ? t("deleting") : t("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Clip Confirmation Dialog */}
      <AlertDialog open={!!deletingClipId} onOpenChange={(open) => !open && setDeletingClipId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteClipTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteClipBody")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deletingClipId && handleDeleteClip(deletingClipId)}
              className="bg-red-600 hover:bg-red-700"
            >
              {t("delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <UpgradeDialog
        open={upgradeReason !== null}
        onOpenChange={(open) => { if (!open) setUpgradeReason(null); }}
        reason={upgradeReason ?? { kind: "limit" }}
        billing={billingSummary}
        source={upgradeReason?.kind === "preview" ? "preview" : "task_resume"}
        returnTo={upgradeReason?.kind === "preview" ? `/tasks/${taskId}` : undefined}
      />
    </main>
  );
}
